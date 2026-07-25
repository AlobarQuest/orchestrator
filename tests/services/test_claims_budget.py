import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from tests.services.test_budget import (
    READY_UNIT_MAX_LLM_CALLS,
    _build_unit_no_ceiling,
    _cost_event,
)
from tests.services.test_claims import worker


def _transition_event(session: Session, unit_id: uuid.UUID, idempotency_key: str) -> Event | None:
    return session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))


def test_over_budget_claim_halts_and_records_breach(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _cost_event(migrated_session, ready_unit.id, llm_calls=ceiling)

    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-budget-1")

    assert isinstance(result, DomainError)
    assert result.code == "budget_exceeded"

    # The halt must have PERSISTED -- prove it survived past this function's return, not just
    # that the in-session ORM instance looks right.
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, ready_unit.id)
    assert refreshed is not None
    assert refreshed.state == "failed"

    event = _transition_event(migrated_session, ready_unit.id, "claim-budget-1:budget-halt")
    assert event is not None
    assert event.to_state == "failed"
    assert event.payload["reason"] == "budget_exceeded"


def test_under_budget_claim_succeeds(migrated_session: Session, ready_unit: WorkUnit) -> None:
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _cost_event(migrated_session, ready_unit.id, llm_calls=ceiling - 1)

    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-budget-2")

    assert isinstance(result, LeaseGrant)
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, ready_unit.id)
    assert refreshed is not None
    assert refreshed.state == "claimed"


def test_no_ceiling_claim_succeeds(migrated_session: Session) -> None:
    unit = _build_unit_no_ceiling(migrated_session, "no-ceiling-claim")
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    _cost_event(migrated_session, unit.id, llm_calls=10_000)

    result = claim_unit(migrated_session, unit.id, worker(), "claim-budget-3")

    assert isinstance(result, LeaseGrant)


def test_unknown_cost_does_not_trip_budget(migrated_session: Session, ready_unit: WorkUnit) -> None:
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _cost_event(migrated_session, ready_unit.id, llm_calls=ceiling * 100, cost_known=False)

    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-budget-4")

    assert isinstance(result, LeaseGrant)
