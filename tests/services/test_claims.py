import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import DEFAULT_LEASE, hash_lease_token
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Claim, ContextSnapshot
from orchestrator.services.claims import LeaseGrant, claim_unit, release_claim, renew_claim
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_context_preflight import register_context_unit, valid_context


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
    # This unit's package declares no reach, so it gets the build's default hold. Which hold a
    # declared reach gets, and that every grant site reads the same source, is test_lease_policy.
    assert result.expires_at - before == DEFAULT_LEASE
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


def test_claim_rejects_stale_expected_version(migrated_session: Session, ready_unit) -> None:
    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1", expected_version=0)

    assert isinstance(result, DomainError)
    assert result.code == "version_conflict"
    assert result.current_state == "ready"
    assert result.current_version == 1


def test_claim_replay_binds_expected_version(migrated_session: Session, ready_unit) -> None:
    first = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1", expected_version=1)
    assert isinstance(first, LeaseGrant)

    replay = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1", expected_version=2)

    assert isinstance(replay, DomainError)
    assert replay.code == "idempotency_conflict"


def test_claim_required_context_unit_rejects_missing_context(migrated_session: Session) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "claim-context-missing")

    result = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")

    assert isinstance(result, DomainError)
    assert result.code == "context_missing_required"


def test_claim_required_context_unit_stores_context_snapshot(migrated_session: Session) -> None:
    ready_unit = register_context_unit(migrated_session, valid_context(), "claim-context-stored")

    result = claim_unit(
        migrated_session,
        ready_unit.id,
        worker(),
        "claim-1",
        standing_context=valid_context(),
    )

    assert isinstance(result, LeaseGrant)
    assert result.context_snapshot_id is not None
    claim = migrated_session.get(Claim, result.claim_id)
    assert claim is not None
    assert claim.context_snapshot_id == result.context_snapshot_id
    snapshot = migrated_session.get(ContextSnapshot, result.context_snapshot_id)
    assert snapshot is not None
    assert snapshot.work_unit_id == ready_unit.id
    assert snapshot.claim_id is None
    assert snapshot.attempt == result.attempt


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
    assert renewed.lease_token == ""


def test_renew_replay_does_not_extend_twice(migrated_session: Session, ready_unit) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)

    first = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="renew-1",
        expected_version=2,
    )
    replay = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="renew-1",
        expected_version=2,
    )

    assert isinstance(first, LeaseGrant)
    assert isinstance(replay, LeaseGrant)
    assert replay.expires_at == first.expires_at


def test_renew_reused_key_with_different_request_conflicts(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-1")
    assert isinstance(grant, LeaseGrant)
    first = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="renew-1",
        expected_version=2,
    )
    assert isinstance(first, LeaseGrant)

    result = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="renew-1",
        expected_version=1,
    )

    assert isinstance(result, DomainError)
    assert result.code == "idempotency_conflict"


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
    assert DEFAULT_LEASE == timedelta(minutes=15)


def test_release_claim_is_the_single_writer_of_released_at_and_terminal_reason(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-1")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.released_at is None
    assert claim.terminal_reason is None

    now = TransactionClock().now(migrated_session)
    release_claim(claim, terminal_reason="lease_expired", released_at=now)
    migrated_session.flush()

    assert claim.released_at == now
    assert claim.terminal_reason == "lease_expired"


def test_release_claim_rejects_a_blank_terminal_reason(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-2")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None

    with pytest.raises(DomainError) as error:
        release_claim(
            claim, terminal_reason="", released_at=TransactionClock().now(migrated_session)
        )

    assert error.value.code == "terminal_reason_required"


def test_release_claim_refuses_an_already_released_claim(
    migrated_session: Session, ready_unit
) -> None:
    """A silent double-release would rewrite the terminal reason. Having one writer is exactly
    what prevents evidence recovery and reclaim from racing to release the same claim."""
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-3")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    now = TransactionClock().now(migrated_session)
    release_claim(claim, terminal_reason="lease_expired", released_at=now)

    with pytest.raises(DomainError) as error:
        release_claim(claim, terminal_reason="recovered_from_expired_lease", released_at=now)

    assert error.value.code == "claim_already_released"
    assert claim.terminal_reason == "lease_expired"
