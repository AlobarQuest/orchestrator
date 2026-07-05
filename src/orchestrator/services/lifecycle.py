import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import Clock, TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Event,
    WorkPackageRevision,
    WorkUnit,
)


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    role: ActorRole


@dataclass(frozen=True)
class TransitionCommand:
    unit_id: uuid.UUID
    target: WorkUnitState
    actor: ActorContext
    expected_version: int
    idempotency_key: str


@dataclass(frozen=True)
class TransitionResult:
    unit_id: uuid.UUID
    state: WorkUnitState
    version: int
    event_id: uuid.UUID


def transition_unit(
    session: Session,
    command: TransitionCommand,
    *,
    clock: Clock | None = None,
) -> TransitionResult:
    try:
        result = _perform_transition(session, command, clock or TransactionClock())
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _perform_transition(
    session: Session, command: TransitionCommand, clock: Clock
) -> TransitionResult:
    unit = session.execute(
        select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update()
    ).scalar_one_or_none()
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)

    existing = session.execute(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _idempotent_result(existing, command)
    if unit.version != command.expected_version:
        raise DomainError("version_conflict", "work unit version has changed", "reload")

    source = WorkUnitState(unit.state)
    authorize_transition(
        source,
        command.target,
        command.actor.role,
        _transition_guards(session, unit),
    )
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    next_version = unit.version + 1
    unit.state = command.target
    unit.version = next_version
    event = _transition_event(command, unit, source, revision.registry_version, clock.now(session))
    session.add(event)
    session.flush()
    return TransitionResult(unit.id, command.target, next_version, event.id)


def _transition_event(
    command: TransitionCommand,
    unit: WorkUnit,
    source: WorkUnitState,
    registry_version: int,
    occurred_at: datetime,
) -> Event:
    return Event(
        occurred_at=occurred_at,
        actor_id=command.actor.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=source,
        to_state=command.target,
        payload={
            "actor_role": command.actor.role,
            "registry_version": registry_version,
            "version": unit.version,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=command.idempotency_key,
    )


def _idempotent_result(event: Event, command: TransitionCommand) -> TransitionResult:
    expected = (
        event.subject_type == "work_unit"
        and event.subject_id == command.unit_id
        and event.to_state == command.target
        and event.actor_id == command.actor.actor_id
    )
    if not expected:
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    version = event.payload.get("version")
    if not isinstance(version, int):
        raise DomainError("event_invalid", "transition event has no valid version", None)
    return TransitionResult(command.unit_id, command.target, version, event.id)


def _transition_guards(session: Session, unit: WorkUnit) -> TransitionGuards:
    approval_recorded = (
        session.execute(
            select(Approval.id)
            .where(
                Approval.subject_type == "work_unit",
                Approval.subject_id == unit.id,
                Approval.decision == "approved",
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    outcomes = session.execute(
        select(Adjudication.outcome).where(Adjudication.work_unit_id == unit.id)
    ).scalars()
    adjudication_outcomes = tuple(outcomes)
    completion_satisfied = bool(adjudication_outcomes) and all(
        outcome in {"passed", "waived", "not_applicable"} for outcome in adjudication_outcomes
    )
    return TransitionGuards(approval_recorded, completion_satisfied)
