"""AC-007: cost-actuals has NO advisory lock and NO WorkUnit row lock.

`record_cost_actuals` pre-checks by `Event.idempotency_key`, inserts, and only falls back to
catching the unique-constraint `IntegrityError` on an actual collision. Unlike the
ADVISORY_LOCK/ROW_LOCK ingresses, nothing serializes two concurrent callers before they both
pass the pre-check -- so the `except IntegrityError` replay branch is only proven by an actual
race, not a sequential double-call. This file proves both: a sequential replay, and the
concurrent double-submit that drives both callers through the insert and forces one of them
into the `IntegrityError -> rollback -> re-query` path.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.cost_actuals import record_cost_actuals
from tests.services.test_dependencies import register_unit
from tests.services.test_evidence import active_claim, worker

# `record_cost_actuals` commits, and the session's default `expire_on_commit=True` expires the
# returned instance -- reading `.id` after the caller's session has closed raises
# `DetachedInstanceError`. Read it while the session is still open, inside `submit()`.


def _ready_unit(session: Session, key: str) -> WorkUnit:
    unit = register_unit(session, key)
    unit.state = WorkUnitState.READY
    session.commit()
    return unit


def test_a_duplicate_cost_actuals_submission_replays_the_same_event(
    migrated_session: Session, ready_unit: WorkUnit
) -> None:
    grant = active_claim(migrated_session, ready_unit)
    key = f"factory-runner:{ready_unit.id}:cost:a{grant.attempt}"
    kwargs: dict[str, Any] = dict(
        work_unit_id=ready_unit.id,
        actor=worker(),
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        cost_known=True,
        llm_calls=12,
        num_turns=4,
        input_tokens=1000,
        output_tokens=200,
        cost_usd=1.23,
        idempotency_key=key,
    )

    first = record_cost_actuals(migrated_session, **kwargs)
    second = record_cost_actuals(migrated_session, **kwargs)

    assert second.id == first.id
    migrated_session.expire_all()
    count = migrated_session.scalar(
        select(func.count()).select_from(Event).where(Event.idempotency_key == key)
    )
    assert count == 1


def test_a_concurrent_duplicate_cost_actuals_submission_writes_one_event(
    migrated_engine: Engine,
) -> None:
    """Two callers race the SAME idempotency_key. Nothing serializes them ahead of the
    insert -- each has its own claim (a different WorkUnit/Claim row), so
    `validate_active_claim`'s `with_for_update()` never contends between the two threads. Both
    must therefore reach the pre-check together, both insert, and the loser must take the
    `IntegrityError` branch -- exercising the one path the sequential test above cannot reach.
    """
    with Session(migrated_engine) as setup:
        first_unit = _ready_unit(setup, "cost-actuals-concurrent-first")
        second_unit = _ready_unit(setup, "cost-actuals-concurrent-second")
        first_grant = active_claim(setup, first_unit)
        second_grant = claim_unit(setup, second_unit.id, worker(), "claim-2")
        assert isinstance(second_grant, LeaseGrant)
        requests = (
            (first_unit.id, first_grant.attempt, first_grant.lease_token),
            (second_unit.id, second_grant.attempt, second_grant.lease_token),
        )

    key = "cost-actuals-double-submit"
    start = Barrier(2)
    before_insert = Barrier(2)
    synchronized_threads: set[int] = set()

    def synchronize_precheck(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        # Match ONLY the idempotency_key pre-check `WHERE` clause, once per thread. A looser
        # match (e.g. bare "FROM events") also catches the winner's post-commit refresh-by-id
        # and the loser's post-rollback re-query -- both legitimately reuse this table -- and
        # synchronizing on those too deadlocks the barrier waiting for a second party that
        # never arrives.
        thread_id = threading.get_ident()
        if thread_id in synchronized_threads:
            return
        if "events.idempotency_key = " in statement:
            synchronized_threads.add(thread_id)
            before_insert.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_precheck)

    def submit(request: tuple[uuid.UUID, int, str]) -> uuid.UUID:
        unit_id, attempt, lease_token = request
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            result = record_cost_actuals(
                session,
                work_unit_id=unit_id,
                actor=worker(),
                attempt=attempt,
                lease_token=lease_token,
                cost_known=True,
                llm_calls=12,
                num_turns=4,
                input_tokens=1000,
                output_tokens=200,
                cost_usd=1.23,
                idempotency_key=key,
            )
            return result.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(submit, request) for request in requests)
            results = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_precheck)

    assert results[0] == results[1]

    with Session(migrated_engine) as session:
        count = session.scalar(
            select(func.count()).select_from(Event).where(Event.idempotency_key == key)
        )
        assert count == 1
