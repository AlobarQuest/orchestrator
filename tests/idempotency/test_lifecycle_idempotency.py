"""AC-007 asymmetry #1: the lifecycle path has NO advisory lock.

Every other ingress takes `pg_advisory_xact_lock` on the key. Lifecycle transitions do not: they
rely on the `WorkUnit` row lock (`SELECT ... FOR UPDATE`) to serialize writers, plus the globally
unique `Event.idempotency_key` so the loser finds the event and replays it.

That asymmetry is real, so a SEQUENTIAL double-submit is not enough to prove it safe. This drives
two writers CONCURRENTLY through the row lock.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from tests.services.test_dependencies import register_unit

KEY = "lifecycle-concurrent-double-submit"


def test_a_concurrent_duplicate_transition_writes_one_event(
    migrated_engine: Engine, migrated_session: Session
) -> None:
    unit = register_unit(migrated_session, "idem-lifecycle")
    unit.state = WorkUnitState.DRAFT
    migrated_session.commit()
    unit_id, version = unit.id, unit.version
    barrier = Barrier(2)

    def submit() -> tuple[str, int]:
        with Session(migrated_engine) as session:
            barrier.wait(timeout=15)  # maximise the overlap
            result = transition_unit(
                session,
                TransitionCommand(
                    unit_id=unit_id,
                    target=WorkUnitState.READY,
                    actor=ActorContext("system", ActorRole.SYSTEM),
                    expected_version=version,
                    idempotency_key=KEY,
                ),
            )
            return result.state, result.version

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=30) for f in [pool.submit(submit), pool.submit(submit)]]

    # Identical responses, exactly one row, and the version bumped once -- with no advisory lock.
    # The row lock serialized them; the loser replayed the event by its globally-unique key.
    assert results[0] == results[1] == (WorkUnitState.READY, version + 1)
    with Session(migrated_engine) as session:
        events = session.scalar(
            select(func.count()).select_from(Event).where(Event.idempotency_key == KEY)
        )
        assert events == 1
        refreshed = session.get(WorkUnit, unit_id)
        assert refreshed is not None and refreshed.version == version + 1
