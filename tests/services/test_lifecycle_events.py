import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit


def command_for(unit, *, idempotency_key: str = "claim-1") -> TransitionCommand:
    return TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.CLAIMED,
        actor=ActorContext("worker-1", ActorRole.SYSTEM),
        expected_version=unit.version,
        idempotency_key=idempotency_key,
    )


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self, session: Session) -> datetime:
        del session
        return self.value


def worker_command(
    unit,
    grant: LeaseGrant,
    target: WorkUnitState,
    *,
    idempotency_key: str,
) -> TransitionCommand:
    return TransitionCommand(
        unit_id=unit.id,
        target=target,
        actor=ActorContext("worker-1", ActorRole.WORKER),
        expected_version=unit.version,
        idempotency_key=idempotency_key,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )


def test_transition_appends_attributable_event(migrated_session: Session, ready_unit) -> None:
    result = transition_unit(migrated_session, command_for(ready_unit))
    event = migrated_session.get(Event, result.event_id)

    assert event is not None
    assert (event.actor_id, event.from_state, event.to_state) == (
        "worker-1",
        "ready",
        "claimed",
    )
    assert event.payload["registry_version"] == 1


def test_transition_uses_injected_clock(migrated_session: Session, ready_unit) -> None:
    occurred_at = datetime(2025, 1, 2, tzinfo=UTC)

    result = transition_unit(
        migrated_session,
        command_for(ready_unit),
        clock=FixedClock(occurred_at),
    )
    persisted_event = migrated_session.get(Event, result.event_id)

    assert persisted_event is not None
    assert persisted_event.occurred_at == occurred_at


def test_idempotent_retry_returns_original_result(migrated_session: Session, ready_unit) -> None:
    command = command_for(ready_unit)
    first = transition_unit(migrated_session, command)
    second = transition_unit(migrated_session, command)

    assert second == first
    assert migrated_session.query(Event).filter_by(idempotency_key="claim-1").count() == 1


def test_reused_idempotency_key_for_different_transition_is_rejected(
    migrated_session: Session, ready_unit
) -> None:
    transition_unit(migrated_session, command_for(ready_unit))
    conflicting = TransitionCommand(
        unit_id=ready_unit.id,
        target=WorkUnitState.EXECUTING,
        actor=ActorContext("worker-1", ActorRole.WORKER),
        expected_version=2,
        idempotency_key="claim-1",
    )

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, conflicting)

    assert error.value.code == "idempotency_conflict"


def test_non_transition_event_idempotency_collision_is_rejected(
    migrated_session: Session, ready_unit
) -> None:
    migrated_session.add(
        Event(
            actor_id="worker-1",
            action="evidence.recorded",
            subject_type="work_unit",
            subject_id=ready_unit.id,
            from_state=None,
            to_state=None,
            payload={},
            correlation_id=uuid.uuid4(),
            idempotency_key="claim-1",
        )
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, command_for(ready_unit))

    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    ("expected_version_delta", "role"),
    [(1, ActorRole.SYSTEM), (0, ActorRole.WORKER)],
)
def test_idempotent_retry_requires_exact_command(
    migrated_session: Session,
    ready_unit,
    expected_version_delta: int,
    role: ActorRole,
) -> None:
    command = command_for(ready_unit)
    transition_unit(migrated_session, command)
    changed = TransitionCommand(
        unit_id=command.unit_id,
        target=command.target,
        actor=ActorContext(command.actor.actor_id, role),
        expected_version=command.expected_version + expected_version_delta,
        idempotency_key=command.idempotency_key,
    )

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, changed)

    assert error.value.code == "idempotency_conflict"


def test_stale_expected_version_is_rejected(migrated_session: Session, ready_unit) -> None:
    command = command_for(ready_unit, idempotency_key=str(uuid.uuid4()))
    command = TransitionCommand(
        unit_id=command.unit_id,
        target=command.target,
        actor=command.actor,
        expected_version=command.expected_version + 1,
        idempotency_key=command.idempotency_key,
    )

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, command)

    assert error.value.code == "version_conflict"


def test_forbidden_worker_transition_is_rejected_before_claim_proof(
    migrated_session: Session, ready_unit
) -> None:
    ready_unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                ready_unit.id,
                WorkUnitState.COMPLETED,
                ActorContext("worker-1", ActorRole.WORKER),
                ready_unit.version,
                "worker-complete",
            ),
        )

    assert error.value.code == "role_forbidden"


@pytest.mark.parametrize(
    ("actor_id", "attempt_delta", "expire"),
    [
        ("worker-2", 0, False),
        ("worker-1", 1, False),
        ("worker-1", 0, True),
    ],
)
def test_worker_transition_rejects_invalid_claim_proof(
    migrated_session: Session,
    ready_unit,
    actor_id: str,
    attempt_delta: int,
    expire: bool,
) -> None:
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        "claim-worker",
    )
    assert isinstance(grant, LeaseGrant)
    if expire:
        migrated_session.execute(
            text(
                "UPDATE claims SET lease_expires_at = "
                "transaction_timestamp() - interval '1 second' WHERE id = :claim_id"
            ),
            {"claim_id": grant.claim_id},
        )
        migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                ready_unit.id,
                WorkUnitState.EXECUTING,
                ActorContext(actor_id, ActorRole.WORKER),
                2,
                f"invalid-proof-{actor_id}-{attempt_delta}-{expire}",
                grant.attempt + attempt_delta,
                grant.lease_token,
            ),
        )

    assert error.value.code == "active_claim_required"


@pytest.mark.parametrize("source", [WorkUnitState.CLAIMED, WorkUnitState.EXECUTING])
def test_worker_failure_releases_claim_at_transition_time_and_replays_stably(
    migrated_session: Session,
    ready_unit,
    source: WorkUnitState,
) -> None:
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        f"claim-worker-failure-{source}",
    )
    assert isinstance(grant, LeaseGrant)
    if source is WorkUnitState.EXECUTING:
        transition_unit(
            migrated_session,
            worker_command(
                ready_unit,
                grant,
                WorkUnitState.EXECUTING,
                idempotency_key="start-before-failure",
            ),
        )
    active_claim = migrated_session.get(Claim, grant.claim_id)
    assert active_claim is not None
    occurred_at = active_claim.acquired_at + timedelta(seconds=1)
    command = worker_command(
        ready_unit,
        grant,
        WorkUnitState.FAILED,
        idempotency_key=f"worker-failure-{source}",
    )

    first = transition_unit(migrated_session, command, clock=FixedClock(occurred_at))
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    original_release = (claim.released_at, claim.terminal_reason)
    replay = transition_unit(
        migrated_session,
        command,
        clock=FixedClock(occurred_at + timedelta(days=1)),
    )

    migrated_session.expire_all()
    persisted_claim = migrated_session.get(Claim, grant.claim_id)
    assert replay == first
    assert persisted_claim is not None
    assert original_release == (occurred_at, "work_unit_failed")
    assert (persisted_claim.released_at, persisted_claim.terminal_reason) == original_release


@pytest.mark.parametrize(
    "source",
    [
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
    ],
)
def test_human_cancellation_releases_claim_at_transition_time_and_replays_stably(
    migrated_session: Session,
    ready_unit,
    source: WorkUnitState,
) -> None:
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        f"claim-human-cancellation-{source}",
    )
    assert isinstance(grant, LeaseGrant)
    if source is not WorkUnitState.CLAIMED:
        transition_unit(
            migrated_session,
            worker_command(
                ready_unit,
                grant,
                source,
                idempotency_key=f"prepare-human-cancellation-{source}",
            ),
        )
    occurred_at = datetime(2030, 3, 4, 5, 6, tzinfo=UTC)
    command = TransitionCommand(
        unit_id=ready_unit.id,
        target=WorkUnitState.CANCELLED,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        expected_version=ready_unit.version,
        idempotency_key=f"human-cancellation-{source}",
        reason="operator cancelled work",
    )

    first = transition_unit(migrated_session, command, clock=FixedClock(occurred_at))
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    original_release = (claim.released_at, claim.terminal_reason)
    replay = transition_unit(
        migrated_session,
        command,
        clock=FixedClock(datetime(2031, 3, 4, 5, 6, tzinfo=UTC)),
    )

    migrated_session.expire_all()
    persisted_claim = migrated_session.get(Claim, grant.claim_id)
    assert replay == first
    assert persisted_claim is not None
    assert original_release == (occurred_at, "work_unit_cancelled")
    assert (persisted_claim.released_at, persisted_claim.terminal_reason) == original_release


def test_human_cancellation_without_claim_succeeds_without_creating_one(
    migrated_session: Session,
    ready_unit,
) -> None:
    ready_unit.state = WorkUnitState.AWAITING_APPROVAL
    migrated_session.commit()

    result = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=ready_unit.id,
            target=WorkUnitState.CANCELLED,
            actor=ActorContext("human-1", ActorRole.HUMAN),
            expected_version=ready_unit.version,
            idempotency_key="human-cancellation-without-claim",
            reason="operator cancelled unclaimed work",
        ),
    )

    assert result.state is WorkUnitState.CANCELLED
    assert migrated_session.query(Claim).filter_by(work_unit_id=ready_unit.id).count() == 0
