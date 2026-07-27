"""AC-007: the tracker-detect pass (WS-P2.7 Inc-2) mirrors detect_reconciliation_conditions.

Conditions dedup on the divergence hash inside `record_reconciliation_condition`'s advisory
lock, regardless of the request's idempotency key -- so a duplicate detect pass surfaces as a
suppressed_duplicates count, never a second row.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import ReconciliationCondition
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    ObservedTrackerItem,
    detect_tracker_conditions,
)
from orchestrator.services.tracker_bindings import upsert_tracker_binding
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def test_a_duplicate_tracker_detect_records_no_second_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-tracker-detect")
    upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="tid-1",
        external_url=None,
        projected_state="ready",
    )
    migrated_session.commit()

    observed = [ObservedTrackerItem("todoist", "tid-1", True)]
    first = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=observed)
    second = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=observed)

    assert first == DetectionCounters(conditions_recorded=1)
    # A duplicate is SUPPRESSED and COUNTED -- observable, not silent.
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(ReconciliationCondition)
            .where(ReconciliationCondition.work_unit_id == unit.id)
        )
        == 1
    )
