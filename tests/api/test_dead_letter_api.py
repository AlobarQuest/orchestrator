"""WS-P2.1 Task 10: the dead-letter API surface (AC-005)."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.reporting.dead_letter import recovery_action
from tests._support.seeding import register_unit
from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER


def test_dead_letter_is_operator_only(db_client: TestClient) -> None:
    """A worker or verifier credential has no business enumerating another unit's failures."""
    assert db_client.get("/api/v1/dead-letter", headers=WORKER).status_code == 403
    assert db_client.get("/api/v1/dead-letter", headers=VERIFIER).status_code == 403


def test_dead_letter_is_empty_on_a_clean_database(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/dead-letter", headers=SYSTEM)

    assert response.status_code == 200
    assert response.json() == []


def test_an_entry_serves_its_recovery_action(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The response model declares the field, so the route doesn't drop it."""
    with Session(migrated_engine) as session:
        unit = register_unit(session, "dl-api-failed")
        unit.state = WorkUnitState.FAILED
        session.commit()
        expected = recovery_action(session, unit)

    response = db_client.get("/api/v1/dead-letter", headers=SYSTEM)

    assert response.status_code == 200
    assert [entry["recovery_action"] for entry in response.json()] == [expected]
