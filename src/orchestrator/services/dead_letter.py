"""The dead-letter view (AC-005): terminal failures made visible.

Derived LIVE from the source tables. There is no materialized dead-letter queue, so there is
nothing to drift out of sync with the reality it reports. Read-only: this module performs no
write, no transition, and no commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Claim, DispatchRecord, WorkUnit
from orchestrator.services.dispatch import circuit_open

DEAD_LETTER_UNIT_STATES = ("failed", "blocked", "cancelled")
DEAD_LETTER_DISPATCH_STATUSES = ("failed", "blocked")
# `blocked` is here because requeue TARGETS it. An action whose subject is invisible in the
# surface it is offered from is not an operator affordance.
REQUEUE_STATES = ("failed", "blocked")


@dataclass(frozen=True)
class DeadLetterEntry:
    source: str
    work_unit_id: uuid.UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None


def dead_letter(
    session: Session,
    *,
    failure_signature_threshold: int,
) -> tuple[DeadLetterEntry, ...]:
    return (
        *_terminal_units(session),
        *_failed_dispatch_records(session),
        *_open_circuit_breakers(session, failure_signature_threshold),
    )


def _terminal_units(session: Session) -> tuple[DeadLetterEntry, ...]:
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.in_(DEAD_LETTER_UNIT_STATES))
        .order_by(WorkUnit.unit_key, WorkUnit.id)
    ).all()
    return tuple(_unit_entry(session, unit) for unit in units)


def _unit_entry(session: Session, unit: WorkUnit) -> DeadLetterEntry:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc(), Claim.id.desc())
        .limit(1)
    )
    return DeadLetterEntry(
        source="work_unit",
        work_unit_id=unit.id,
        unit_key=unit.unit_key,
        unit_state=unit.state,
        reason_code=claim.terminal_reason if claim is not None else None,
        detail=str(claim.id) if claim is not None else None,
        attempt_count=unit.attempt_count,
        max_attempts=unit.max_attempts,
        requeue_eligible=_requeue_eligible(unit),
        occurred_at=claim.released_at if claim is not None else None,
    )


def _failed_dispatch_records(session: Session) -> tuple[DeadLetterEntry, ...]:
    rows = session.execute(
        select(DispatchRecord, WorkUnit)
        .join(WorkUnit, WorkUnit.id == DispatchRecord.work_unit_id)
        .where(DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES))
        .order_by(WorkUnit.unit_key, DispatchRecord.runner_attempt, DispatchRecord.id)
    ).all()
    return tuple(
        DeadLetterEntry(
            source="dispatch_record",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code=record.reason_code,
            detail=record.failure_signature,
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=None,
        )
        for record, unit in rows
    )


def _open_circuit_breakers(
    session: Session,
    failure_signature_threshold: int,
) -> tuple[DeadLetterEntry, ...]:
    """Derived -- there is no persisted breaker entity; "open" is a predicate over the records.

    `circuit_open` is called with the AT-REST count, never `count + 1`. Dispatch's own call site
    is PROSPECTIVE: it counts the failure it is about to write. Reusing that call here would
    report a breaker open one failure early -- flagging a unit as circuit-broken while it still
    has a dispatch left. One shared predicate, two tenses; the `+ 1` lives only at dispatch.
    """
    grouped = session.execute(
        select(
            DispatchRecord.work_unit_id,
            DispatchRecord.failure_signature,
            func.count().label("failures"),
        )
        .where(
            DispatchRecord.failure_signature.is_not(None),
            DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES),
        )
        .group_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
        .order_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
    ).all()
    entries: list[DeadLetterEntry] = []
    for unit_id, signature, failures in grouped:
        if not circuit_open(failures, failure_signature_threshold):
            continue
        unit = session.get(WorkUnit, unit_id)
        if unit is None:
            continue
        entries.append(
            DeadLetterEntry(
                source="circuit_breaker",
                work_unit_id=unit.id,
                unit_key=unit.unit_key,
                unit_state=unit.state,
                reason_code="failure_signature_circuit_open",
                detail=signature,
                attempt_count=unit.attempt_count,
                max_attempts=unit.max_attempts,
                requeue_eligible=_requeue_eligible(unit),
                occurred_at=None,
            )
        )
    return tuple(entries)


def _requeue_eligible(unit: WorkUnit) -> bool:
    """An exhausted unit is NOT requeue-eligible.

    Requeue would land it READY, where `claim_unit` rejects it with `attempts_exhausted` -- and
    it would drop out of this view at the same time: invisible AND unrunnable. `retry` (which
    raises the budget) is the operator's path for that case.
    """
    return unit.state in REQUEUE_STATES and unit.attempt_count < unit.max_attempts
