import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event
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
