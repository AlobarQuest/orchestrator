"""WS-P2.7 Task 1: the `unit_tracker_bindings` row persists and its vocabulary is closed.

WS-P2.7 Task 2 (below): the `tracker_bindings` service -- SYSTEM-only upsert/get/list, and every
validation fails closed via `DomainError` rather than letting the DB CHECK raise.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitTrackerBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.tracker_bindings import (
    list_tracker_bindings,
    upsert_tracker_binding,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)


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
    assert reread is not None
    assert reread.tracker_system == "todoist"
    assert reread.external_item_id == "task-123"

    other = register_unit(migrated_session, "tracker-binding-2")
    bad = UnitTrackerBinding(
        work_unit_id=other.id,
        tracker_system="jira",
        external_item_id="x",
        projected_state="ready",
    )
    migrated_session.add(bad)
    with pytest.raises(IntegrityError):
        migrated_session.commit()
    migrated_session.rollback()


def test_system_upsert_creates_then_updates_one_row_and_persists(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "tracker-binding-upsert")
    upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )
    migrated_session.expire_all()
    row = migrated_session.get(UnitTrackerBinding, unit.id)
    assert row is not None
    assert row.external_item_id == "task-1"
    assert row.projected_state == "ready"

    upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url="https://todoist/app/task/task-1",
        projected_state="completed",
    )
    migrated_session.expire_all()
    rows = list_tracker_bindings(migrated_session)
    assert len(rows) == 1
    assert rows[0].projected_state == "completed"
    assert rows[0].external_url == "https://todoist/app/task/task-1"


def test_non_system_actor_is_forbidden(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "tracker-binding-role")
    with pytest.raises(DomainError) as error:
        upsert_tracker_binding(
            migrated_session,
            actor=WORKER,
            work_unit_id=unit.id,
            tracker_system="todoist",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert error.value.code == "role_forbidden"


def test_unsupported_tracker_system_raises_domain_error_not_integrity(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "tracker-binding-bad-system")
    with pytest.raises(DomainError) as error:
        upsert_tracker_binding(
            migrated_session,
            actor=SYSTEM,
            work_unit_id=unit.id,
            tracker_system="jira",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert error.value.code == "tracker_system_unsupported"


def test_empty_item_id_raises_domain_error(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "tracker-binding-empty-item")
    with pytest.raises(DomainError) as error:
        upsert_tracker_binding(
            migrated_session,
            actor=SYSTEM,
            work_unit_id=unit.id,
            tracker_system="todoist",
            external_item_id="",
            external_url=None,
            projected_state="ready",
        )
    assert error.value.code == "tracker_item_id_required"


def test_missing_work_unit_raises_not_found(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        upsert_tracker_binding(
            migrated_session,
            actor=SYSTEM,
            work_unit_id=uuid.uuid4(),
            tracker_system="todoist",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert error.value.code == "work_unit_not_found"


def test_upsert_does_not_change_unit_state(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "tracker-binding-state")
    before = unit.state
    upsert_tracker_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )
    migrated_session.expire_all()
    unit_after = migrated_session.get(WorkUnit, unit.id)
    assert unit_after is not None
    assert unit_after.state == before
