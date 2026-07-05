from fastapi.testclient import TestClient

from orchestrator.main import app


def test_command_validation_is_stable() -> None:
    response = TestClient(app).post(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/commands/start",
        headers={"x-actor-id": "worker-1", "x-actor-role": "worker"},
        json={"idempotency_key": "", "expected_version": -1},
    )

    assert response.status_code == 422
