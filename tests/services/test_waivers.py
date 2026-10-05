import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Adjudication, Evidence
from orchestrator.services.lifecycle.lifecycle import TransitionCommand, transition_unit
from orchestrator.services.verifier.evidence import record_adjudication
from tests._support.seeding import record_failure

NOW = datetime(2027, 7, 5, tzinfo=UTC)


class FixedClock:
    def now(self, session: Session) -> datetime:
        del session
        return NOW + timedelta(days=2)


def failed_evidence(session: Session, unit) -> Evidence:
    row = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://failed",
        source_revision="abc123",
        recorded_by="worker-1",
        event_id=uuid.uuid4(),
        idempotency_key="failed-evidence",
    )
    session.add(row)
    session.commit()
    record_failure(session, row)
    return row


def waiver_command(unit, evidence: Evidence) -> dict[str, Any]:
    return {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "ac_id": "ac-1",
        "outcome": "waived",
        "actor": ActorContext("human-1", ActorRole.HUMAN),
        "failed_evidence_id": evidence.id,
        "rationale": "accepted for this release",
        "risk": "medium",
        "follow_up": "repair in next release",
        "expires_at": NOW + timedelta(days=1),
        "idempotency_key": "waiver-1",
    }


def record(session: Session, command: dict[str, Any]) -> Adjudication | DomainError:
    return record_adjudication(session, **cast(Any, command))


def test_only_human_may_record_waiver(migrated_session: Session, ready_unit) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command["actor"] = ActorContext("verifier-1", ActorRole.VERIFIER)

    result = record(migrated_session, command)

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("failed_evidence_id", None),
        ("rationale", " "),
        ("risk", None),
        ("follow_up", ""),
    ],
)
def test_waiver_requires_failed_evidence_rationale_risk_and_follow_up(
    migrated_session: Session, ready_unit, field: str, value: object
) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command[field] = value

    result = record(migrated_session, command)

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


def test_human_records_whole_ac_waiver_without_scope_or_expiry(
    migrated_session: Session, ready_unit
) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command["expires_at"] = None

    result = record(migrated_session, command)

    assert isinstance(result, Adjudication)
    assert result.outcome == "waived"
    assert result.scope is None
    assert result.expires_at is None


def test_human_records_waiver_with_future_expiry(migrated_session: Session, ready_unit) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)

    result = record(migrated_session, waiver_command(ready_unit, evidence))

    assert isinstance(result, Adjudication)
    assert result.expires_at == NOW + timedelta(days=1)


def test_expired_waiver_is_rejected_at_creation(migrated_session: Session, ready_unit) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command["expires_at"] = datetime(2020, 1, 1, tzinfo=UTC)

    result = record(migrated_session, command)

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


def test_waiver_risk_class_must_be_in_vocabulary(migrated_session: Session, ready_unit) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command["risk"] = "catastrophic"
    command["idempotency_key"] = "waiver-bad-risk"

    result = record(migrated_session, command)

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


@pytest.mark.parametrize(
    ("scope", "expires_at"),
    [
        (None, NOW + timedelta(days=1)),
        ("linux-only", NOW + timedelta(days=1)),
    ],
)
def test_expired_or_narrow_waiver_does_not_satisfy_completion(
    migrated_session: Session,
    ready_unit,
    scope: str | None,
    expires_at: datetime,
) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    command = waiver_command(ready_unit, evidence)
    command.update(scope=scope, expires_at=expires_at)
    result = record(migrated_session, command)
    assert isinstance(result, Adjudication)
    ready_unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=ready_unit.id,
                target=WorkUnitState.COMPLETED,
                actor=ActorContext("human-1", ActorRole.HUMAN),
                expected_version=ready_unit.version,
                idempotency_key="complete-1",
            ),
            clock=FixedClock(),
        )

    assert error.value.code == "completion_incomplete"


def _unfailed_evidence(
    session: Session, unit, key: str, *, supersedes: Evidence | None = None
) -> Evidence:
    row = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref=f"artifact://{key}",
        source_revision="abc123",
        recorded_by="worker-1",
        event_id=uuid.uuid4(),
        idempotency_key=key,
        supersedes_evidence_id=supersedes.id if supersedes is not None else None,
    )
    session.add(row)
    session.commit()
    return row


def test_a_criterion_nobody_has_failed_cannot_be_waived(
    migrated_session: Session, ready_unit
) -> None:
    evidence = _unfailed_evidence(migrated_session, ready_unit, "undecided-evidence")

    result = record(migrated_session, waiver_command(ready_unit, evidence))

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


def test_a_passed_criterion_cannot_be_waived(migrated_session: Session, ready_unit) -> None:
    evidence = _unfailed_evidence(migrated_session, ready_unit, "passed-evidence")
    migrated_session.add(
        Adjudication(
            work_package_revision_id=ready_unit.work_package_revision_id,
            work_unit_id=ready_unit.id,
            ac_id="ac-1",
            outcome="passed",
            evidence_id=evidence.id,
            decided_by="verifier",
            rationale="the evidence passed",
            event_id=uuid.uuid4(),
        )
    )
    migrated_session.commit()

    result = record(migrated_session, waiver_command(ready_unit, evidence))

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


def test_a_waiver_must_name_the_evidence_the_criterion_failed_on(
    migrated_session: Session, ready_unit
) -> None:
    failed = failed_evidence(migrated_session, ready_unit)
    other = _unfailed_evidence(migrated_session, ready_unit, "other-evidence", supersedes=failed)

    result = record(migrated_session, waiver_command(ready_unit, other))

    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"


def test_a_waiver_can_be_renewed_on_the_same_failure(migrated_session: Session, ready_unit) -> None:
    evidence = failed_evidence(migrated_session, ready_unit)
    first = record(migrated_session, waiver_command(ready_unit, evidence))
    assert isinstance(first, Adjudication)

    renewal = waiver_command(ready_unit, evidence)
    renewal["idempotency_key"] = "waiver-renewal"
    result = record(migrated_session, renewal)

    assert isinstance(result, Adjudication)
    assert result.supersedes_adjudication_id == first.id
