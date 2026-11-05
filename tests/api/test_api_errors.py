import pytest
from fastapi.testclient import TestClient

from orchestrator.api.dependencies import APIAuthenticationError
from orchestrator.api.error_shape import error_body, status_for
from orchestrator.errors import DomainError


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


STATUS_TABLE = {
    "unit_not_found": 404,
    "thing_not_found": 404,
    "csrf_unavailable": 503,
    "factory_policy_invalid": 503,
    "factory_policy_version_unsupported": 503,
    "named_check_observation_unavailable": 503,
    "role_forbidden": 403,
    "human_actor_required": 403,
    "csrf_rejected": 403,
    "intake_registrar_invalid": 403,
    "stale_version": 409,
    "intake_change_record_required": 409,
}


def _app_raising(error: Exception) -> TestClient:
    from orchestrator.main import create_app

    application = create_app()

    @application.get("/raises")
    def raises() -> None:
        raise error

    return TestClient(application)


@pytest.mark.parametrize(("code", "status"), sorted(STATUS_TABLE.items()))
def test_every_domain_error_code_keeps_its_status_and_bare_body(code: str, status: int) -> None:
    response = _app_raising(DomainError(code, "msg", None)).get("/raises")

    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": "msg"}}


def test_optional_detail_appears_only_when_set() -> None:
    full = _app_raising(
        DomainError("stale_version", "msg", "reload", current_state="ready", current_version=3)
    ).get("/raises")
    partial = _app_raising(DomainError("stale_version", "msg", None, current_version=0)).get(
        "/raises"
    )

    assert full.json() == {
        "error": {
            "code": "stale_version",
            "message": "msg",
            "recovery": "reload",
            "current_state": "ready",
            "current_version": 3,
        }
    }
    assert partial.json() == {
        "error": {"code": "stale_version", "message": "msg", "current_version": 0}
    }


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("authentication_required", "valid authentication credentials are required"),
        ("authentication_failed", "authentication credentials were rejected"),
    ],
)
def test_authentication_refusal_body_and_challenge_are_stable(code: str, message: str) -> None:
    response = _app_raising(APIAuthenticationError(code)).get("/raises")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"error": {"code": code, "message": message}}


@pytest.mark.parametrize(("code", "status"), sorted(STATUS_TABLE.items()))
def test_status_for_answers_the_table(code: str, status: int) -> None:
    assert status_for(code) == status


def test_the_table_refuses_a_map_that_moves_a_503_code_to_409() -> None:
    def moved(code: str) -> int:
        return 409 if code == "csrf_unavailable" else status_for(code)

    assert any(moved(code) != status for code, status in STATUS_TABLE.items())


def test_error_body_drops_none_details() -> None:
    assert error_body("c", "m", recovery=None, current_version=0) == {
        "error": {"code": "c", "message": "m", "current_version": 0}
    }
