from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN


def test_slo_report_route_returns_status_typed_metrics(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/slo-report", headers=HUMAN)

    assert response.status_code == 200
    body = response.json()
    assert "since" in body and "until" in body
    assert body["cost_per_unit"]["status"] == "no_data"
    assert body["cost_per_unit"]["value"] is None
    assert body["improvisation"]["status"] in {"no_data", "computed"}
