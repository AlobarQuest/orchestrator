"""AC-007 asymmetry #2: reclaim's COMPOUND keys.

Reclaim is the trickiest replay surface in the repo: one call writes THREE events under TWO
derived keys -- `{key}:failed` (the SYSTEM-fail), `{key}:ready` (the re-ready) -- plus the bare
`{key}` for the new claim. A duplicate delivery must replay all three without writing a second of
any, and a duplicate delivery of a REJECTED reclaim must replay the same error rather than 500.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event, WorkUnit
from orchestrator.services.claims import (
    LeaseGrant,
    claim_unit,
    reclaim_expired_claim,
    recover_expired_claim,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_claims import worker
from tests.services.test_dependencies import register_unit
from tests.services.test_reclaim import authorize_readiness, expire

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
NEXT_OWNER = ActorContext("worker-2", ActorRole.WORKER)
KEY = "reclaim-double-submit"


def _expired_unit(session: Session, key: str, *, max_attempts: int = 5):
    unit = register_unit(session, key)
    authorize_readiness(session, unit)
    unit.state = WorkUnitState.READY
    unit.max_attempts = max_attempts
    session.commit()
    grant = claim_unit(session, unit.id, worker(), f"{key}-claim")
    assert isinstance(grant, LeaseGrant)
    expire(session, grant.claim_id)
    session.expire_all()
    return unit


def _count(session: Session, model: type, key: str) -> int:
    return (
        session.scalar(select(func.count()).select_from(model).where(model.idempotency_key == key))
        or 0
    )


def test_a_duplicate_reclaim_writes_one_failed_and_one_ready_event(
    migrated_session: Session,
) -> None:
    unit = _expired_unit(migrated_session, "idem-reclaim")

    one = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT_OWNER, KEY)
    two = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT_OWNER, KEY)

    assert isinstance(one, LeaseGrant)
    assert isinstance(two, LeaseGrant)
    # Identical response -- EXCEPT the lease token, which a replay deliberately withholds.
    assert (one.claim_id, one.attempt, one.expires_at) == (
        two.claim_id,
        two.attempt,
        two.expires_at,
    )
    assert one.lease_token != ""
    assert two.lease_token == ""

    assert _count(migrated_session, Event, f"{KEY}:failed") == 1
    assert _count(migrated_session, Event, f"{KEY}:ready") == 1
    assert _count(migrated_session, Claim, KEY) == 1
    migrated_session.expire_all()
    refreshed = migrated_session.get(type(unit), unit.id)
    assert refreshed is not None
    assert refreshed.attempt_count == 2


def test_a_duplicate_rejected_reclaim_replays_the_same_error(
    migrated_session: Session,
) -> None:
    """The `:failed` event is the ONLY record of a refusal. The compound key is what makes a
    duplicate delivery of a REJECTED reclaim replay the same error instead of raising."""
    unit = _expired_unit(migrated_session, "idem-reclaim-exhausted", max_attempts=1)

    one = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT_OWNER, "reclaim-x")
    two = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT_OWNER, "reclaim-x")

    assert isinstance(one, DomainError)
    assert isinstance(two, DomainError)
    assert one.code == two.code == "attempts_exhausted"
    assert _count(migrated_session, Event, "reclaim-x:failed") == 1
    assert _count(migrated_session, Event, "reclaim-x:ready") == 0
    assert uuid.UUID(str(unit.id))  # sanity: the unit still exists


def test_a_duplicate_expired_claim_recovery_writes_each_transition_once(
    migrated_session: Session,
) -> None:
    unit = _expired_unit(migrated_session, "idem-recover")
    version = unit.version

    one = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)
    two = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)

    assert isinstance(one, WorkUnit) and isinstance(two, WorkUnit)
    assert one.version == two.version
    assert _count(migrated_session, Event, f"{KEY}:failed") == 1
    assert _count(migrated_session, Event, KEY) == 1
    assert _count(migrated_session, Claim, KEY) == 0


def test_expired_claim_recovery_reused_key_with_a_different_version_conflicts(
    migrated_session: Session,
) -> None:
    unit = _expired_unit(migrated_session, "idem-recover-version")
    version = unit.version
    first = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)
    assert isinstance(first, WorkUnit)

    replay = recover_expired_claim(
        migrated_session, unit.id, SYSTEM, KEY, expected_version=version + 1
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "idempotency_conflict"


def test_expired_claim_recovery_reused_key_with_a_different_actor_conflicts(
    migrated_session: Session,
) -> None:
    unit = _expired_unit(migrated_session, "idem-recover-actor")
    version = unit.version
    first = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)
    assert isinstance(first, WorkUnit)

    replay = recover_expired_claim(
        migrated_session,
        unit.id,
        ActorContext("another-system", ActorRole.SYSTEM),
        KEY,
        expected_version=version,
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "idempotency_conflict"
