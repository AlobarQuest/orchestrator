import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.cli import CliError, app, request
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.main import create_app

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

    monkeypatch.setattr("orchestrator.api.routes.lifecycle.transition_unit", version_conflict)
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

    result = CliRunner().invoke(app, ["renew", "unit-1", "--data", "not-json"])

    assert result.exit_code == 2
    assert "data must be a JSON object" in result.stderr


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            httpx.ConnectError(
                "token=secret-token host=private.example",
                request=httpx.Request("GET", "https://private.example"),
            ),
            "api_unavailable",
            "API request could not be completed",
        ),
        (
            httpx.ReadTimeout(
                "timed out at https://private.example?token=secret-token",
                request=httpx.Request("GET", "https://private.example"),
            ),
            "api_timeout",
            "API request timed out",
        ),
    ],
)
def test_transport_failures_are_stable_and_do_not_leak(
    monkeypatch,
    error: httpx.RequestError,
    code: str,
    message: str,
) -> None:
    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr("orchestrator.cli.httpx.Client", lambda **_kwargs: FailingClient())
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "secret-token")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://private.example")

    with pytest.raises(CliError) as observed:
        request("GET", "/api/v1/work-units/unit-1/history")

    assert observed.value.detail == {"code": code, "message": message}
    assert "secret-token" not in str(observed.value.detail)
    assert "private.example" not in str(observed.value.detail)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            request=httpx.Request("GET", "https://private.example"),
            content=b"not-json secret-token",
        ),
        httpx.Response(
            200,
            request=httpx.Request("GET", "https://private.example"),
            json="secret-token",
        ),
    ],
)
def test_malformed_success_response_is_stable_and_does_not_leak(
    monkeypatch, response: httpx.Response
) -> None:
    class ResponseClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr("orchestrator.cli.httpx.Client", lambda **_kwargs: ResponseClient())

    with pytest.raises(CliError) as observed:
        request("GET", "/api/v1/work-units/unit-1/history")

    assert observed.value.detail == {
        "code": "invalid_response",
        "message": "API returned an invalid response",
    }
    assert "secret-token" not in str(observed.value.detail)


def test_non_json_error_response_is_sanitized() -> None:
    response = httpx.Response(
        502,
        request=httpx.Request("GET", "https://private.example?token=secret-token"),
        content=b"upstream private.example token=secret-token",
    )

    error = CliError.from_response(response)

    assert error.detail == {
        "code": "http_error",
        "message": "API request failed with HTTP 502",
    }
    assert "secret-token" not in str(error.detail)
    assert "private.example" not in str(error.detail)


def test_transport_failure_cli_exit_is_stable_and_sanitized(monkeypatch) -> None:
    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, *_args, **_kwargs):
            raise httpx.ConnectError(
                "secret-token private.example",
                request=httpx.Request("GET", "https://private.example"),
            )

    monkeypatch.setattr("orchestrator.cli.httpx.Client", lambda **_kwargs: FailingClient())
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "secret-token")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://private.example")

    result = CliRunner().invoke(app, ["history", "unit-1", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {
            "code": "api_unavailable",
            "message": "API request could not be completed",
        }
    }
    assert "secret-token" not in result.stdout
    assert "private.example" not in result.stdout
    assert "secret-token" not in result.stderr
    assert "private.example" not in result.stderr


def _serve(monkeypatch, handler) -> None:
    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "t")
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://api.example.invalid")


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        (
            lambda _r: httpx.Response(
                401, json={"error": {"code": "unauthorized", "message": "no"}}
            ),
            "unauthorized",
        ),
        (lambda _r: httpx.Response(503, text="upstream down"), "http_error"),
        (_unreachable, "api_unavailable"),
    ],
)
def test_check_consistency_failures_exit_one_without_a_traceback(
    monkeypatch, handler, code: str
) -> None:
    _serve(monkeypatch, handler)

    result = CliRunner().invoke(app, ["check-consistency"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert f"code={code} message=" in result.stderr
    assert "Traceback" not in result.output + result.stderr


def test_check_consistency_reports_divergence_then_exits_one(monkeypatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={"divergent": True}))

    result = CliRunner().invoke(app, ["check-consistency", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"divergent": True}


def test_check_consistency_clean_report_exits_zero(monkeypatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={"divergent": False}))

    result = CliRunner().invoke(app, ["check-consistency", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"divergent": False}


@pytest.mark.parametrize("api_url", ["http://a..b", "http://exa\x01mple"])
def test_a_malformed_api_url_is_a_stable_cli_error(monkeypatch, api_url: str) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", api_url)
    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", None)

    result = CliRunner().invoke(app, ["history", "unit-1"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "code=api_url_invalid" in result.stderr


def test_an_unresolvable_host_with_a_space_is_unavailable_not_a_traceback(monkeypatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://exa mple")
    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", None)

    result = CliRunner().invoke(app, ["history", "unit-1"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "code=api_unavailable" in result.stderr


def test_a_control_character_in_a_unit_id_is_a_stable_cli_error(monkeypatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))

    result = CliRunner().invoke(app, ["history", "unit\x01-1"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "code=api_url_invalid" in result.stderr
