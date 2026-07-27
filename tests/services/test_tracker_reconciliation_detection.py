"""WS-P2.7 Increment 2 Task 2: inbound tracker divergence detection.

Report-only, and fail-open like every other detector in this module: an unknown card
correlation is skipped and counted, never raised.
"""

from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import open_conditions
from orchestrator.services.reconciliation_detection import (
    ObservedTrackerItem,
    detect_tracker_conditions,
)
from orchestrator.services.tracker_bindings import upsert_tracker_binding
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def make_unit_with_binding(
    session: Session, state: WorkUnitState, *, external_item_id: str = "tid-1"
) -> WorkUnit:
    unit = register_unit(session, f"tracker-{external_item_id}-{state.value}")
    unit.state = state
    session.commit()
    upsert_tracker_binding(
        session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id=external_item_id,
        external_url=None,
        projected_state=state.value,
    )
    return unit


def _obs(external_item_id: str = "tid-1", completed: bool = True) -> list[ObservedTrackerItem]:
    return [ObservedTrackerItem("todoist", external_item_id, completed)]


def test_fires_on_completed_card_for_non_closed_unit(migrated_session: Session) -> None:
    unit = make_unit_with_binding(migrated_session, WorkUnitState.READY, external_item_id="tid-1")
    counters = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    assert counters.conditions_recorded == 1
    conditions = open_conditions(migrated_session, unit.id)
    assert len(conditions) == 1
    assert conditions[0].condition_type == "tracker_state_divergence"
    assert conditions[0].observation_kind == "tracker"


def test_fires_on_completed_card_for_failed_unit(migrated_session: Session) -> None:
    # failed is NOT a card-closed state (outbound keeps it open), so a completed card is a
    # human edit.
    make_unit_with_binding(migrated_session, WorkUnitState.FAILED, external_item_id="tid-1")
    counters = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    assert counters.conditions_recorded == 1


def test_no_condition_when_unit_completed(migrated_session: Session) -> None:
    unit = make_unit_with_binding(
        migrated_session, WorkUnitState.COMPLETED, external_item_id="tid-1"
    )
    counters = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    assert counters.conditions_recorded == 0
    assert open_conditions(migrated_session, unit.id) == ()


def test_no_condition_when_unit_cancelled(migrated_session: Session) -> None:
    # The false-fire the predicate exists to avoid: outbound closed this card, so it is agreement.
    unit = make_unit_with_binding(
        migrated_session, WorkUnitState.CANCELLED, external_item_id="tid-1"
    )
    counters = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    assert counters.conditions_recorded == 0
    assert open_conditions(migrated_session, unit.id) == ()


def test_no_condition_for_open_card(migrated_session: Session) -> None:
    make_unit_with_binding(migrated_session, WorkUnitState.READY, external_item_id="tid-1")
    counters = detect_tracker_conditions(
        migrated_session, SYSTEM, observed_states=_obs(completed=False)
    )
    assert counters == counters.__class__()  # all zero


def test_unknown_item_is_skipped_not_raised(migrated_session: Session) -> None:
    counters = detect_tracker_conditions(
        migrated_session, SYSTEM, observed_states=_obs(external_item_id="nope")
    )
    assert counters.skipped_correlations == 1
    assert counters.conditions_recorded == 0


def test_second_pass_suppresses_duplicate(migrated_session: Session) -> None:
    make_unit_with_binding(migrated_session, WorkUnitState.READY, external_item_id="tid-1")
    first = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    second = detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    assert first.conditions_recorded == 1
    assert second.conditions_recorded == 0
    assert second.suppressed_duplicates == 1


def test_detection_never_mutates_unit_state(migrated_session: Session) -> None:
    unit = make_unit_with_binding(migrated_session, WorkUnitState.READY, external_item_id="tid-1")
    detect_tracker_conditions(migrated_session, SYSTEM, observed_states=_obs())
    migrated_session.expire_all()
    refreshed = migrated_session.get(unit.__class__, unit.id)
    assert refreshed is not None
    assert refreshed.state == WorkUnitState.READY.value
