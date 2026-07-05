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
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    occurred_at = clock.now(session)
    authorize_transition(
        source,
        command.target,
        command.actor.role,
        _transition_guards(session, unit, revision, occurred_at),
    )

    next_version = unit.version + 1
    unit.state = command.target
    unit.version = next_version
    event = _transition_event(command, unit, source, revision.registry_version, occurred_at)
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
            "command": _command_identity(command, source),
            "registry_version": registry_version,
            "version": unit.version,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=command.idempotency_key,
    )


def _idempotent_result(event: Event, command: TransitionCommand) -> TransitionResult:
    try:
        source = WorkUnitState(event.from_state)
    except ValueError:
        source = None
    expected = (
        event.subject_type == "work_unit"
        and event.subject_id == command.unit_id
        and event.action == "work_unit.transitioned"
        and source is not None
        and event.payload.get("command") == _command_identity(command, source)
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


def _command_identity(command: TransitionCommand, source: WorkUnitState) -> dict[str, str | int]:
    return {
        "action": "work_unit.transitioned",
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role,
        "expected_version": command.expected_version,
        "from_state": source,
        "target": command.target,
        "unit_id": str(command.unit_id),
    }


def _transition_guards(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    occurred_at: datetime,
) -> TransitionGuards:
    approval_recorded = (
        session.execute(
            select(Approval.id)
            .where(
                Approval.subject_type == "action",
                Approval.subject_id == unit.id,
                Approval.decision == "approved",
                Approval.subject_revision_or_fingerprint == str(unit.version),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    adjudications = tuple(
        session.execute(
            select(Adjudication).where(
                Adjudication.work_unit_id == unit.id,
                Adjudication.work_package_revision_id == revision.id,
            )
        ).scalars()
    )
    return TransitionGuards(
        approval_recorded,
        _completion_satisfied(revision.enforcement_snapshot, adjudications, occurred_at),
    )


def _completion_satisfied(
    enforcement_snapshot: dict[str, object],
    adjudications: tuple[Adjudication, ...],
    occurred_at: datetime,
) -> bool:
    required_ac_ids = _required_ac_ids(enforcement_snapshot)
    if required_ac_ids is None:
        return False
    grouped = {ac_id: [] for ac_id in required_ac_ids}
    for adjudication in adjudications:
        if adjudication.ac_id not in grouped:
            return False
        grouped[adjudication.ac_id].append(adjudication)
    return all(
        _current_terminal_is_satisfied(tuple(grouped[ac_id]), occurred_at)
        for ac_id in required_ac_ids
    )


def _required_ac_ids(enforcement_snapshot: dict[str, object]) -> tuple[str, ...] | None:
    value = enforcement_snapshot.get("acceptance_criteria")
    if not isinstance(value, list):
        return None
    ac_ids = tuple(item for item in value if isinstance(item, str) and item.strip())
    if len(ac_ids) != len(value) or len(set(ac_ids)) != len(ac_ids):
        return None
    return ac_ids


def _current_terminal_is_satisfied(
    adjudications: tuple[Adjudication, ...], occurred_at: datetime
) -> bool:
    by_id = {adjudication.id: adjudication for adjudication in adjudications}
    superseded_ids: set[uuid.UUID] = set()
    for adjudication in adjudications:
        previous_id = adjudication.supersedes_adjudication_id
        if previous_id is None:
            continue
        previous = by_id.get(previous_id)
        if (
            previous is None
            or previous.ac_id != adjudication.ac_id
            or previous_id in superseded_ids
            or previous_id == adjudication.id
        ):
            return False
        superseded_ids.add(previous_id)

    current = tuple(row for row in adjudications if row.id not in superseded_ids)
    if len(current) != 1 or not _is_single_chain(current[0], by_id):
        return False
    terminal = current[0]
    if terminal.outcome in {"passed", "not_applicable"}:
        return True
    return (
        terminal.outcome == "waived"
        and terminal.scope is None
        and (terminal.expires_at is None or terminal.expires_at > occurred_at)
    )


def _is_single_chain(current: Adjudication, by_id: dict[uuid.UUID, Adjudication]) -> bool:
    visited: set[uuid.UUID] = set()
    cursor: Adjudication | None = current
    while cursor is not None:
        if cursor.id in visited:
            return False
        visited.add(cursor.id)
        previous_id = cursor.supersedes_adjudication_id
        cursor = by_id.get(previous_id) if previous_id is not None else None
    return len(visited) == len(by_id)
