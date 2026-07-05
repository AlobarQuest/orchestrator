from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.lifecycle import transition_unit
from tests.services.test_lifecycle_events import command_for


def test_event_failure_rolls_back_state(migrated_session: Session, ready_unit) -> None:
    engine = migrated_session.get_bind()

    def fail_event_insert(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO events"):
            raise IntegrityError(statement, {}, RuntimeError("injected event failure"))

    event.listen(engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(IntegrityError):
            transition_unit(migrated_session, command_for(ready_unit))
    finally:
        event.remove(engine, "before_cursor_execute", fail_event_insert)

    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None
    assert (unit.state, unit.version) == (WorkUnitState.READY, 1)
