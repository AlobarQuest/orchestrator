"""SDS 1.1: a waiver accepts the criterion's current failure, as the verifier really records it.

The verifier records a failure with no `evidence_id`; it cites its own finding, which becomes the
criterion's evidence head, in `failed_evidence_id`. These tests drive `verify_work_unit` to that
row rather than writing one by hand.
"""

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Adjudication, Evidence, WorkUnit
from orchestrator.services.verifier.evidence import record_adjudication
from orchestrator.services.verifier.verifier import VerifyCommand, verify_work_unit
from tests.fixtures.named_check import (
    HUMAN,
    NOW,
    VERIFIER,
    mapped_submitted_unit,
    record_worker_evidence,
)


def _failed_by_the_verifier(session: Session, key: str) -> tuple[WorkUnit, uuid.UUID]:
    unit = mapped_submitted_unit(session, key=key)
    record_worker_evidence(session, unit, payload={"exit_code": 1})
    result = verify_work_unit(
        session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key=f"{key}-verify",
        ),
    )
    (evaluation,) = result.evaluations
    assert evaluation.outcome == "failed"
    assert evaluation.finding_evidence_id is not None
    return unit, evaluation.finding_evidence_id


def _waive(session: Session, unit: WorkUnit, failed_evidence_id: uuid.UUID, key: str):
    return record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="waived",
        actor=HUMAN,
        failed_evidence_id=failed_evidence_id,
        rationale="accepted for this release",
        risk="medium",
        follow_up="repair in the next release",
        expires_at=NOW + timedelta(days=3650),
        idempotency_key=key,
    )


def test_a_verifier_failure_can_be_waived_on_its_finding(migrated_session: Session) -> None:
    unit, finding_id = _failed_by_the_verifier(migrated_session, "waive-finding")

    result = _waive(migrated_session, unit, finding_id, "waive-finding-waiver")

    assert isinstance(result, Adjudication)
    assert result.failed_evidence_id == finding_id


def test_newer_evidence_reopens_the_failure_and_refuses_a_waiver(
    migrated_session: Session,
) -> None:
    unit, finding_id = _failed_by_the_verifier(migrated_session, "waive-reopened")
    # A later attempt's evidence, superseding the finding. Written directly: the worker route
    # needs a whole new attempt, and only the evidence head matters here.
    newer = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=2,
        evidence_type="pytest",
        stable_ref="artifact://waive-reopened-newer",
        payload={"exit_code": 0},
        source_revision="def456",
        recorded_by="worker-1",
        event_id=uuid.uuid4(),
        idempotency_key="waive-reopened-newer",
        supersedes_evidence_id=finding_id,
    )
    migrated_session.add(newer)
    migrated_session.commit()

    stale = _waive(migrated_session, unit, finding_id, "waive-reopened-stale")
    unjudged = _waive(migrated_session, unit, newer.id, "waive-reopened-unjudged")

    assert isinstance(stale, DomainError) and stale.code == "waiver_invalid"
    assert isinstance(unjudged, DomainError) and unjudged.code == "waiver_invalid"


def test_a_waiver_naming_superseded_evidence_is_refused(migrated_session: Session) -> None:
    """The finding supersedes the worker's evidence. Waiving that older row names no current one."""
    unit, finding_id = _failed_by_the_verifier(migrated_session, "waive-superseded")
    finding = migrated_session.get(Evidence, finding_id)
    assert finding is not None and finding.supersedes_evidence_id is not None

    result = _waive(
        migrated_session, unit, finding.supersedes_evidence_id, "waive-superseded-waiver"
    )

    assert isinstance(result, DomainError) and result.code == "waiver_invalid"
