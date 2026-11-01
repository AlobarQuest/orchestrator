"""The execution-status read: per-unit facts for the execution alert lane."""

import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DispatchRecord,
    Event,
    SdsMember,
    SdsMemberGrant,
    WorkUnit,
)
from orchestrator.services.lifecycle.claims import LeaseGrant, claim_unit
from orchestrator.services.reporting.execution_status import execution_status
from tests.services.test_claims import worker
from tests.services.test_dispatch import PILOT_REPOSITORY, ready_unit
from tests.services.test_reclaim import expire

ROTATION_SNAPSHOT = {"profile_fields": {"standing": True, "credential_id": "cred-1"}}


def _record(session: Session, unit: WorkUnit, attempt: int, status: str) -> None:
    event = Event(
        actor_id="system",
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"es-{unit.id}-{attempt}:event",
    )
    session.add(event)
    session.flush()
    session.add(
        DispatchRecord(
            work_unit_id=unit.id,
            work_package_revision_id=unit.work_package_revision_id,
            runner_attempt=attempt,
            status=status,
            reason_code="github_api",
            idempotency_key=f"es-{unit.id}-{attempt}",
            target_repository=PILOT_REPOSITORY,
            workflow_id="w.yml",
            workflow_ref="main",
            failure_signature="sig" if status == "failed" else None,
            payload={},
            event_id=event.id,
        )
    )
    session.commit()


def _row(
    session: Session,
    unit: WorkUnit,
    *,
    execution_stall_grace_seconds: int = 900,
    dispatch_failure_signature_threshold: int = 3,
):
    rows = {
        row.unit_id: row
        for row in execution_status(
            session,
            stall_grace_seconds=execution_stall_grace_seconds,
            failure_signature_threshold=dispatch_failure_signature_threshold,
        )
    }
    return rows.get(unit.id)


@pytest.mark.parametrize("state", [WorkUnitState.COMPLETED, WorkUnitState.CANCELLED])
def test_a_completed_or_cancelled_unit_has_no_row(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = ready_unit(migrated_session, key="es-done")
    unit.state = state
    migrated_session.commit()

    assert _row(migrated_session, unit) is None


def test_a_live_unit_has_a_row_with_its_class_and_repository(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-live", change_class="app-change")

    row = _row(migrated_session, unit)

    assert row is not None
    assert (row.state, row.change_class, row.target_repository) == (
        "ready",
        "app-change",
        PILOT_REPOSITORY,
    )
    assert not (row.has_claim or row.lease_lapsed or row.breaker_open or row.is_rotation)


def test_lease_lapsed_is_true_only_past_grace(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-lapse")
    grant = claim_unit(migrated_session, unit.id, worker(), "es-lapse-claim")
    assert isinstance(grant, LeaseGrant)
    unit.state = WorkUnitState.EXECUTING
    migrated_session.commit()
    expire(migrated_session, grant.claim_id)

    inside = _row(migrated_session, unit, execution_stall_grace_seconds=3_600)
    past = _row(migrated_session, unit, execution_stall_grace_seconds=0)

    assert inside is not None and past is not None
    assert inside.has_claim and past.has_claim
    assert (inside.lease_lapsed, past.lease_lapsed) == (False, True)


def test_the_latest_dispatch_record_decides(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-latest")
    _record(migrated_session, unit, 1, "failed")
    _record(migrated_session, unit, 2, "dispatched")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.latest_dispatch_status == "dispatched"
    assert row.latest_dispatch_at is not None


def test_a_unit_never_dispatched_has_no_latest_record(migrated_session: Session) -> None:
    row = _row(migrated_session, ready_unit(migrated_session, key="es-none"))

    assert row is not None
    assert (row.latest_dispatch_status, row.latest_dispatch_at) == (None, None)


def test_breaker_open_follows_the_at_rest_failure_count(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-breaker")
    for attempt in (1, 2, 3):
        _record(migrated_session, unit, attempt, "failed")

    open_row = _row(migrated_session, unit, dispatch_failure_signature_threshold=3)
    closed_row = _row(migrated_session, unit, dispatch_failure_signature_threshold=4)

    assert open_row is not None and closed_row is not None
    assert (open_row.breaker_open, closed_row.breaker_open) == (True, False)


@pytest.mark.parametrize(
    ("grant_state", "member_state", "expected"),
    [("granted", "member", True), ("withdrawn", "member", False), ("granted", "retired", False)],
)
def test_granted_reads_the_member_record(
    migrated_session: Session, grant_state: str, member_state: str, expected: bool
) -> None:
    unit = ready_unit(migrated_session, key="es-grant", change_class="app-change")
    member = SdsMember(
        repository=PILOT_REPOSITORY.upper(),
        owner="devon",
        state=member_state,
        state_changed_by="human:devon",
        state_reason="seed",
    )
    migrated_session.add(member)
    migrated_session.flush()
    migrated_session.add(
        SdsMemberGrant(
            member_id=member.id,
            service="app_changes",
            state=grant_state,
            seeded=False,
            set_by="human:devon",
            reason="seed",
        )
    )
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.granted is expected


def test_granted_is_false_with_no_member(migrated_session: Session) -> None:
    row = _row(migrated_session, ready_unit(migrated_session, key="es-nomember"))

    assert row is not None
    assert row.granted is False


def test_a_rotation_unit_says_so(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-rot", enforcement_snapshot=ROTATION_SNAPSHOT)
    unit.required_capability = "operational_action"
    migrated_session.commit()
    plain = ready_unit(migrated_session, key="es-plain")

    rotation_row = _row(migrated_session, unit)
    plain_row = _row(migrated_session, plain)

    assert rotation_row is not None and plain_row is not None
    assert rotation_row.standing is True
    assert plain_row.standing is False
