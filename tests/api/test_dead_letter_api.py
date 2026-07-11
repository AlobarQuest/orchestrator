"""WS-P2.1 Task 10: the dead-letter API surface (AC-005)."""

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER


def test_dead_letter_is_operator_only(db_client: TestClient) -> None:
    """A worker or verifier credential has no business enumerating another unit's failures."""
    assert db_client.get("/api/v1/dead-letter", headers=WORKER).status_code == 403
    assert db_client.get("/api/v1/dead-letter", headers=VERIFIER).status_code == 403


def test_dead_letter_is_empty_on_a_clean_database(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/dead-letter", headers=SYSTEM)

    assert response.status_code == 200
    assert response.json() == []
