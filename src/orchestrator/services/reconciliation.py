"""Reconciliation conditions: a recorded divergence between pushed reality and stored state.

Recording a condition writes only the two append-only tables and `events`. It performs no
`work_units` write and no transition -- detection surfaces a divergence for an operator
decision; it never auto-un-completes a completed unit and never auto-resolves.

Two hashes, and the difference matters:

* `lineage_hash` = sha256(kind, condition_type, canonical(key_facts)) -- GENERATION-FREE, so it
  is a stable grouping key and the resolutions on a lineage can simply be counted.
* `normalized_divergence_hash` = sha256(lineage_hash, resolution_generation) -- what the UNIQUE
  constraint sees. Folding the generation in is what lets a RESOLVED divergence be raised again:
  without it, a recurring `check_result_flip` (which recurs with identical facts) would hit the
  UNIQUE, be silently swallowed, and never re-emit `reconciliation.required` -- permanently
  blinding the operator to exactly the conditions that recur.

An unresolved condition therefore dedups on re-detection, while a condition that recurs *after*
resolution mints a new row and a new event.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    RECONCILIATION_CONDITION_TYPES,
    RECONCILIATION_DECISIONS,
    RECONCILIATION_OBSERVATION_KINDS,
    Event,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.production_drill_resources import (
    bind_production_drill_resource,
    require_production_drill_resource,
)

# Distinct from evidence (0x57503338), observations (0x57533631) and the evidence-head recovery
# lock (0x57503232), so a lock taken here can never serialize against an unrelated ingress.
IDEMPOTENCY_LOCK_NAMESPACE = 0x57503231


@dataclass(frozen=True)
class ConditionCommand:
    actor: ActorContext
    work_unit_id: uuid.UUID
    observation_kind: str
    condition_type: str
    key_facts: dict[str, Any]
    stored_state: dict[str, Any]
    observed_state: dict[str, Any]
    detail: str
    observation_id: uuid.UUID | None = None
    deployment_observation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ConditionOutcome:
    """`suppressed` is True when an identical UNRESOLVED condition already existed.

    Callers use it to count `suppressed_duplicates`, so a dedup is observable rather than a
    silent miss.
    """

    condition: ReconciliationCondition
    suppressed: bool


@dataclass(frozen=True)
class ResolutionCommand:
    actor: ActorContext
    condition_id: uuid.UUID
    decision: str
    rationale: str
    idempotency_key: str


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def lineage_hash(observation_kind: str, condition_type: str, key_facts: dict[str, Any]) -> str:
    """Generation-free identity of a divergence, and the grouping key for its resolution count.

    `key_facts` is canonicalized HERE, at write time, and never recomputed from the stored JSONB
    -- jsonb reorders keys and drops duplicates, so a round-tripped rehash would differ.
    """
    encoded = _canonical(
        {
            "observation_kind": observation_kind,
            "condition_type": condition_type,
            "key_facts": key_facts,
        }
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def divergence_hash(lineage: str, generation: int) -> str:
    encoded = _canonical({"lineage_hash": lineage, "resolution_generation": generation})
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_reconciliation_condition(
    session: Session, command: ConditionCommand
) -> ConditionOutcome | DomainError:
    """Append-only. Commits its own transaction, so one failing condition cannot roll back an
    entire detect pass. Returns a DomainError rather than raising past the caller: a 500 here
    would let a malformed correlation take down the observation ingest path."""
    try:
        outcome = _record_condition(session, command)
        session.commit()
        return outcome
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        # Constraint-name agnostic on purpose: the idempotency and divergence uniques are
        # equivalent by key derivation, and which one PostgreSQL reports is not contractual.
        session.rollback()
        return DomainError(
            "reconciliation_conflict",
            "condition conflicts with an existing reconciliation condition",
            "re-run detection; an equivalent condition is already recorded",
        )
    except Exception:
        session.rollback()
        raise


def record_production_drill_reconciliation_condition(
    session: Session, *, run_id: uuid.UUID, command: ConditionCommand
) -> ConditionOutcome | DomainError:
    try:
        require_production_drill_resource(session, run_id, "work_unit", command.work_unit_id)
        outcome = _record_condition(session, command)
        bind_production_drill_resource(
            session, run_id, "reconciliation_condition", outcome.condition.id
        )
        session.commit()
        return outcome
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "reconciliation_conflict",
            "condition conflicts with an existing reconciliation condition",
            "re-run detection; an equivalent condition is already recorded",
        )
    except Exception:
        session.rollback()
        raise


def _record_condition(session: Session, command: ConditionCommand) -> ConditionOutcome:
    _authorize_system(command.actor)
    _validate_condition(command)
    if session.get(WorkUnit, command.work_unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)

    lineage = lineage_hash(command.observation_kind, command.condition_type, command.key_facts)
    # Lock on the LINEAGE, never on the idempotency key: the key embeds the generation, so two
    # concurrent detectors that disagree about the generation would take DIFFERENT locks and fail
    # to serialize -- exactly the race the lock exists to prevent. Lock -> count -> derive.
    _lock(session, f"{command.work_unit_id}:{lineage}")

    generation = _resolution_generation(session, command.work_unit_id, lineage)
    divergence = divergence_hash(lineage, generation)
    idempotency_key = f"reconcile:{command.work_unit_id}:{command.observation_kind}:{divergence}"

    existing = session.scalar(
        select(ReconciliationCondition).where(
            ReconciliationCondition.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return ConditionOutcome(condition=existing, suppressed=True)

    now = TransactionClock().now(session)
    condition_id, event_id = uuid.uuid4(), uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="reconciliation.required",
            subject_type="reconciliation_condition",
            subject_id=condition_id,
            # No from_state/to_state, and no work_unit write anywhere in this function:
            # detection only ever appends.
            from_state=None,
            to_state=None,
            payload=_event_payload(command, lineage, generation, divergence),
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    condition = ReconciliationCondition(
        id=condition_id,
        work_unit_id=command.work_unit_id,
        observation_kind=command.observation_kind,
        observation_id=command.observation_id,
        deployment_observation_id=command.deployment_observation_id,
        condition_type=command.condition_type,
        stored_state=command.stored_state,
        observed_state=command.observed_state,
        lineage_hash=lineage,
        resolution_generation=generation,
        normalized_divergence_hash=divergence,
        detail=command.detail,
        detected_at=now,
        event_id=event_id,
        idempotency_key=idempotency_key,
    )
    session.add(condition)
    session.flush()
    return ConditionOutcome(condition=condition, suppressed=False)


def record_resolution(
    session: Session, command: ResolutionCommand
) -> ReconciliationResolution | DomainError:
    try:
        resolution = _record_resolution(session, command)
        session.commit()
        return resolution
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "condition_already_resolved",
            "condition already has a resolution",
            "a recurrence mints a new condition; it is not a re-resolution",
        )
    except Exception:
        session.rollback()
        raise


def _record_resolution(session: Session, command: ResolutionCommand) -> ReconciliationResolution:
    _authorize_human(command.actor)
    if command.decision not in RECONCILIATION_DECISIONS:
        raise DomainError(
            "decision_invalid", f"decision must be one of {RECONCILIATION_DECISIONS}", None
        )
    _lock(session, command.idempotency_key)

    existing = session.scalar(
        select(ReconciliationResolution).where(
            ReconciliationResolution.idempotency_key == command.idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.condition_id != command.condition_id
            or existing.decision != command.decision
            or existing.rationale != command.rationale
            or existing.resolved_by != command.actor.actor_id
        ):
            raise _idempotency_conflict()
        return existing

    condition = session.scalar(
        select(ReconciliationCondition)
        .where(ReconciliationCondition.id == command.condition_id)
        .with_for_update()
    )
    if condition is None:
        raise DomainError("condition_not_found", "reconciliation condition does not exist", None)

    already = session.scalar(
        select(ReconciliationResolution).where(
            ReconciliationResolution.condition_id == command.condition_id
        )
    )
    if already is not None:
        # UNIQUE(condition_id) enforces this structurally; this is the legible path to it.
        raise DomainError(
            "condition_already_resolved",
            "condition has already been resolved",
            "a recurrence mints a new condition; it is not a re-resolution",
        )

    now = TransactionClock().now(session)
    resolution_id, event_id = uuid.uuid4(), uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="reconciliation.resolved",
            subject_type="reconciliation_resolution",
            subject_id=resolution_id,
            from_state=None,
            to_state=None,
            payload={
                "condition_id": str(command.condition_id),
                "decision": command.decision,
                "rationale": command.rationale,
                "resolved_by": command.actor.actor_id,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    resolution = ReconciliationResolution(
        id=resolution_id,
        condition_id=command.condition_id,
        resolved_by=command.actor.actor_id,
        decision=command.decision,
        rationale=command.rationale,
        resolved_at=now,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(resolution)
    session.flush()
    return resolution


def open_conditions(
    session: Session, work_unit_id: uuid.UUID | None = None
) -> tuple[ReconciliationCondition, ...]:
    """An OPEN condition is one with no resolution row -- a set difference, mirroring evidence
    supersession. There is no status column to drift."""
    resolved = select(ReconciliationResolution.condition_id)
    stmt = select(ReconciliationCondition).where(ReconciliationCondition.id.not_in(resolved))
    if work_unit_id is not None:
        stmt = stmt.where(ReconciliationCondition.work_unit_id == work_unit_id)
    stmt = stmt.order_by(ReconciliationCondition.detected_at, ReconciliationCondition.id)
    return tuple(session.scalars(stmt))


def _resolution_generation(session: Session, work_unit_id: uuid.UUID, lineage: str) -> int:
    """How many conditions on this lineage have already been resolved.

    Valid as a generation counter because UNIQUE(condition_id) makes each resolution row
    correspond to exactly one resolved condition on the lineage.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(ReconciliationResolution)
            .join(
                ReconciliationCondition,
                ReconciliationResolution.condition_id == ReconciliationCondition.id,
            )
            .where(
                ReconciliationCondition.work_unit_id == work_unit_id,
                ReconciliationCondition.lineage_hash == lineage,
            )
        )
        or 0
    )


def _event_payload(
    command: ConditionCommand, lineage: str, generation: int, divergence: str
) -> dict[str, Any]:
    return {
        "work_unit_id": str(command.work_unit_id),
        "observation_kind": command.observation_kind,
        "observation_id": str(command.observation_id) if command.observation_id else None,
        "deployment_observation_id": (
            str(command.deployment_observation_id) if command.deployment_observation_id else None
        ),
        "condition_type": command.condition_type,
        "key_facts": command.key_facts,
        "stored_state": command.stored_state,
        "observed_state": command.observed_state,
        "detail": command.detail,
        "lineage_hash": lineage,
        "resolution_generation": generation,
        "normalized_divergence_hash": divergence,
    }


def _validate_condition(command: ConditionCommand) -> None:
    if command.observation_kind not in RECONCILIATION_OBSERVATION_KINDS:
        raise DomainError(
            "observation_kind_invalid",
            f"observation_kind must be one of {RECONCILIATION_OBSERVATION_KINDS}",
            None,
        )
    if command.condition_type not in RECONCILIATION_CONDITION_TYPES:
        raise DomainError(
            "condition_type_invalid",
            f"condition_type must be one of {RECONCILIATION_CONDITION_TYPES}",
            None,
        )
    if not command.detail:
        raise DomainError("detail_required", "a condition requires a detail", None)


def _authorize_system(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may record a reconciliation condition",
            None,
        )


def _authorize_human(actor: ActorContext) -> None:
    # Invariant #4: detection never auto-resolves. A resolution is an operator decision.
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "role_forbidden", "only a human may resolve a reconciliation condition", None
        )


def _lock(session: Session, key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
        {"namespace": IDEMPOTENCY_LOCK_NAMESPACE, "key": key},
    )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different reconciliation record",
        "use a new idempotency key",
    )
