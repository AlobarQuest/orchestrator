"""WS-P2.1 Task 13: the consistency-check API surface (AC-008)."""

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_consistency_check_is_operator_only(db_client: TestClient) -> None:
    assert db_client.get("/api/v1/consistency-check", headers=WORKER).status_code == 403


def test_consistency_check_is_clean_on_a_fresh_database(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/consistency-check", headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert body["divergent"] is False
    assert body["findings"] == []
