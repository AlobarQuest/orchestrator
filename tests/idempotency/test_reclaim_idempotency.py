"""AC-007 asymmetry #2: reclaim's COMPOUND keys.

Reclaim is the trickiest replay surface in the repo: one call writes THREE events under TWO
derived keys -- `{key}:failed` (the SYSTEM-fail), `{key}:ready` (the re-ready) -- plus the bare
`{key}` for the new claim. A duplicate delivery must replay all three without writing a second of
any, and a duplicate delivery of a REJECTED reclaim must replay the same error rather than 500.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
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


def test_expired_claim_recovery_reused_key_for_a_different_unit_conflicts_without_mutation(
    migrated_session: Session,
) -> None:
    winner = _expired_unit(migrated_session, "idem-recover-first-unit")
    loser = _expired_unit(migrated_session, "idem-recover-second-unit")
    winner_version, loser_version = winner.version, loser.version

    first = recover_expired_claim(
        migrated_session, winner.id, SYSTEM, KEY, expected_version=winner_version
    )
    second = recover_expired_claim(
        migrated_session, loser.id, SYSTEM, KEY, expected_version=loser_version
    )

    assert isinstance(first, WorkUnit)
    assert isinstance(second, DomainError)
    assert second.code == "idempotency_conflict"
    migrated_session.expire_all()
    persisted_loser = migrated_session.get(WorkUnit, loser.id)
    loser_claim = migrated_session.scalar(select(Claim).where(Claim.work_unit_id == loser.id))
    assert persisted_loser is not None
    assert (persisted_loser.state, persisted_loser.version) == (
        WorkUnitState.CLAIMED,
        loser_version,
    )
    assert loser_claim is not None and loser_claim.released_at is None
    assert loser_claim.terminal_reason is None
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.subject_id == loser.id,
                Event.idempotency_key.in_((KEY, f"{KEY}:failed")),
            )
        )
        == 0
    )


def test_concurrent_expired_claim_recovery_reused_key_for_different_units_is_stable(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as setup:
        first = _expired_unit(setup, "idem-recover-concurrent-first")
        second = _expired_unit(setup, "idem-recover-concurrent-second")
        requests = ((first.id, first.version), (second.id, second.version))

    start = Barrier(2)
    before_serialization_point = Barrier(2)
    synchronized_connections: set[int] = set()

    def synchronize_first_serialization_point(
        connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        connection_id = id(connection)
        if connection_id in synchronized_connections:
            return
        if "pg_advisory_xact_lock" in statement or "FROM events" in statement:
            synchronized_connections.add(connection_id)
            before_serialization_point.wait(timeout=5)

    event.listen(
        migrated_engine,
        "before_cursor_execute",
        synchronize_first_serialization_point,
    )

    def recover(request: tuple[uuid.UUID, int]) -> tuple[str, uuid.UUID | str]:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            result = recover_expired_claim(
                session,
                request[0],
                SYSTEM,
                KEY,
                expected_version=request[1],
            )
            if isinstance(result, WorkUnit):
                return ("success", result.id)
            assert isinstance(result, DomainError)
            return ("error", result.code)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(recover, request) for request in requests)
            results = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(
            migrated_engine,
            "before_cursor_execute",
            synchronize_first_serialization_point,
        )

    successes = tuple(value for kind, value in results if kind == "success")
    errors = tuple(value for kind, value in results if kind == "error")
    assert len(successes) == 1
    assert errors == ("idempotency_conflict",)
    winning_unit_id = successes[0]
    assert isinstance(winning_unit_id, uuid.UUID)
    losing_unit_id = next(unit_id for unit_id, _version in requests if unit_id != winning_unit_id)
    losing_version = next(version for unit_id, version in requests if unit_id == losing_unit_id)

    with Session(migrated_engine) as session:
        losing_unit = session.get(WorkUnit, losing_unit_id)
        losing_claim = session.scalar(select(Claim).where(Claim.work_unit_id == losing_unit_id))
        assert losing_unit is not None
        assert (losing_unit.state, losing_unit.version) == (
            WorkUnitState.CLAIMED,
            losing_version,
        )
        assert losing_claim is not None and losing_claim.released_at is None
        assert losing_claim.terminal_reason is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.subject_id == losing_unit_id,
                    Event.idempotency_key.in_((KEY, f"{KEY}:failed")),
                )
            )
            == 0
        )


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


def test_expired_claim_recovery_replay_fails_closed_after_later_state_advancement(
    migrated_session: Session,
) -> None:
    unit = _expired_unit(migrated_session, "idem-recover-advanced")
    version = unit.version
    recovered = recover_expired_claim(
        migrated_session, unit.id, SYSTEM, KEY, expected_version=version
    )
    assert isinstance(recovered, WorkUnit)
    advanced = claim_unit(
        migrated_session,
        unit.id,
        worker("post-recovery-worker"),
        "post-recovery-claim",
    )
    assert isinstance(advanced, LeaseGrant)

    replay = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)

    assert isinstance(replay, DomainError)
    assert replay.code == "idempotency_conflict"


@pytest.mark.parametrize(
    ("event_key", "attribute", "collision_value"),
    [
        (f"{KEY}:failed", "action", "claim.renewed"),
        (f"{KEY}:failed", "subject_type", "claim"),
        (f"{KEY}:failed", "actor_id", "different-system"),
        (KEY, "action", "claim.renewed"),
        (KEY, "subject_type", "claim"),
        (KEY, "actor_id", "different-system"),
    ],
)
def test_expired_claim_recovery_rejects_event_identity_collisions(
    migrated_session: Session,
    event_key: str,
    attribute: str,
    collision_value: str,
) -> None:
    unit = _expired_unit(migrated_session, f"idem-recover-collision-{attribute}-{event_key}")
    version = unit.version
    correlation_id = uuid.uuid4()
    failed_event = Event(
        occurred_at=TransactionClock().now(migrated_session),
        actor_id=SYSTEM.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=WorkUnitState.CLAIMED,
        to_state=WorkUnitState.FAILED,
        payload={
            "recovery_actor_id": SYSTEM.actor_id,
            "expected_version": version,
            "version": version + 1,
        },
        correlation_id=correlation_id,
        idempotency_key=f"{KEY}:failed",
    )
    ready_event = Event(
        occurred_at=failed_event.occurred_at,
        actor_id=SYSTEM.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=WorkUnitState.FAILED,
        to_state=WorkUnitState.READY,
        payload={
            "recovery_actor_id": SYSTEM.actor_id,
            "expected_version": version,
            "version": version + 2,
        },
        correlation_id=correlation_id,
        idempotency_key=KEY,
    )
    collision_event = failed_event if event_key.endswith(":failed") else ready_event
    setattr(collision_event, attribute, collision_value)
    migrated_session.add_all([failed_event, ready_event])
    unit.state = WorkUnitState.READY
    unit.version = version + 2
    migrated_session.commit()

    replay = recover_expired_claim(migrated_session, unit.id, SYSTEM, KEY, expected_version=version)

    assert isinstance(replay, DomainError)
    assert replay.code == "idempotency_conflict"
