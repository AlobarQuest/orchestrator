import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    Approval,
    Dependency,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from tests.api.conftest import auth_config, db_client
from tests.persistence.conftest import migrated_engine
from tests.services.test_dependencies import register_unit

__all__ = ["auth_config", "db_client", "migrated_engine"]


def _review_unit(
    migrated_engine: Engine, *, unit_key: str, acceptance_criteria: tuple[str, ...] = ("ac-1",)
) -> WorkUnit:
    with Session(migrated_engine) as session:
        unit = register_unit(session, unit_key, acceptance_criteria=acceptance_criteria)
        unit.state = WorkUnitState.AWAITING_REVIEW
        approval = Approval(
            subject_type="authority",
            subject_id=unit.id,
            subject_revision_or_fingerprint=unit.authority_fingerprint,
            decision="approved",
            approved_by="devon",
            reason="approved authority",
            event_id=uuid.uuid4(),
            idempotency_key=f"authority-{unit.id}",
        )
        session.add(approval)
        session.add(
            Dependency(
                work_unit_id=unit.id,
                kind="external_system",
                required_state_or_condition="passed",
                external_ref="ci/review",
                status="pending",
            )
        )
        session.flush()
        unit.authority_approval_id = approval.id
        session.commit()
        session.refresh(unit)
        session.expunge(unit)
        return unit


@pytest.fixture
def review_unit(migrated_engine: Engine) -> WorkUnit:
    return _review_unit(migrated_engine, unit_key="review-unit")


def criterion_condition(ac_id: str) -> str:
    """The standard one criterion states -- distinct per criterion, on purpose.

    A fieldset-scoped test can only state the negative that makes it discriminate ("this block does
    not carry another criterion's standard") if the two texts differ. The placeholder these fixtures
    used before was `"c"` for every criterion, under which a template rendering every condition into
    every fieldset would satisfy the assertions.
    """
    return f"What {ac_id} requires of the change must be true."


def criterion_expectation(ac_id: str) -> str:
    """The evidence one criterion's author asked for. Distinct for the same reason."""
    return f"The artifact recorded for {ac_id} showing it."


def _review_unit_with_criteria(
    migrated_engine: Engine, *, unit_key: str, criteria: tuple[tuple[str, str], ...]
) -> WorkUnit:
    unit = _review_unit(
        migrated_engine,
        unit_key=unit_key,
        acceptance_criteria=tuple(ac_id for ac_id, _evidence_type in criteria),
    )
    with Session(migrated_engine) as session:
        for ac_id, evidence_type in criteria:
            session.add(
                PackageAcceptanceCriterion(
                    work_package_revision_id=unit.work_package_revision_id,
                    ac_id=ac_id,
                    condition=criterion_condition(ac_id),
                    evidence_type=evidence_type,
                    evidence=criterion_expectation(ac_id),
                    approver="human-1",
                )
            )
        session.commit()
    return unit


def _review_unit_with_ac(migrated_engine: Engine, *, unit_key: str, evidence_type: str) -> WorkUnit:
    return _review_unit_with_criteria(
        migrated_engine, unit_key=unit_key, criteria=(("ac-1", evidence_type),)
    )


@pytest.fixture
def review_unit_with_judgment_ac(migrated_engine: Engine) -> WorkUnit:
    return _review_unit_with_ac(
        migrated_engine, unit_key="review-unit-judgment-ac", evidence_type="human.review"
    )


@pytest.fixture
def review_unit_with_test_ac(migrated_engine: Engine) -> WorkUnit:
    return _review_unit_with_ac(
        migrated_engine, unit_key="review-unit-test-ac", evidence_type="test"
    )


@pytest.fixture
def review_unit_with_two_judgment_acs(migrated_engine: Engine) -> WorkUnit:
    return _review_unit_with_criteria(
        migrated_engine,
        unit_key="review-unit-two-acs",
        criteria=(("ac-1", "human.review"), ("ac-2", "human.review")),
    )


@pytest.fixture
def review_unit_with_post_deploy_ac(migrated_engine: Engine) -> WorkUnit:
    # A package that DECLARES one of the generated post-deploy ids. The form must exclude it on the
    # id alone (`_adjudicatable_criteria` filters `POST_DEPLOY_AC_IDS`), without needing the
    # deployment observation that makes a unit genuinely generated.
    return _review_unit_with_criteria(
        migrated_engine,
        unit_key="review-unit-post-deploy-ac",
        criteria=(("ac-1", "human.review"), ("post-deploy-health", "production.health")),
    )
