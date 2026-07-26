"""AC-007: the tracker-binding report (WS-P2.7) is a ROW_LOCK upsert, exactly like pr-binding.

`upsert_tracker_binding` takes its row FOR UPDATE (`get_tracker_binding`) before deciding
insert-vs-update, so a duplicate report is absorbed by the row lock rather than by an
idempotency key: there is exactly one row per unit by construction (PK on `work_unit_id`), and
re-reporting the same projection is a no-op.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitTrackerBinding
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.tracker_bindings import get_tracker_binding, upsert_tracker_binding
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def test_a_duplicate_tracker_binding_report_replays(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-tracker-binding")

    first = upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )
    replay = upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )

    assert (replay.tracker_system, replay.external_item_id, replay.projected_state) == (
        first.tracker_system,
        first.external_item_id,
        first.projected_state,
    )
    bindings = migrated_session.scalar(
        select(func.count())
        .select_from(UnitTrackerBinding)
        .where(UnitTrackerBinding.work_unit_id == unit.id)
    )
    assert bindings == 1
    assert get_tracker_binding(migrated_session, unit.id) is not None
