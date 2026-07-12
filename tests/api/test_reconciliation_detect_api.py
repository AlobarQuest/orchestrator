"""WS-P2.1 Task 8: the detect-pass API surface."""

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_detect_is_system_only(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/reconciliation/detect",
        headers=WORKER,
        json={"idempotency_key": "detect-role", "expected_version": 0},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_detect_reports_counters_and_is_clean_on_a_fresh_database(
    db_client: TestClient,
) -> None:
    response = db_client.post(
        "/api/v1/reconciliation/detect",
        headers=SYSTEM,
        json={"idempotency_key": "detect-clean", "expected_version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "conditions_recorded": 0,
        "skipped_correlations": 0,
        "suppressed_duplicates": 0,
    }
