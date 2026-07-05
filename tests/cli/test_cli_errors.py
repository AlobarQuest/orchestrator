import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.cli import app
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.main import create_app
from orchestrator.services.lifecycle import ActorContext

LIFECYCLE_COMMANDS = (
    "ready",
    "start",
    "block",
    "request-approval",
    "approve",
    "submit",
    "verify",
    "review",
    "complete",
    "fail",
    "retry",
    "cancel",
)


@pytest.mark.parametrize("command", LIFECYCLE_COMMANDS)
def test_cli_error_preserves_api_contract(monkeypatch, command: str) -> None:
    application = create_app()
    application.dependency_overrides[get_actor] = lambda: ActorContext("worker", ActorRole.WORKER)
    application.dependency_overrides[get_session] = lambda: Mock(spec=Session)

    def version_conflict(*_args, **_kwargs):
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state="ready",
            current_version=4,
        )

    monkeypatch.setattr("orchestrator.api.routes.transition_unit", version_conflict)
    api_response = TestClient(application).post(
        f"/api/v1/work-units/00000000-0000-0000-0000-000000000001/commands/{command}",
        json={"idempotency_key": f"{command}-1", "expected_version": 3},
    )
    assert api_response.status_code == 409

    def fail(*_args, **_kwargs):
        from orchestrator.cli import CliError

        response = httpx.Response(
            api_response.status_code,
            request=httpx.Request("POST", "https://example.invalid/api"),
            json=api_response.json(),
        )
        raise CliError.from_response(response)

    monkeypatch.setattr("orchestrator.cli.request", fail)
    result = CliRunner().invoke(
        app,
        [
            command,
            "unit-1",
            "--idempotency-key",
            f"{command}-1",
            "--expected-version",
            "3",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == api_response.json()


def test_cli_rejects_invalid_data_before_http(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestrator.cli.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP called")),
    )

    result = CliRunner().invoke(app, ["register-revision", "--data", "not-json"])

    assert result.exit_code == 2
    assert "data must be a JSON object" in result.stderr
