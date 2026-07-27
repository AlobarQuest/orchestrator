"""WS-P2.7 Increment 2 Task 1: tracker reconciliation vocabulary + migration 0019."""

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    RECONCILIATION_CONDITION_TYPES,
    RECONCILIATION_OBSERVATION_KINDS,
    ReconciliationCondition,
)


def test_tracker_vocab_members_present() -> None:
    assert "tracker" in RECONCILIATION_OBSERVATION_KINDS
    assert "tracker_state_divergence" in RECONCILIATION_CONDITION_TYPES


def _condition(**overrides: Any) -> ReconciliationCondition:
    base: dict[str, Any] = dict(
        work_unit_id=overrides.pop("work_unit_id"),
        observation_kind="tracker",
        condition_type="tracker_state_divergence",
        stored_state={},
        observed_state={},
        lineage_hash="sha256:x",
        normalized_divergence_hash="sha256:y",
        detail="d",
        idempotency_key=str(uuid.uuid4()),
        event_id=overrides.pop("event_id"),
    )
    base.update(overrides)
    return ReconciliationCondition(**base)


def test_check_accepts_tracker_values(
    migrated_session: Session, work_unit_and_event: tuple[uuid.UUID, uuid.UUID]
) -> None:
    unit_id, event_id = work_unit_and_event
    migrated_session.add(_condition(work_unit_id=unit_id, event_id=event_id))
    migrated_session.commit()  # no IntegrityError


def test_check_rejects_unknown_condition_type(
    migrated_session: Session, work_unit_and_event: tuple[uuid.UUID, uuid.UUID]
) -> None:
    unit_id, event_id = work_unit_and_event
    migrated_session.add(
        _condition(work_unit_id=unit_id, event_id=event_id, condition_type="not_a_real_type")
    )
    with pytest.raises(IntegrityError):
        migrated_session.commit()
