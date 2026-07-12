"""The dead-letter view: terminal failures AND stalled approval gates made visible.

Derived LIVE from the source tables. There is no materialized dead-letter queue, so there is
nothing to drift out of sync with the reality it reports. Read-only: this module performs no
write, no transition, and no commit.

WS-P2.15 widened the view's contract. It used to report only terminal failures. It now also
reports STALLED APPROVAL GATES -- a unit sitting in `awaiting_approval` or `awaiting_review`
that no human has answered past a threshold.

Two things about that, both deliberate:

  * It is a DERIVED READ, not a written record. A stalled gate is not an event that happened;
    it is a fact about the present -- a predicate over (state, updated_at). The predecessor
    design wrote a `blocked` DispatchRecord instead, which (a) could not use runner_attempt=0
    (`CheckConstraint("runner_attempt > 0")`), and (b) with any other runner_attempt silently
    consumed a dispatch slot, so a later genuine dispatch of the same unit would be
    short-circuited into returning the stale record and never dispatch. When every variant of
    a mechanism is broken, the mechanism is wrong. `_open_circuit_breakers` below is the
    precedent: also derived, also unpersisted.

  * SILENCE IS NEVER APPROVAL. This REPORTS. It transitions nothing, and it cannot: every edge
    out of an approval state requires a named HUMAN actor (asserted in
    tests/kernel/test_approval_edges_require_a_human.py). A stalled gate is therefore reported
    and NOT requeue-eligible -- which falls out of the existing `_requeue_eligible` predicate
    for free, since an approval state is not in REQUEUE_STATES.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.persistence.models import Claim, DispatchRecord, ProductionDrillResource, WorkUnit
from orchestrator.services.dispatch import circuit_open
from orchestrator.services.production_drill_resources import is_not_production_drill_resource
from orchestrator.services.production_drills import production_drill_deadlines

DEAD_LETTER_UNIT_STATES = ("failed", "blocked", "cancelled")
DEAD_LETTER_DISPATCH_STATUSES = ("failed", "blocked")
# `blocked` is here because requeue TARGETS it. An action whose subject is invisible in the
# surface it is offered from is not an operator affordance.
REQUEUE_STATES = ("failed", "blocked")
# The gates a human must answer. Nothing here can be answered by time passing.
APPROVAL_STATES = ("awaiting_approval", "awaiting_review")


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
    stalled_approval_seconds: int,
    include_production_drill_resources: bool = False,
    production_drill_run_id: uuid.UUID | None = None,
) -> tuple[DeadLetterEntry, ...]:
    """`stalled_approval_seconds` is a plain int on purpose. It has no "off" value.

    Its predecessor was `int | None = None`, and that None is precisely why the age-out it
    configured sat unwired and invisible for an entire workstream. A reporting obligation that
    can be switched off is a reporting obligation that will be.
    """
    return (
        *_terminal_units(session, include_production_drill_resources, production_drill_run_id),
        *_failed_dispatch_records(
            session, include_production_drill_resources, production_drill_run_id
        ),
        *_open_circuit_breakers(
            session,
            failure_signature_threshold,
            include_production_drill_resources,
            production_drill_run_id,
        ),
        *_stalled_approvals(
            session,
            stalled_approval_seconds,
            include_production_drill_resources,
            production_drill_run_id,
        ),
    )


def _stalled_approvals(
    session: Session,
    stalled_approval_seconds: int,
    include_production_drill_resources: bool,
    production_drill_run_id: uuid.UUID | None = None,
) -> tuple[DeadLetterEntry, ...]:
    """A human gate nobody answered. Reported, never resolved -- silence is not approval."""
    statement = select(WorkUnit).where(WorkUnit.state.in_(APPROVAL_STATES))
    if production_drill_run_id is not None:
        statement = statement.where(_run_owned_unit(production_drill_run_id))
        deadlines = production_drill_deadlines(session, production_drill_run_id)
        if not hasattr(deadlines, "reporting_deadline"):
            return ()
        cutoff = TransactionClock().now(session) - deadlines.reporting_deadline
    else:
        cutoff = TransactionClock().now(session) - timedelta(seconds=stalled_approval_seconds)
    statement = statement.where(WorkUnit.updated_at <= cutoff)
    if production_drill_run_id is None and not include_production_drill_resources:
        statement = statement.where(is_not_production_drill_resource("work_unit", WorkUnit.id))
    units = session.scalars(statement.order_by(WorkUnit.updated_at, WorkUnit.id)).all()
    return tuple(
        DeadLetterEntry(
            source="stalled_approval",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code="approval_unanswered",
            detail=f"awaiting a human decision since {unit.updated_at.isoformat()}",
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            # False, from the existing predicate: an approval state is not a REQUEUE_STATE.
            # A stalled gate needs a human decision, not a retry.
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=unit.updated_at,
        )
        for unit in units
    )


def _terminal_units(
    session: Session,
    include_production_drill_resources: bool,
    production_drill_run_id: uuid.UUID | None = None,
) -> tuple[DeadLetterEntry, ...]:
    statement = select(WorkUnit).where(WorkUnit.state.in_(DEAD_LETTER_UNIT_STATES))
    if production_drill_run_id is not None:
        statement = statement.where(_run_owned_unit(production_drill_run_id))
    elif not include_production_drill_resources:
        statement = statement.where(is_not_production_drill_resource("work_unit", WorkUnit.id))
    units = session.scalars(statement.order_by(WorkUnit.unit_key, WorkUnit.id)).all()
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


def _failed_dispatch_records(
    session: Session,
    include_production_drill_resources: bool,
    production_drill_run_id: uuid.UUID | None = None,
) -> tuple[DeadLetterEntry, ...]:
    statement = (
        select(DispatchRecord, WorkUnit)
        .join(WorkUnit, WorkUnit.id == DispatchRecord.work_unit_id)
        .where(DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES))
    )
    if production_drill_run_id is not None:
        statement = statement.where(_run_owned_unit(production_drill_run_id))
    elif not include_production_drill_resources:
        statement = statement.where(is_not_production_drill_resource("work_unit", WorkUnit.id))
    rows = session.execute(
        statement.order_by(WorkUnit.unit_key, DispatchRecord.runner_attempt, DispatchRecord.id)
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
    include_production_drill_resources: bool,
    production_drill_run_id: uuid.UUID | None = None,
) -> tuple[DeadLetterEntry, ...]:
    """Derived -- there is no persisted breaker entity; "open" is a predicate over the records.

    `circuit_open` is called with the AT-REST count, never `count + 1`. Dispatch's own call site
    is PROSPECTIVE: it counts the failure it is about to write. Reusing that call here would
    report a breaker open one failure early -- flagging a unit as circuit-broken while it still
    has a dispatch left. One shared predicate, two tenses; the `+ 1` lives only at dispatch.
    """
    statement = (
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
    )
    if production_drill_run_id is not None:
        statement = statement.where(
            ProductionDrillResource.run_id == production_drill_run_id,
            ProductionDrillResource.resource_type == "work_unit",
            ProductionDrillResource.resource_id == DispatchRecord.work_unit_id,
        )
    elif not include_production_drill_resources:
        statement = statement.where(
            is_not_production_drill_resource("work_unit", DispatchRecord.work_unit_id)
        )
    grouped = session.execute(
        statement.order_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
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


def _run_owned_unit(run_id: uuid.UUID):
    return (
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "work_unit",
        ProductionDrillResource.resource_id == WorkUnit.id,
    )
