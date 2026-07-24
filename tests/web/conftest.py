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


def _review_unit(migrated_engine: Engine, *, unit_key: str) -> WorkUnit:
    with Session(migrated_engine) as session:
        unit = register_unit(session, unit_key)
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


def _review_unit_with_ac(migrated_engine: Engine, *, unit_key: str, evidence_type: str) -> WorkUnit:
    unit = _review_unit(migrated_engine, unit_key=unit_key)
    with Session(migrated_engine) as session:
        session.add(
            PackageAcceptanceCriterion(
                work_package_revision_id=unit.work_package_revision_id,
                ac_id="ac-1",
                condition="c",
                evidence_type=evidence_type,
                evidence="e",
                approver="human-1",
            )
        )
        session.commit()
    return unit


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
