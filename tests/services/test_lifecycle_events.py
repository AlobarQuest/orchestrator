import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event
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

    class FixedClock:
        def now(self, session: Session) -> datetime:
            del session
            return occurred_at

    result = transition_unit(migrated_session, command_for(ready_unit), clock=FixedClock())
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
