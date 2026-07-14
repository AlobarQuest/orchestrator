from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.lifecycle import ActorContext, transition_unit
from tests.services.test_lifecycle_events import command_for, worker_command


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


def test_event_failure_rolls_back_terminal_claim_release(
    migrated_session: Session,
    ready_unit,
) -> None:
    grant = claim_unit(
        migrated_session,
        ready_unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        "claim-before-failed-event",
    )
    assert isinstance(grant, LeaseGrant)
    command = worker_command(
        ready_unit,
        grant,
        WorkUnitState.FAILED,
        idempotency_key="failed-transition-event",
    )
    engine = migrated_session.get_bind()
    claim_during_transition = migrated_session.get(Claim, grant.claim_id)
    assert claim_during_transition is not None
    release_observed = False

    def fail_event_insert(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal release_observed
        if statement.startswith("INSERT INTO events"):
            release_observed = claim_during_transition.released_at is not None
            raise IntegrityError(statement, {}, RuntimeError("injected event failure"))

    event.listen(engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(IntegrityError):
            transition_unit(migrated_session, command)
    finally:
        event.remove(engine, "before_cursor_execute", fail_event_insert)

    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert release_observed
    assert unit is not None
    assert claim is not None
    assert (unit.state, unit.version) == (WorkUnitState.CLAIMED, 2)
    assert (claim.released_at, claim.terminal_reason) == (None, None)
