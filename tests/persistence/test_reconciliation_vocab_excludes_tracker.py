"""The tracker adapter's retirement (ADR-0040 amendment 1): its vocabulary and table are gone."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence import models
from orchestrator.persistence.models import (
    RECONCILIATION_CONDITION_TYPES,
    RECONCILIATION_OBSERVATION_KINDS,
    ReconciliationCondition,
)


def test_constants_exclude_the_tracker_members() -> None:
    assert "tracker" not in RECONCILIATION_OBSERVATION_KINDS
    assert "tracker_state_divergence" not in RECONCILIATION_CONDITION_TYPES
    assert not hasattr(models, "TRACKER_SYSTEMS")
    assert not hasattr(models, "UnitTrackerBinding")


def test_the_bindings_table_does_not_exist(migrated_engine: Engine) -> None:
    assert "unit_tracker_bindings" not in inspect(migrated_engine).get_table_names()


def _condition(**overrides: Any) -> ReconciliationCondition:
    base: dict[str, Any] = dict(
        observation_kind="github_pr",
        condition_type="pr_state_divergence",
        stored_state={},
        observed_state={},
        lineage_hash="sha256:x",
        normalized_divergence_hash="sha256:y",
        detail="d",
        idempotency_key=str(uuid.uuid4()),
    )
    base.update(overrides)
    return ReconciliationCondition(**base)


def test_a_current_row_is_accepted(
    migrated_session: Session, work_unit_and_event: tuple[uuid.UUID, uuid.UUID]
) -> None:
    unit_id, event_id = work_unit_and_event
    migrated_session.add(_condition(work_unit_id=unit_id, event_id=event_id))
    migrated_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"observation_kind": "tracker"},
        {"condition_type": "tracker_state_divergence"},
        {"condition_type": "not_a_real_type"},
    ],
)
def test_the_database_rejects_tracker_and_unknown_values(
    migrated_session: Session,
    work_unit_and_event: tuple[uuid.UUID, uuid.UUID],
    override: dict[str, str],
) -> None:
    unit_id, event_id = work_unit_and_event
    migrated_session.add(_condition(work_unit_id=unit_id, event_id=event_id, **override))
    with pytest.raises(IntegrityError):
        migrated_session.commit()
