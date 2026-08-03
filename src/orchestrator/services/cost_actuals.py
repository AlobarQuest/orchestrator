"""Record the actual LLM cost of a work-unit attempt as an append-only event (WS-P2.4).

The runner reports what its claude-code run actually consumed. This is a request entry point,
so it OWNS its transaction and commits. The events table's unique idempotency_key makes a
re-emit a no-op: we pre-check and also catch the race, so a duplicate is never a bare 500.
No envelope is touched; this only appends an event.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.budget import BREACH_ACTION, cumulative_llm_calls, declared_ceiling
from orchestrator.services.claims import validate_active_claim
from orchestrator.services.lifecycle import ActorContext

ACTION = "attempt.cost_recorded"


def record_cost_actuals(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    attempt: int,
    lease_token: str,
    cost_known: bool,
    llm_calls: int | None,
    num_turns: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    idempotency_key: str,
) -> Event:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    _authorize(session, unit, actor, attempt, lease_token)

    existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    occurred_at = TransactionClock().now(session)
    correlation_id = uuid.uuid4()
    # Computed BEFORE the cost event joins the session: the total is this report's calls plus
    # what is already on file, and a flushed row would be counted on both sides.
    breach = _breach_event(
        session,
        unit,
        occurred_at=occurred_at,
        actor=actor,
        attempt=attempt,
        cost_known=cost_known,
        llm_calls=llm_calls,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    event = Event(
        id=uuid.uuid4(),
        occurred_at=occurred_at,
        actor_id=actor.actor_id,
        action=ACTION,
        subject_type="work_unit",
        subject_id=work_unit_id,
        from_state=None,
        to_state=None,
        payload={
            "attempt": attempt,
            "cost_known": cost_known,
            "llm_calls": llm_calls,
            "num_turns": num_turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        },
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    if breach is not None:
        # Same transaction as the cost event it is derived from: a breach that could be lost
        # while the spend it describes survived would be worse than not recording it.
        session.add(breach)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent emit won the unique idempotency_key. First write wins; return it.
        session.rollback()
        winner = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
        if winner is None:
            raise
        return winner
    return event


def _breach_event(
    session: Session,
    unit: WorkUnit,
    *,
    occurred_at: datetime,
    actor: ActorContext,
    attempt: int,
    cost_known: bool,
    llm_calls: int | None,
    correlation_id: uuid.UUID,
    idempotency_key: str,
) -> Event | None:
    """Record an overrun at the moment it becomes known, or None when none happened.

    `is_over_budget` is asked only when a unit is about to be granted ANOTHER attempt, so an
    attempt that blows its declared ceiling and then finishes is never asked at all: the ceiling
    was approved by a human, exceeded, and left no trace.

    The condition is "the total INCLUDING this report is at or over the ceiling, and no breach
    is on file yet" -- deliberately NOT "this report crossed the ceiling". Crossing arithmetic
    reads as the tighter test and is strictly weaker in two ways: a declared ceiling of 0 is
    already met before the first report, so a crossing never happens and the unit that most
    obviously overran records nothing; and if two reports ever land without seeing each other,
    a crossing missed once is missed permanently, whereas an at-or-over test recovers on the
    next report. The `>=` matches `is_over_budget`, so the two never disagree about a unit.

    Recording, never refusal. The orchestrator cannot interrupt a run that has already happened,
    and refusing here would only make the worker's best-effort emit drop the very fact this
    exists to capture.
    """
    ceiling = declared_ceiling(unit)
    if ceiling is None:
        return None
    total = cumulative_llm_calls(session, unit.id) + ((llm_calls or 0) if cost_known else 0)
    if total < ceiling or _breach_on_file(session, unit.id):
        return None
    return Event(
        id=uuid.uuid4(),
        occurred_at=occurred_at,
        actor_id=actor.actor_id,
        action=BREACH_ACTION,
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=None,
        to_state=None,
        # `reason` is the key the evidence pack renders. The two counts are audit fields with no
        # reader today; they are here so the row states its own case without a second query.
        payload={
            "reason": "budget_exceeded",
            "attempt": attempt,
            "declared_max_llm_calls": ceiling,
            "cumulative_llm_calls": total,
        },
        # Shared with the cost event this is a consequence of -- see `claims.py`, which links a
        # transition to its failure the same way.
        correlation_id=correlation_id,
        idempotency_key=f"{idempotency_key}:budget-breach",
    )


def _breach_on_file(session: Session, unit_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(Event.id)
            .where(
                Event.action == BREACH_ACTION,
                Event.subject_type == "work_unit",
                Event.subject_id == unit_id,
            )
            .limit(1)
        )
        is not None
    )


def _authorize(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    attempt: int,
    lease_token: str,
) -> None:
    if actor.role is ActorRole.SYSTEM:
        return
    if actor.role is not ActorRole.WORKER:
        raise DomainError(
            "role_forbidden",
            "only a claim-holding worker or the system actor may record cost actuals",
            None,
        )
    validate_active_claim(session, unit, actor, attempt, lease_token)
