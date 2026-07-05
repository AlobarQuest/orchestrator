from fastapi.testclient import TestClient


def test_command_validation_is_stable(client: TestClient) -> None:
    response = client.post(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000001/commands/start",
        headers={
            "Authorization": "Bearer fixture-token",
            "X-Credential-Key-Id": "worker-key",
        },
        json={"idempotency_key": "", "expected_version": -1},
    )

    assert response.status_code == 422


def test_forbidden_error_shape_is_stable() -> None:
    from orchestrator.errors import DomainError
    from orchestrator.main import create_app

    application = create_app()

    @application.get("/forbidden")
    def forbidden() -> None:
        raise DomainError("role_forbidden", "only a human may register work", None)

    response = TestClient(application).get("/forbidden")

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "role_forbidden",
            "message": "only a human may register work",
        }
    }
