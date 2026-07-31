from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER


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
