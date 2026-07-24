"""Record the actual LLM cost of a work-unit attempt as an append-only event (WS-P2.4).

The runner reports what its claude-code run actually consumed. This is a request entry point,
so it OWNS its transaction and commits. The events table's unique idempotency_key makes a
re-emit a no-op: we pre-check and also catch the race, so a duplicate is never a bare 500.
No envelope is touched; this only appends an event.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, WorkUnit
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

    event = Event(
        id=uuid.uuid4(),
        occurred_at=TransactionClock().now(session),
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
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )
    session.add(event)
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
