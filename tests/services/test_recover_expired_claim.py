import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit, recover_expired_claim
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_claims import worker
from tests.services.test_reclaim import authorize_readiness, expire

SYSTEM = ActorContext("lease-reaper", ActorRole.SYSTEM)
WORKER = ActorContext("worker-2", ActorRole.WORKER)


def _claimed_unit(session: Session, ready_unit: WorkUnit, key: str) -> tuple[WorkUnit, Claim]:
    authorize_readiness(session, ready_unit)
    grant = claim_unit(session, ready_unit.id, worker(), f"{key}-claim")
    assert isinstance(grant, LeaseGrant)
    claim = session.get(Claim, grant.claim_id)
    assert claim is not None
    return ready_unit, claim


@pytest.mark.parametrize("source", [WorkUnitState.CLAIMED, WorkUnitState.EXECUTING])
def test_recover_expired_claim_moves_active_work_to_ready_without_a_new_claim(
    migrated_session: Session,
    ready_unit: WorkUnit,
    source: WorkUnitState,
) -> None:
    unit, claim = _claimed_unit(migrated_session, ready_unit, source.value)
    unit.state = source
    migrated_session.commit()
    expire(migrated_session, claim.id)
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, unit.id)
    assert unit is not None

    result = recover_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        "recover-expired-1",
        expected_version=unit.version,
    )

    assert isinstance(result, WorkUnit)
    assert result.state == WorkUnitState.READY
    assert result.attempt_count == 1
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Claim).where(Claim.work_unit_id == unit.id)
        )
        == 1
    )
    migrated_session.expire_all()
    old_claim = migrated_session.get(Claim, claim.id)
    assert old_claim is not None
    assert old_claim.released_at is not None
    assert old_claim.terminal_reason == "lease_expired"
    failed_event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == "recover-expired-1:failed")
    )
    ready_event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == "recover-expired-1")
    )
    assert failed_event is not None and ready_event is not None
    assert (failed_event.from_state, failed_event.to_state) == (source, WorkUnitState.FAILED)
    assert (ready_event.from_state, ready_event.to_state) == (
        WorkUnitState.FAILED,
        WorkUnitState.READY,
    )
    assert failed_event.correlation_id == ready_event.correlation_id


def test_recover_expired_claim_rejects_a_live_lease(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    unit, claim = _claimed_unit(migrated_session, ready_unit, "recover-live")

    result = recover_expired_claim(
        migrated_session, unit.id, SYSTEM, "recover-live", expected_version=unit.version
    )

    assert isinstance(result, DomainError)
    assert result.code == "lease_not_expired"
    migrated_session.expire_all()
    persisted_claim = migrated_session.get(Claim, claim.id)
    assert persisted_claim is not None and persisted_claim.released_at is None


def test_recover_expired_claim_is_system_only(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    unit, claim = _claimed_unit(migrated_session, ready_unit, "recover-role")
    expire(migrated_session, claim.id)

    result = recover_expired_claim(
        migrated_session, unit.id, WORKER, "recover-role", expected_version=unit.version
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"
    migrated_session.expire_all()
    persisted_claim = migrated_session.get(Claim, claim.id)
    assert persisted_claim is not None and persisted_claim.released_at is None


def test_recover_expired_claim_persists_failed_release_when_budget_is_exhausted(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    ready_unit.max_attempts = 1
    migrated_session.commit()
    unit, claim = _claimed_unit(migrated_session, ready_unit, "recover-exhausted")
    expire(migrated_session, claim.id)

    result = recover_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        "recover-exhausted",
        expected_version=unit.version,
    )

    assert isinstance(result, DomainError)
    assert result.code == "attempts_exhausted"
    migrated_session.expire_all()
    persisted_unit = migrated_session.get(WorkUnit, unit.id)
    persisted_claim = migrated_session.get(Claim, claim.id)
    assert persisted_unit is not None and persisted_unit.state == WorkUnitState.FAILED
    assert persisted_claim is not None and persisted_claim.released_at is not None
    assert persisted_claim.terminal_reason == "lease_expired"
    failed_event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == "recover-exhausted:failed")
    )
    assert failed_event is not None
    assert failed_event.payload["result_error_code"] == "attempts_exhausted"


def test_recover_expired_claim_persists_failed_release_when_readiness_is_unsatisfied(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    unit, claim = _claimed_unit(migrated_session, ready_unit, "recover-unready")
    unit.authority_approval_id = None
    migrated_session.commit()
    expire(migrated_session, claim.id)

    result = recover_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        "recover-unready",
        expected_version=unit.version,
    )

    assert isinstance(result, DomainError)
    assert result.code == "readiness_not_satisfied"
    migrated_session.expire_all()
    persisted_unit = migrated_session.get(WorkUnit, unit.id)
    persisted_claim = migrated_session.get(Claim, claim.id)
    assert persisted_unit is not None and persisted_unit.state == WorkUnitState.FAILED
    assert persisted_claim is not None and persisted_claim.released_at is not None
    assert persisted_claim.terminal_reason == "lease_expired"
    failed_event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == "recover-unready:failed")
    )
    assert failed_event is not None
    assert failed_event.payload["result_error_code"] == "readiness_not_satisfied"
