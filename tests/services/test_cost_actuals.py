import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event
from orchestrator.services.cost_actuals import record_cost_actuals
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
