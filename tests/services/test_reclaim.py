import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Approval, Event, WorkUnit
from orchestrator.services.claims import (
    LeaseGrant,
    authorize_retry,
    claim_unit,
    reclaim_expired_claim,
    renew_claim,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_claims import worker

SYSTEM = ActorContext("lease-reaper", ActorRole.SYSTEM)


def authorize_readiness(session: Session, unit: WorkUnit) -> None:
    approval = Approval(
        subject_type="authority",
        subject_id=unit.id,
        subject_revision_or_fingerprint=unit.authority_fingerprint,
        decision="approved",
        approved_by="human-1",
        reason="approved authority",
        event_id=uuid.uuid4(),
        idempotency_key=f"authority-{unit.id}",
    )
    session.add(approval)
    session.flush()
    unit.authority_approval_id = approval.id
    session.commit()


def expire(session: Session, claim_id: uuid.UUID) -> None:
    session.execute(
        text(
            "UPDATE claims SET lease_expires_at = "
            "transaction_timestamp() - interval '1 second' WHERE id = :claim_id"
        ),
        {"claim_id": claim_id},
    )
    session.commit()


def test_reclaim_records_three_transitions_and_invalidates_old_token(
    migrated_session: Session, ready_unit
) -> None:
    authorize_readiness(migrated_session, ready_unit)
    first = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(first, LeaseGrant)
    expire(migrated_session, first.claim_id)

    second = reclaim_expired_claim(
        migrated_session,
        ready_unit.id,
        SYSTEM,
        worker("worker-2"),
        "claim-2",
    )

    assert isinstance(second, LeaseGrant)
    assert second.attempt == 2
    events = sorted(
        migrated_session.scalars(select(Event).where(Event.subject_id == ready_unit.id)),
        key=lambda event: event.payload["version"],
    )
    assert [(event.from_state, event.to_state) for event in events[-3:]] == [
        ("claimed", "failed"),
        ("failed", "ready"),
        ("ready", "claimed"),
    ]
    stale = renew_claim(migrated_session, ready_unit.id, worker(), first.attempt, first.lease_token)
    assert isinstance(stale, DomainError)
    assert stale.code == "claim_not_owned"


def test_reclaim_from_executing_records_honest_failure(
    migrated_session: Session, ready_unit
) -> None:
    authorize_readiness(migrated_session, ready_unit)
    first = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(first, LeaseGrant)
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None
    unit.state = WorkUnitState.EXECUTING
    migrated_session.commit()
    expire(migrated_session, first.claim_id)

    result = reclaim_expired_claim(
        migrated_session, ready_unit.id, SYSTEM, worker("worker-2"), "claim-2"
    )

    assert isinstance(result, LeaseGrant)
    failed = migrated_session.scalar(
        select(Event)
        .where(Event.subject_id == ready_unit.id, Event.to_state == WorkUnitState.FAILED)
        .order_by(Event.occurred_at.desc())
    )
    assert failed is not None
    assert failed.from_state == WorkUnitState.EXECUTING


def test_third_expired_attempt_exhausts_budget(migrated_session: Session, ready_unit) -> None:
    authorize_readiness(migrated_session, ready_unit)
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)
    for attempt in (2, 3):
        expire(migrated_session, grant.claim_id)
        result = reclaim_expired_claim(
            migrated_session,
            ready_unit.id,
            SYSTEM,
            worker(f"worker-{attempt}"),
            f"claim-{attempt}",
        )
        assert isinstance(result, LeaseGrant)
        grant = result

    expire(migrated_session, grant.claim_id)
    exhausted = reclaim_expired_claim(
        migrated_session, ready_unit.id, SYSTEM, worker("worker-4"), "claim-4"
    )

    assert isinstance(exhausted, DomainError)
    assert exhausted.code == "attempts_exhausted"
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None
    assert (unit.state, unit.attempt_count) == (WorkUnitState.FAILED, 3)

    replay = reclaim_expired_claim(
        migrated_session, ready_unit.id, SYSTEM, worker("worker-4"), "claim-4"
    )
    assert isinstance(replay, DomainError)
    assert replay.code == "attempts_exhausted"

    conflict = reclaim_expired_claim(
        migrated_session, ready_unit.id, SYSTEM, worker("worker-5"), "claim-4"
    )
    assert isinstance(conflict, DomainError)
    assert conflict.code == "idempotency_conflict"


def test_retry_after_exhaustion_requires_human_and_new_budget(
    migrated_session: Session, ready_unit
) -> None:
    ready_unit.state = WorkUnitState.FAILED
    ready_unit.attempt_count = ready_unit.max_attempts = 3
    migrated_session.commit()

    denied = authorize_retry(
        migrated_session,
        ready_unit.id,
        worker(),
        new_max_attempts=4,
        reason="investigated",
        idempotency_key="retry-1",
    )
    assert isinstance(denied, DomainError)
    assert denied.code == "human_actor_required"

    approved = authorize_retry(
        migrated_session,
        ready_unit.id,
        ActorContext("human-1", ActorRole.HUMAN),
        new_max_attempts=4,
        reason="investigated",
        idempotency_key="retry-1",
    )

    assert isinstance(approved, Approval)
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None
    assert (unit.state, unit.max_attempts) == (WorkUnitState.READY, 4)
    assert approved.subject_revision_or_fingerprint == "1"

    replay = authorize_retry(
        migrated_session,
        ready_unit.id,
        ActorContext("human-1", ActorRole.HUMAN),
        new_max_attempts=4,
        reason="investigated",
        idempotency_key="retry-1",
    )
    assert isinstance(replay, Approval)
    assert replay.id == approved.id

    conflict = authorize_retry(
        migrated_session,
        ready_unit.id,
        ActorContext("human-1", ActorRole.HUMAN),
        new_max_attempts=5,
        reason="investigated",
        idempotency_key="retry-1",
    )
    assert isinstance(conflict, DomainError)
    assert conflict.code == "idempotency_conflict"
