"""An unhandled exception gets the nested error shape, a log line and one keyed alert.

Decision 1 and calls 1 and 2 of the platform layer design. The body never depends on whether the
alert went out, and nothing the exception said reaches the body, the alert or a log line.
"""

import asyncio
import json
import logging
import re

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from alert_hub_client import alert_key
from orchestrator.api.dependencies import APIAuthenticationError
from orchestrator.api.server_errors import (
    LOG,
    ServerAlertConfig,
    log_level,
    server_alert_config,
    server_error_handler,
)
from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.main import create_app

SECRET = "hunter2"
PLACEHOLDER_TOKEN = "placeholder-sender-token-for-tests"
UUID4_HEX = re.compile(r"[0-9a-f]{32}")


@pytest.fixture(autouse=True)
def _enabled_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that ran alembic's `fileConfig` on this xdist worker disabled every logger that
    already existed, this module's among them."""
    monkeypatch.setattr(LOG, "disabled", False)


def _app(transport: httpx.BaseTransport | None, *, token: str | None) -> FastAPI:
    application = create_app()

    @application.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"postgresql://user:{SECRET}@db/x")

    application.state.alert_config = ServerAlertConfig(
        token=SecretStr(token) if token is not None else None,
        user_agent="server-errors-test/1",
        timeout=5.0,
        transport=transport,
    )
    return application


def _recording(status: int, sent: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status)

    return httpx.MockTransport(handle)


def _timing_out(sent: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    return httpx.MockTransport(handle)


def _refusing_any_request() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing may be sent without a token")

    return httpx.MockTransport(handle)


def _get(application: FastAPI, path: str = "/boom") -> httpx2.Response:
    return TestClient(application, raise_server_exceptions=False).get(path)


def _without_request_id(body: dict[str, dict[str, object]]) -> dict[str, object]:
    return {key: value for key, value in body["error"].items() if key != "request_id"}


def test_an_unhandled_exception_answers_the_nested_shape_with_a_request_id() -> None:
    response = _get(_app(None, token=None))

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == "internal_error"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert UUID4_HEX.fullmatch(body["error"]["request_id"])


def test_the_body_is_the_same_whatever_alert_hub_does() -> None:
    sent: list[httpx.Request] = []
    cases = {
        "accepted": _app(_recording(202, sent), token=PLACEHOLDER_TOKEN),
        "refused": _app(_recording(401, sent), token=PLACEHOLDER_TOKEN),
        "timed out": _app(_timing_out(sent), token=PLACEHOLDER_TOKEN),
        "no token": _app(_refusing_any_request(), token=None),
    }

    responses = {name: _get(application) for name, application in cases.items()}

    assert {name: response.status_code for name, response in responses.items()} == dict.fromkeys(
        cases, 500
    )
    bodies = {name: _without_request_id(response.json()) for name, response in responses.items()}
    assert len({json.dumps(body, sort_keys=True) for body in bodies.values()}) == 1, bodies
    # Each case with a token tried its one send; the case without one sent nothing.
    assert len(sent) == 3


def test_one_alert_is_sent_keyed_on_route_template_and_exception_type() -> None:
    sent: list[httpx.Request] = []
    application = _app(_recording(202, sent), token=PLACEHOLDER_TOKEN)

    first = _get(application)

    assert len(sent) == 1
    request = sent[0]
    assert (request.method, request.url.path) == ("POST", "/v1/alerts")
    assert request.headers["Authorization"] == f"Bearer {PLACEHOLDER_TOKEN}"
    payload = json.loads(request.content)
    assert payload["key"] == alert_key("server-error", "/boom", "RuntimeError")
    assert payload["status"] == "firing"

    second = _get(application)

    assert len(sent) == 2
    assert json.loads(sent[1].content)["key"] == payload["key"]
    request_ids = {first.json()["error"]["request_id"], second.json()["error"]["request_id"]}
    assert len(request_ids) == 2
    assert not any(request_id in payload["key"] for request_id in request_ids)


def test_the_route_template_not_the_path_keys_the_alert() -> None:
    sent: list[httpx.Request] = []
    application = _app(_recording(202, sent), token=PLACEHOLDER_TOKEN)

    @application.get("/things/{thing_id}")
    def thing(thing_id: str) -> None:
        raise KeyError(thing_id)

    _get(application, "/things/one")
    _get(application, "/things/two")

    keys = {json.loads(request.content)["key"] for request in sent}
    assert keys == {alert_key("server-error", "/things/{thing_id}", "KeyError")}


def test_a_request_no_route_matched_keys_on_unmatched() -> None:
    sent: list[httpx.Request] = []
    application = _app(_recording(202, sent), token=PLACEHOLDER_TOKEN)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/nowhere",
        "headers": [],
        "query_string": b"",
        "app": application,
    }

    response = asyncio.run(server_error_handler(Request(scope), RuntimeError(SECRET)))
    assert response.background is not None
    asyncio.run(response.background())

    assert response.status_code == 500
    assert len(sent) == 1
    assert json.loads(sent[0].content)["key"] == alert_key(
        "server-error", "unmatched", "RuntimeError"
    )


def _texts(record: logging.LogRecord) -> str:
    return " ".join(str(value) for value in (record.getMessage(), *vars(record).values()))


def test_nothing_the_exception_said_leaves_the_handler(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[httpx.Request] = []
    application = _app(_recording(202, sent), token=PLACEHOLDER_TOKEN)

    with caplog.at_level(logging.DEBUG):
        response = _get(application)

    request_id = response.json()["error"]["request_id"]
    assert SECRET not in response.text
    assert "Traceback" not in response.text

    alert = json.loads(sent[0].content)
    told = f"{alert['title']} {alert['message']}"
    for fact in (request_id, "GET", "/boom", "RuntimeError"):
        assert fact in told, fact
    assert SECRET not in sent[0].content.decode()
    assert "Traceback" not in told

    ours = [record for record in caplog.records if record.name == "orchestrator.server_errors"]
    assert [record.msg.split()[0] for record in ours] == ["server_error", "notified"]
    for record in ours:
        for fact in (request_id, "GET", "/boom", "RuntimeError"):
            assert fact in record.getMessage(), (record.msg, fact)
    for record in caplog.records:
        assert SECRET not in _texts(record), record
        assert record.exc_info is None, record
        assert "Traceback" not in _texts(record), record


def test_a_refused_send_is_logged_with_the_request_and_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[httpx.Request] = []
    application = _app(_recording(401, sent), token=PLACEHOLDER_TOKEN)

    with caplog.at_level(logging.INFO):
        response = _get(application)

    request_id = response.json()["error"]["request_id"]
    assert len(sent) == 1
    ours = [record for record in caplog.records if record.name == "orchestrator.server_errors"]
    assert [record.msg.split()[0] for record in ours] == ["server_error", "notify_failed"]
    for record in ours:
        for fact in (request_id, "GET", "/boom", "RuntimeError"):
            assert fact in record.getMessage(), (record.msg, fact)
    assert all(SECRET not in _texts(record) for record in caplog.records)
    assert all(PLACEHOLDER_TOKEN not in _texts(record) for record in caplog.records)


def test_an_empty_token_is_not_a_token(caplog: pytest.LogCaptureFixture) -> None:
    application = _app(_refusing_any_request(), token="")

    with caplog.at_level(logging.INFO):
        response = _get(application)

    assert response.status_code == 500
    ours = [record for record in caplog.records if record.name == "orchestrator.server_errors"]
    assert [record.msg.split()[0] for record in ours] == ["server_error", "alert_not_configured"]


def test_with_no_token_nothing_is_sent_and_that_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = _app(_refusing_any_request(), token=None)

    with caplog.at_level(logging.INFO):
        response = _get(application)

    assert response.status_code == 500
    request_id = response.json()["error"]["request_id"]
    ours = [record for record in caplog.records if record.name == "orchestrator.server_errors"]
    assert [record.msg.split()[0] for record in ours] == ["server_error", "alert_not_configured"]
    assert request_id in ours[1].getMessage()
    assert all(SECRET not in _texts(record) for record in caplog.records)


def test_create_app_defaults_to_no_token() -> None:
    assert create_app().state.alert_config.token is None


def test_the_setting_becomes_the_config() -> None:
    settings = Settings.model_validate({"alert_hub_token": PLACEHOLDER_TOKEN})

    config = server_alert_config(settings)

    assert config.token is not None
    assert config.token.get_secret_value() == PLACEHOLDER_TOKEN
    assert config.timeout == 5.0
    assert config.transport is None
    assert server_alert_config(Settings.model_validate({})).token is None


def test_the_handlers_beyond_fastapis_defaults_are_exactly_three() -> None:
    added = set(create_app().exception_handlers) - set(FastAPI().exception_handlers)

    assert added == {APIAuthenticationError, DomainError, Exception}


@pytest.mark.parametrize(
    ("value", "level"),
    [
        ("info", logging.INFO),
        ("INFO", logging.INFO),
        ("debug", logging.DEBUG),
        ("bogus", logging.INFO),
        (None, logging.INFO),
        ("", logging.INFO),
    ],
)
def test_log_level_uppercases_and_falls_back_to_info(value: str | None, level: int) -> None:
    assert log_level(value) == level
