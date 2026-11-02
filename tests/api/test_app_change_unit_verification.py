"""The verifier lane over an app-change unit, through the real routes and the real evaluator.

Only GitHub is substituted, as in `test_bump_unit_verification.py`, so these prove what the
orchestrator does with the rows the lane files.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposalAcMapping,
    Evidence,
    PackageAcceptanceCriterion,
    SdsMember,
    SdsMemberGrant,
    WorkUnit,
)
from rotation_verifier.alerts import REFUSED
from rotation_verifier.cli import run_pass
from rotation_verifier.client import VerifierClient
from rotation_verifier.required_checks import NoRequiredChecks
from tests.api.test_bump_unit_verification import GitHubByCheck, ThroughTestClient
from tests.api.test_verifier_api import substitute_github
from tests.fixtures.named_check import (
    AUTOMATED_CHECK_AUTHORITY,
    HEAD_SHA,
    PR_NUMBER,
    TARGET_REPOSITORY,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    record_worker_evidence,
)

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}


def _map_criterion(session: Session, unit: WorkUnit, ac_id: str, evidence_type: str) -> None:
    criterion = PackageAcceptanceCriterion(
        work_package_revision_id=unit.work_package_revision_id,
        ac_id=ac_id,
        condition=f"{ac_id} passes",
        evidence_type=evidence_type,
        evidence="recorded evidence",
        approver="verifier",
    )
    session.add(criterion)
    session.flush()
    approved = session.scalar(
        select(ApprovedDecomposition).where(
            ApprovedDecomposition.work_package_revision_id == unit.work_package_revision_id
        )
    )
    assert approved is not None
    session.add(
        DecompositionProposalAcMapping(
            proposal_id=approved.proposal_id,
            package_acceptance_criterion_id=criterion.id,
            unit_key=unit.unit_key,
        )
    )
    session.commit()


def _submitted(engine: Engine, key: str, first: str, *others: tuple[str, str]) -> uuid.UUID:
    with Session(engine) as session:
        unit = mapped_submitted_unit(
            session,
            key=key,
            evidence_type=first,
            ac_id="AC-001",
            authority=AUTOMATED_CHECK_AUTHORITY,
            other_ac_ids=tuple(ac_id for ac_id, _ in others),
        )
        for ac_id, evidence_type in others:
            _map_criterion(session, unit, ac_id, evidence_type)
        record_worker_evidence(
            session,
            unit,
            ac_id="AC-001",
            evidence_type="runner.pr.opened",
            payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
            idempotency_key=f"{key}-worker-evidence",
        )
        bind_dispatched_pull_request(session, unit)
        return unit.id


def _lane(
    db_client: TestClient,
    conclusions: dict[str, str],
    *required: str,
    read: Callable[[str], tuple[str, ...]] | None = None,
):
    github = GitHubByCheck(conclusions)
    substitute_github(db_client, github)  # type: ignore[arg-type]
    client = VerifierClient(
        base_url="https://sds.example",
        credential_key_id="verifier-key",
        token="verifier-token",
        transport=ThroughTestClient(db_client),
    )
    return github, run_pass(client, read_required_checks=read or (lambda repository: required))


def _filed(engine: Engine, unit_id: uuid.UUID) -> list[tuple[str, str]]:
    with Session(engine) as session:
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.work_unit_id == unit_id,
                Evidence.evidence_type == "verifier.github.named_check",
            )
            .order_by(Evidence.recorded_at)
        ).all()
        return [(row.ac_id, (row.payload or {})["check_name"]) for row in rows]


def test_the_unit_read_names_only_the_automated_check_criteria(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(
        migrated_engine,
        "served-ids",
        "automated_check",
        ("AC-002", "automated_test"),
        ("AC-003", "automated_check"),
        ("AC-004", "human_review"),
    )

    response = db_client.get(f"/api/v1/work-units/{unit_id}", headers=OBSERVER)

    assert response.status_code == 200, response.text
    assert response.json()["automated_check_ac_ids"] == ["AC-001", "AC-003"]


def test_two_criteria_each_get_every_check_and_the_unit_completes(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(
        migrated_engine, "two-criteria", "automated_check", ("AC-002", "automated_check")
    )

    _, outcomes = _lane(db_client, {"Lint": "success", "test": "success"}, "test", "Lint")

    assert [o.result for o in outcomes] == ["completed"]
    assert _filed(migrated_engine, unit_id) == [
        ("AC-001", "Lint"),
        ("AC-001", "test"),
        ("AC-002", "Lint"),
        ("AC-002", "test"),
    ]


def test_a_failed_check_fails_the_unit_on_every_criterion(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(
        migrated_engine, "two-fail", "automated_check", ("AC-002", "automated_check")
    )

    _, outcomes = _lane(db_client, {"Lint": "success", "test": "failure"}, "test", "Lint")

    assert [o.result for o in outcomes] == ["revision_required"]
    assert _filed(migrated_engine, unit_id) == [
        ("AC-001", "Lint"),
        ("AC-001", "test"),
        ("AC-002", "Lint"),
        ("AC-002", "test"),
    ]


def _unprotected(repository: str) -> tuple[str, ...]:
    raise NoRequiredChecks("GitHub answered 404")


@pytest.mark.parametrize(
    "lost", [lambda repository: (), _unprotected], ids=["empty-contexts", "a-404"]
)
def test_a_filed_check_then_a_lost_required_list_holds_the_unit(
    db_client: TestClient,
    migrated_engine: Engine,
    lost: Callable[[str], tuple[str, ...]],
) -> None:
    """Pass one files Lint's success and waits on test. If the list then comes back empty, Lint's
    row would pass the criterion on `/verify` with test never observed."""
    unit_id = _submitted(migrated_engine, "lost-list", "automated_check")
    _, first = _lane(db_client, {"Lint": "success"}, "Lint", "test")
    assert [o.result for o in first] == ["waiting"]

    _, outcomes = _lane(db_client, {"Lint": "success"}, read=lost)

    assert [o.result for o in outcomes] == [REFUSED]
    assert "required checks" in outcomes[0].detail
    assert _filed(migrated_engine, unit_id) == [("AC-001", "Lint")]
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None and unit.state == "submitted"


def test_a_repository_with_no_required_check_reaches_a_person(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(migrated_engine, "no-required", "automated_check")

    _, outcomes = _lane(db_client, {})

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert _filed(migrated_engine, unit_id) == []


def test_an_automated_test_with_no_evaluator_goes_to_a_person_unfiled(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(migrated_engine, "test-only", "automated_test")

    github, outcomes = _lane(db_client, {"Lint": "success"}, "Lint")

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert github.asked == []
    assert _filed(migrated_engine, unit_id) == []


def test_a_human_criterion_beside_a_green_check_goes_to_a_person(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(
        migrated_engine, "with-human", "automated_check", ("AC-002", "human_review")
    )

    _, outcomes = _lane(db_client, {"Lint": "success"}, "Lint")

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert _filed(migrated_engine, unit_id) == [("AC-001", "Lint")]


def test_a_withdrawn_grant_on_the_real_record_holds_the_unit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted(migrated_engine, "withdrawn", "automated_check")
    with Session(migrated_engine) as session:
        grant = session.scalar(
            select(SdsMemberGrant)
            .join(SdsMember, SdsMember.id == SdsMemberGrant.member_id)
            .where(
                SdsMember.repository == TARGET_REPOSITORY, SdsMemberGrant.service == "app_changes"
            )
        )
        assert grant is not None
        grant.state = "withdrawn"
        session.commit()

    _, outcomes = _lane(db_client, {"Lint": "success"}, "Lint")

    assert [o.result for o in outcomes] == ["refused"]
    assert _filed(migrated_engine, unit_id) == []
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None and unit.state == "submitted"
