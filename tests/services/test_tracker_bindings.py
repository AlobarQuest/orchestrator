"""WS-P2.7 Task 1: the `unit_tracker_bindings` row persists and its vocabulary is closed."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import UnitTrackerBinding
from tests.services.test_dependencies import register_unit


def test_tracker_binding_row_persists_and_rejects_bad_tracker_system(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "tracker-binding")
    binding = UnitTrackerBinding(
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-123",
        external_url="https://todoist.com/app/task/task-123",
        projected_state="ready",
    )
    migrated_session.add(binding)
    migrated_session.commit()
    migrated_session.expire_all()
    reread = migrated_session.get(UnitTrackerBinding, unit.id)
    assert reread.tracker_system == "todoist"
    assert reread.external_item_id == "task-123"

    bad = UnitTrackerBinding(
        work_unit_id=unit.id,
        tracker_system="jira",
        external_item_id="x",
        projected_state="ready",
    )
    migrated_session.add(bad)
    with pytest.raises(IntegrityError):
        migrated_session.commit()
    migrated_session.rollback()
