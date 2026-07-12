"""WS-P2.1 Task 10: the dead-letter view (AC-005).

Terminal failures must be VISIBLE. The view is derived live from the source tables -- there is no
materialized dead-letter queue to drift out of sync with reality.
"""

import uuid

from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import DispatchRecord, Event, WorkUnit
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.dispatch import (
    circuit_open,
    failure_signature,
    signature_failure_count,
)
from tests.services.test_dependencies import register_unit

# Long enough that nothing in these fixtures is stale; the stalled-approval
# report has its own tests.
STALLED_APPROVAL_SECONDS = 604_800

SIGNATURE = failure_signature("workflow_dispatch", "github_api", "status:500")
THRESHOLD = 3


def _fail_dispatch(
    session: Session, unit: WorkUnit, attempt: int, status: str, *, signature: str = SIGNATURE
) -> DispatchRecord:
    event = Event(
        actor_id="system",
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"dl-dispatch-{unit.id}-{attempt}:event",
    )
    session.add(event)
    session.flush()
    record = DispatchRecord(
        work_unit_id=unit.id,
        work_package_revision_id=unit.work_package_revision_id,
        runner_attempt=attempt,
        status=status,
        reason_code="github_api",
        idempotency_key=f"dl-dispatch-{unit.id}-{attempt}",
        target_repository="AlobarQuest/orchestrator",
        workflow_id="factory-runner-pilot.yml",
        workflow_ref="main",
        failure_signature=signature,
        payload={},
        event_id=event.id,
    )
    session.add(record)
    session.flush()
    return record


def test_the_at_rest_breaker_is_the_prospective_one_minus_exactly_one_failure(
    migrated_session: Session,
) -> None:
    """THE reason Task 2 split the predicate.

    Dispatch counts the failure it is ABOUT to write; the view counts what is already on disk.
    Reusing dispatch's call site here would show a breaker open one failure early -- reporting a
    unit as circuit-broken when it still has a dispatch left.
    """
    unit = register_unit(migrated_session, "dl-breaker")
    unit.state = WorkUnitState.FAILED
    for attempt in (1, 2):
        _fail_dispatch(migrated_session, unit, attempt, "failed")
    migrated_session.commit()

    count = signature_failure_count(migrated_session, unit.id, SIGNATURE)
    assert count == THRESHOLD - 1
    assert circuit_open(count + 1, THRESHOLD) is True  # prospective: dispatch would block
    assert circuit_open(count, THRESHOLD) is False  # at rest: not open yet

    breakers = [
        entry
        for entry in dead_letter(
            migrated_session,
            failure_signature_threshold=THRESHOLD,
            stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
        )
        if entry.source == "circuit_breaker"
    ]
    assert breakers == []

    _fail_dispatch(migrated_session, unit, 3, "blocked")
    migrated_session.commit()

    breakers = [
        entry
        for entry in dead_letter(
            migrated_session,
            failure_signature_threshold=THRESHOLD,
            stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
        )
        if entry.source == "circuit_breaker"
    ]
    assert [(entry.work_unit_id, entry.reason_code, entry.detail) for entry in breakers] == [
        (unit.id, "failure_signature_circuit_open", SIGNATURE)
    ]


def test_terminal_and_blocked_units_are_enumerated(migrated_session: Session) -> None:
    failed = register_unit(migrated_session, "dl-failed")
    failed.state = WorkUnitState.FAILED
    blocked = register_unit(migrated_session, "dl-blocked")
    blocked.state = WorkUnitState.BLOCKED
    cancelled = register_unit(migrated_session, "dl-cancelled")
    cancelled.state = WorkUnitState.CANCELLED
    healthy = register_unit(migrated_session, "dl-healthy")
    healthy.state = WorkUnitState.READY
    migrated_session.commit()

    entries = dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
    )

    units = {entry.work_unit_id: entry for entry in entries if entry.source == "work_unit"}
    assert set(units) == {failed.id, blocked.id, cancelled.id}
    # `blocked` is in the view because requeue TARGETS it -- an action whose subject is invisible
    # in the surface it is offered from is not an operator affordance.
    assert units[blocked.id].requeue_eligible is True
    assert units[cancelled.id].requeue_eligible is False


def test_failed_and_blocked_dispatch_records_are_enumerated(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "dl-dispatch")
    unit.state = WorkUnitState.FAILED
    _fail_dispatch(migrated_session, unit, 1, "failed")
    _fail_dispatch(migrated_session, unit, 2, "blocked")
    _fail_dispatch(migrated_session, unit, 3, "dispatched")  # not a failure
    migrated_session.commit()

    entries = dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
    )

    dispatches = [entry for entry in entries if entry.source == "dispatch_record"]
    assert len(dispatches) == 2
    assert all(entry.reason_code == "github_api" for entry in dispatches)


def test_requeue_eligibility_reflects_the_attempt_budget(migrated_session: Session) -> None:
    """An exhausted unit is NOT requeue-eligible: requeue would land it READY where claim_unit
    rejects it, and it would vanish from this very view -- invisible and unrunnable."""
    exhausted = register_unit(migrated_session, "dl-exhausted")
    exhausted.state = WorkUnitState.FAILED
    exhausted.attempt_count = exhausted.max_attempts
    spare = register_unit(migrated_session, "dl-spare")
    spare.state = WorkUnitState.FAILED
    spare.attempt_count = 1
    migrated_session.commit()

    entries = {
        entry.work_unit_id: entry
        for entry in dead_letter(
            migrated_session,
            failure_signature_threshold=THRESHOLD,
            stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
        )
        if entry.source == "work_unit"
    }

    assert entries[exhausted.id].requeue_eligible is False
    assert entries[spare.id].requeue_eligible is True


def test_the_view_is_read_only(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "dl-readonly")
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()
    before = (unit.state, unit.version)

    dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
    )

    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == before


def test_a_clean_database_yields_an_empty_view(migrated_session: Session) -> None:
    assert (
        dead_letter(
            migrated_session,
            failure_signature_threshold=THRESHOLD,
            stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
        )
        == ()
    )
