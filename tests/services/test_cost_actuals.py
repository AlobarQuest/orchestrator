import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event
from orchestrator.services.budget import BREACH_ACTION
from orchestrator.services.cost_actuals import record_cost_actuals
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_budget import (
    READY_UNIT_MAX_LLM_CALLS,
    _build_unit_no_ceiling,
    _build_unit_with_ceiling,
)
from tests.services.test_evidence import active_claim, worker


def _record(session: Session, unit, **overrides: Any) -> Event:
    grant = active_claim(session, unit)
    kwargs: dict[str, Any] = dict(
        work_unit_id=unit.id,
        actor=worker(),
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        cost_known=True,
        llm_calls=37,
        num_turns=12,
        input_tokens=812004,
        output_tokens=41220,
        cost_usd=9.14,
        idempotency_key=f"factory-runner:{unit.id}:cost:a{grant.attempt}",
    )
    kwargs.update(overrides)
    return record_cost_actuals(session, **kwargs)


def test_records_event_and_persists(migrated_session: Session, ready_unit) -> None:
    event = _record(migrated_session, ready_unit)
    migrated_session.expire_all()  # prove it committed, not just flushed
    reread = migrated_session.get(Event, event.id)
    assert reread is not None
    assert reread.action == "attempt.cost_recorded"
    assert reread.subject_type == "work_unit"
    assert reread.subject_id == ready_unit.id
    assert reread.payload["llm_calls"] == 37
    assert reread.payload["cost_known"] is True


def test_reemit_same_key_is_idempotent(migrated_session: Session, ready_unit) -> None:
    grant = active_claim(migrated_session, ready_unit)
    idempotency_key = f"factory-runner:{ready_unit.id}:cost:a{grant.attempt}"
    first = record_cost_actuals(
        migrated_session,
        work_unit_id=ready_unit.id,
        actor=worker(),
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        cost_known=True,
        llm_calls=37,
        num_turns=12,
        input_tokens=812004,
        output_tokens=41220,
        cost_usd=9.14,
        idempotency_key=idempotency_key,
    )
    again = record_cost_actuals(
        migrated_session,
        work_unit_id=ready_unit.id,
        actor=worker(),
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        cost_known=True,
        llm_calls=37,
        num_turns=12,
        input_tokens=812004,
        output_tokens=41220,
        cost_usd=9.14,
        idempotency_key=idempotency_key,
    )
    assert again.id == first.id
    count = sum(
        1 for _ in migrated_session.query(Event).filter(Event.action == "attempt.cost_recorded")
    )
    assert count == 1


def test_unknown_unit_is_domain_error(migrated_session: Session, ready_unit) -> None:
    with pytest.raises(DomainError) as exc:
        record_cost_actuals(
            migrated_session,
            work_unit_id=uuid.uuid4(),
            actor=worker(),
            attempt=1,
            lease_token="x",
            cost_known=False,
            llm_calls=None,
            num_turns=None,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            idempotency_key="k",
        )
    assert exc.value.code == "work_unit_not_found"


def test_wrong_lease_is_domain_error(migrated_session: Session, ready_unit) -> None:
    with pytest.raises(DomainError):
        _record(migrated_session, ready_unit, lease_token="not-the-lease")


# ---- the overrun a completed attempt used to leave no trace of (WS-P2.31) ------------------
#
# `is_over_budget` is asked only when a unit is about to be granted ANOTHER attempt, so an
# attempt that blew its ceiling and then finished was recorded nowhere. Each test below names
# the control that would have shown the emitter firing when it should not.


def _breaches(session: Session, unit_id: uuid.UUID) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(Event.action == BREACH_ACTION, Event.subject_id == unit_id)
        )
    )


def test_spend_over_the_ceiling_records_a_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _record(migrated_session, ready_unit, llm_calls=ceiling + 1)

    # A DIFFERENT session: the only reader that cannot see an uncommitted write, so this
    # discriminates where expire_all() would not.
    with Session(migrated_engine) as reader:
        recorded = _breaches(reader, ready_unit.id)
        assert len(recorded) == 1
        assert recorded[0].payload["reason"] == "budget_exceeded"
        assert recorded[0].payload["declared_max_llm_calls"] == ceiling
        assert recorded[0].payload["cumulative_llm_calls"] == ceiling + 1


def test_spend_under_the_ceiling_records_no_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    """The control for the test above: same call, one call short of the ceiling. Without it,
    an emitter that fired unconditionally would pass every other test in this block."""
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _record(migrated_session, ready_unit, llm_calls=ceiling - 1)

    with Session(migrated_engine) as reader:
        assert _breaches(reader, ready_unit.id) == []


def test_spend_exactly_at_the_ceiling_records_a_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    """`is_over_budget` refuses the next claim at >= ceiling, so the recorded breach must use
    the same boundary or the two disagree about the same unit."""
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    _record(migrated_session, ready_unit, llm_calls=ceiling)

    with Session(migrated_engine) as reader:
        assert len(_breaches(reader, ready_unit.id)) == 1


def test_unknown_cost_records_no_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    """An attempt whose cost is unknown contributes nothing to the total, so it cannot reach a
    ceiling. Such attempts stay bounded by max_attempts.

    Every numeric is nulled, not just llm_calls: `CostActualsCommand` rejects a non-null numeric
    beside `cost_known: false`, so any other combination is a state the wire cannot produce.
    """
    _record(
        migrated_session,
        ready_unit,
        cost_known=False,
        llm_calls=None,
        num_turns=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )

    with Session(migrated_engine) as reader:
        assert _breaches(reader, ready_unit.id) == []


def test_no_declared_ceiling_records_no_breach(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit = _build_unit_no_ceiling(migrated_session, "no-ceiling-cost")
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    _record(migrated_session, unit, llm_calls=10_000)

    with Session(migrated_engine) as reader:
        assert _breaches(reader, unit.id) == []


def test_a_zero_ceiling_records_a_breach_on_the_first_spend(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """`_is_budget_value` accepts 0, so `max_llm_calls: 0` is a real ceiling already met before
    any spend. A guard keyed on CROSSING the ceiling never fires for such a unit -- there is no
    under-to-over transition to see -- so the unit that most obviously overran would record
    nothing while `is_over_budget` called it over from birth.

    Reachable only through the SYSTEM actor: `_authorize` waives the claim proof for SYSTEM,
    and a worker cannot get here at all because `claim_unit` refuses a zero-ceiling unit
    outright. That refusal is why this case is narrow -- not why it is absent.
    """
    unit = _build_unit_with_ceiling(migrated_session, "zero-ceiling", ceiling=0)
    migrated_session.commit()
    record_cost_actuals(
        migrated_session,
        work_unit_id=unit.id,
        actor=ActorContext("orchestrator-system", ActorRole.SYSTEM),
        attempt=1,
        lease_token="unused-for-system",
        cost_known=True,
        llm_calls=10_000,
        num_turns=3,
        input_tokens=1,
        output_tokens=1,
        cost_usd=1.0,
        idempotency_key=f"system:{unit.id}:cost:a1",
    )

    with Session(migrated_engine) as reader:
        recorded = _breaches(reader, unit.id)
        assert len(recorded) == 1
        assert recorded[0].payload["declared_max_llm_calls"] == 0
        assert recorded[0].payload["cumulative_llm_calls"] == 10_000


def _report(session: Session, unit, grant, *, llm_calls: int, key: str) -> None:
    """One claim, several reports -- re-claiming would mint a second lease and the second
    report would be refused for the wrong reason."""
    record_cost_actuals(
        session,
        work_unit_id=unit.id,
        actor=worker(),
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        cost_known=True,
        llm_calls=llm_calls,
        num_turns=1,
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.5,
        idempotency_key=key,
    )


def test_breach_is_recorded_once_however_many_reports_follow(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    """One unit records one breach, not one per later report."""
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    grant = active_claim(migrated_session, ready_unit)
    _report(migrated_session, ready_unit, grant, llm_calls=ceiling + 5, key="cost-a")
    _report(migrated_session, ready_unit, grant, llm_calls=ceiling + 5, key="cost-b")

    with Session(migrated_engine) as reader:
        assert len(_breaches(reader, ready_unit.id)) == 1


def test_a_report_landing_exactly_on_the_ceiling_still_records_only_one_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    """The boundary case the test above cannot reach: it overshoots, so a guard keyed on
    `> ceiling` instead of `>= ceiling` would survive it. Here the first report lands EXACTLY
    on the ceiling, and the second must not add a duplicate."""
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    grant = active_claim(migrated_session, ready_unit)
    _report(migrated_session, ready_unit, grant, llm_calls=ceiling, key="cost-exact")
    _report(migrated_session, ready_unit, grant, llm_calls=1, key="cost-more")

    with Session(migrated_engine) as reader:
        assert len(_breaches(reader, ready_unit.id)) == 1


def test_replayed_cost_report_records_no_second_breach(
    migrated_session: Session, migrated_engine: Engine, ready_unit
) -> None:
    ceiling = READY_UNIT_MAX_LLM_CALLS
    assert ceiling is not None
    grant = active_claim(migrated_session, ready_unit)
    _report(migrated_session, ready_unit, grant, llm_calls=ceiling + 1, key="cost-same")
    _report(migrated_session, ready_unit, grant, llm_calls=ceiling + 1, key="cost-same")

    with Session(migrated_engine) as reader:
        assert len(_breaches(reader, ready_unit.id)) == 1
