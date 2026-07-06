import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orchestrator.cli import app
from tests.api.test_status_ledger_api import _register_ready_unit
from tests.cli.test_cli_http_parity import HUMAN


@pytest.fixture
def status_ledger_transport(monkeypatch: pytest.MonkeyPatch, db_client: TestClient) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        headers = dict(request.headers)
        if headers.get("authorization") == "Bearer human-token":
            headers.pop("authorization", None)
            headers.pop("x-credential-key-id", None)
            headers.update(HUMAN)
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query.decode()}"
        response = db_client.request(
            request.method,
            target,
            headers=headers,
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://testserver")


def test_status_ledger_cli_matches_api_json(
    db_client,
    status_ledger_transport: None,
    monkeypatch,
) -> None:
    unit_id = _register_ready_unit(db_client)
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)

    api_response = db_client.get("/api/v1/status-ledger", headers=HUMAN)
    cli_response = CliRunner().invoke(app, ["status-ledger", "--json"])

    assert api_response.status_code == 200
    assert cli_response.exit_code == 0
    assert json.loads(cli_response.stdout) == api_response.json()
    assert any(row["unit_id"] == unit_id for row in json.loads(cli_response.stdout))


def test_status_ledger_cli_matches_filtered_api_json(
    db_client,
    status_ledger_transport: None,
    monkeypatch,
) -> None:
    first_unit_id = _register_ready_unit(db_client, "cli-first")
    second_unit_id = _register_ready_unit(db_client, "cli-second")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)

    api_response = db_client.get(
        "/api/v1/status-ledger",
        headers=HUMAN,
        params={
            "work_unit_id": second_unit_id,
            "state": "ready",
            "include_inactive": "true",
        },
    )
    cli_response = CliRunner().invoke(
        app,
        [
            "status-ledger",
            "--work-unit-id",
            second_unit_id,
            "--state",
            "ready",
            "--include-inactive",
            "--json",
        ],
    )

    assert first_unit_id != second_unit_id
    assert api_response.status_code == 200
    assert cli_response.exit_code == 0
    assert json.loads(cli_response.stdout) == api_response.json()
    assert [row["unit_id"] for row in json.loads(cli_response.stdout)] == [second_unit_id]
