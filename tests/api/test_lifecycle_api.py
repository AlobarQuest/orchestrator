from fastapi.testclient import TestClient

from orchestrator.main import app


def test_api_is_versioned() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/v1/work-units/{unit_id}/readiness" in paths
    assert "/api/v1/work-units/{unit_id}/commands/{command}" in paths
