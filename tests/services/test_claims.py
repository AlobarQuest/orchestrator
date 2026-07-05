import uuid
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.leases import LEASE_DURATION, hash_lease_token
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Claim
from orchestrator.services.claims import LeaseGrant, claim_unit, renew_claim
from orchestrator.services.lifecycle import ActorContext


def worker(actor_id: str = "worker-1") -> ActorContext:
    return ActorContext(actor_id, ActorRole.WORKER)


def test_claim_uses_database_time_and_stores_only_token_hash(
    migrated_session: Session, ready_unit
) -> None:
    before = migrated_session.scalar(select(text("transaction_timestamp()")))

    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")

    assert isinstance(result, LeaseGrant)
    assert before is not None
    claim = migrated_session.get(Claim, result.claim_id)
    assert claim is not None
    assert result.expires_at - before == LEASE_DURATION
    assert claim.lease_token_hash == hash_lease_token(result.lease_token)
    assert result.lease_token not in claim.lease_token_hash


def test_claim_replay_does_not_disclose_raw_token_twice(
    migrated_session: Session, ready_unit
) -> None:
    first = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    replay = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")

    assert isinstance(first, LeaseGrant)
    assert isinstance(replay, LeaseGrant)
    assert (replay.claim_id, replay.attempt, replay.expires_at) == (
        first.claim_id,
        first.attempt,
        first.expires_at,
    )
    assert replay.lease_token == ""


def test_claim_reused_key_with_different_actor_conflicts(
    migrated_session: Session, ready_unit
) -> None:
    claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")

    result = claim_unit(migrated_session, ready_unit.id, worker("worker-2"), "claim-1")

    assert isinstance(result, DomainError)
    assert result.code == "idempotency_conflict"


def test_only_current_owner_attempt_and_token_can_renew(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)

    for actor, attempt, token in (
        (worker("worker-2"), grant.attempt, grant.lease_token),
        (worker(), grant.attempt + 1, grant.lease_token),
        (worker(), grant.attempt, "wrong-token"),
    ):
        result = renew_claim(
            migrated_session,
            ready_unit.id,
            actor,
            attempt,
            token,
        )
        assert isinstance(result, DomainError)
        assert result.code == "claim_not_owned"

    renewed = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
    )
    assert isinstance(renewed, LeaseGrant)
    assert renewed.expires_at > grant.expires_at


def test_expired_claim_cannot_be_renewed(migrated_session: Session, ready_unit) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)
    migrated_session.execute(
        text("UPDATE claims SET lease_expires_at = transaction_timestamp() - interval '1 second'")
    )
    migrated_session.commit()

    result = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
    )

    assert isinstance(result, DomainError)
    assert result.code == "lease_expired"


def test_hash_lease_token_is_stable_and_one_way() -> None:
    token = str(uuid.uuid4())
    digest = hash_lease_token(token)

    assert digest == hash_lease_token(token)
    assert len(digest) == 64
    assert token not in digest
    assert LEASE_DURATION == timedelta(minutes=15)
