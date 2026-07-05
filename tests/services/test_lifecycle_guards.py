import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Adjudication, Approval, Evidence, WorkUnit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit, register_revision
from tests.services.test_dependencies import register_unit
from tests.services.test_package_registration import AUTHORITY
from tests.services.test_package_registration import NOW as APPROVED_AT

NOW = datetime(2026, 7, 5, tzinfo=UTC)


class FixedClock:
    def now(self, session: Session) -> datetime:
        del session
        return NOW


def completion_command(unit: WorkUnit) -> TransitionCommand:
    return TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.COMPLETED,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        expected_version=unit.version,
        idempotency_key=str(uuid.uuid4()),
    )


def submitted_unit(session: Session) -> WorkUnit:
    unit = register_unit(session, "completion")
    unit.state = WorkUnitState.SUBMITTED
    session.commit()
    return unit


def add_adjudication(
    session: Session,
    unit: WorkUnit,
    *,
    outcome: str = "passed",
    supersedes: Adjudication | None = None,
    expires_at: datetime | None = None,
    scope: str | None = None,
    adjudication_id: uuid.UUID | None = None,
    supersedes_id: uuid.UUID | None = None,
) -> Adjudication:
    failed_evidence_id = None
    if outcome == "waived":
        evidence = Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://failed",
            source_revision="abc123",
            recorded_by="worker-1",
            event_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
        )
        session.add(evidence)
        session.flush()
        failed_evidence_id = evidence.id
    adjudication = Adjudication(
        id=adjudication_id,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome=outcome,
        decided_by="human-1",
        rationale="verified",
        failed_evidence_id=failed_evidence_id,
        risk="accepted" if outcome == "waived" else None,
        follow_up="monitor" if outcome == "waived" else None,
        scope=scope,
        expires_at=expires_at,
        event_id=uuid.uuid4(),
        supersedes_adjudication_id=supersedes_id or (supersedes.id if supersedes else None),
    )
    session.add(adjudication)
    session.flush()
    return adjudication


def assert_completion_rejected(session: Session, unit: WorkUnit) -> None:
    with pytest.raises(DomainError) as error:
        transition_unit(session, completion_command(unit), clock=FixedClock())
    assert error.value.code == "completion_incomplete"


def test_completion_requires_adjudication_for_every_required_ac(
    migrated_session: Session,
) -> None:
    assert_completion_rejected(migrated_session, submitted_unit(migrated_session))


def test_completion_rejects_empty_acceptance_criteria(migrated_session: Session) -> None:
    revision = register_revision(
        migrated_session,
        package_id="empty-acs",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:empty",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=APPROVED_AT,
        approval_event_id=uuid.uuid4(),
        enforcement_snapshot={"acceptance_criteria": []},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="empty-acs",
        title="Empty ACs",
        outcome="complete",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=1,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


def test_completion_accepts_one_current_terminal_adjudication(
    migrated_session: Session,
) -> None:
    unit = submitted_unit(migrated_session)
    original = add_adjudication(migrated_session, unit, outcome="failed")
    add_adjudication(migrated_session, unit, supersedes=original)
    migrated_session.commit()

    result = transition_unit(migrated_session, completion_command(unit), clock=FixedClock())

    assert result.state is WorkUnitState.COMPLETED


def test_completion_rejects_duplicate_current_adjudications(
    migrated_session: Session,
) -> None:
    unit = submitted_unit(migrated_session)
    add_adjudication(migrated_session, unit)
    add_adjudication(migrated_session, unit)
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


def test_completion_rejects_cyclic_adjudication_chain(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    adjudication_id = uuid.uuid4()
    add_adjudication(
        migrated_session,
        unit,
        adjudication_id=adjudication_id,
        supersedes_id=adjudication_id,
    )
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


@pytest.mark.parametrize(
    ("expires_at", "scope"),
    [(NOW - timedelta(seconds=1), None), (NOW + timedelta(days=1), "subset")],
)
def test_completion_rejects_invalid_waiver(
    migrated_session: Session,
    expires_at: datetime,
    scope: str | None,
) -> None:
    unit = submitted_unit(migrated_session)
    add_adjudication(
        migrated_session,
        unit,
        outcome="waived",
        expires_at=expires_at,
        scope=scope,
    )
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


@pytest.mark.parametrize(
    ("subject_type", "binding", "allowed"),
    [("action", "1", True), ("action", "0", False), ("work_unit", "1", False)],
)
def test_approval_must_bind_current_unit_version(
    migrated_session: Session,
    subject_type: str,
    binding: str,
    allowed: bool,
) -> None:
    unit = register_unit(migrated_session, "approval")
    unit.state = WorkUnitState.AWAITING_APPROVAL
    approval = Approval(
        subject_type=subject_type,
        subject_id=unit.id,
        subject_revision_or_fingerprint=binding,
        decision="approved",
        approved_by="human-1",
        reason="approved",
        event_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    migrated_session.add(approval)
    migrated_session.commit()
    command = TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.READY,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        expected_version=unit.version,
        idempotency_key=str(uuid.uuid4()),
    )

    if allowed:
        assert transition_unit(migrated_session, command).state is WorkUnitState.READY
    else:
        with pytest.raises(DomainError) as error:
            transition_unit(migrated_session, command)
        assert error.value.code == "approval_required"
