import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Claim, WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER

STALL_DECISION = "Cancel this unit, or have the system recover its expired claim"


def test_queue_is_human_authenticated_and_lists_the_decision_required(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    denied = db_client.get("/review", headers=WORKER)
    page = db_client.get("/review", headers=HUMAN)

    assert denied.status_code == 403
    assert page.status_code == 200
    assert review_unit.title in page.text
    # The item names the DECISION, not the state -- that is the whole point of the rebuild.
    assert "Decide whether this unit becomes completed or revision required" in page.text
    assert "/review/units/" in page.text


def test_a_failed_unit_appears_on_the_queue_with_its_disposition(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    # A failed unit was invisible to Increment 4's queue: FAILED is neither settled nor a designed
    # gate. It is the state most obviously waiting on a person -- nothing automatic moves it.
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.FAILED
        unit.attempt_count = unit.max_attempts
        session.commit()

    page = db_client.get("/review", headers=HUMAN)

    assert page.status_code == 200
    assert review_unit.title in page.text
    assert "Authorize a retry with a raised attempt limit, or cancel this unit" in page.text


def _executing_with_a_hold_ending(
    migrated_engine: Engine, unit_id: uuid.UUID, *, hold_ends_in: timedelta
) -> None:
    """Put a unit into `executing` holding one claim whose hold ends when we say.

    Written directly rather than driven through claim-and-start: what this file exercises is the
    QUEUE, and the claim service's own behaviour is pinned in `test_execution_stall.py`.
    """
    now = datetime.now(UTC)
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None
        unit.state = WorkUnitState.EXECUTING
        unit.attempt_count = 1
        session.add(
            Claim(
                work_unit_id=unit.id,
                attempt=1,
                claimed_by="worker-1",
                lease_token_hash="unused-by-a-read",
                idempotency_key=f"claim-{unit.id}",
                acquired_at=now - timedelta(hours=2),
                lease_expires_at=now + hold_ends_in,
            )
        )
        session.commit()


def test_a_unit_whose_worker_went_quiet_appears_on_the_queue(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    """WS-P2.19's caller, end to end, at the SHIPPED default grace.

    Nothing bounded a hung worker: a lapse transitions nothing and nobody reclaims on their own,
    so a unit whose worker died in `executing` sat there unreported. This is the whole of the
    noticing -- the page a person already opens, rendered from the fact itself.

    The threshold is not overridden here. An hour past the hold is past the 15-minute default, so
    what this proves is that the value production actually runs with reports a stalled unit.
    """
    _executing_with_a_hold_ending(migrated_engine, review_unit.id, hold_ends_in=-timedelta(hours=1))

    page = db_client.get("/review", headers=HUMAN)

    assert page.status_code == 200
    assert "Work units whose worker went quiet" in page.text
    assert STALL_DECISION in page.text
    assert f"/review/units/{review_unit.id}" in page.text
    # The honest caveat reaches the reader: a person who assumed the orchestrator knew the worker
    # was dead would over-read the entry and cancel work that is still running.
    assert "whether its worker died or is still running" in page.text


def test_a_unit_still_inside_its_hold_does_not_appear_as_stalled(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    """The other direction, through the same caller: an ordinary in-flight unit is not reported."""
    _executing_with_a_hold_ending(
        migrated_engine, review_unit.id, hold_ends_in=timedelta(minutes=10)
    )

    page = db_client.get("/review", headers=HUMAN)

    assert page.status_code == 200
    assert STALL_DECISION not in page.text


def test_a_unit_with_nothing_to_decide_is_absent_from_the_queue(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.COMPLETED
        session.commit()

    page = db_client.get("/review", headers=HUMAN)

    assert page.status_code == 200
    assert review_unit.title not in page.text
    assert "Nothing is waiting on you." in page.text
