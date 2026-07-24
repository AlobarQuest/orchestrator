import json

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orchestrator.cli import app
from tests.cli.test_cli_http_parity import HUMAN


@pytest.fixture
def slo_report_transport(monkeypatch: pytest.MonkeyPatch, db_client: TestClient) -> None:
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


def test_slo_report_cli_matches_api_json(
    db_client,
    slo_report_transport: None,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)

    api_response = db_client.get("/api/v1/slo-report", headers=HUMAN)
    cli_response = CliRunner().invoke(app, ["slo-report", "--json"])

    assert api_response.status_code == 200
    assert cli_response.exit_code == 0
    cli_body = json.loads(cli_response.stdout)
    api_body = api_response.json()
    # "since"/"until" default to the request's own now(), so two independent calls
    # (one via the API TestClient, one via the CLI's HTTP round-trip) legitimately
    # compute distinct instants; compare everything else exactly.
    assert cli_body.keys() == api_body.keys()
    for key in api_body:
        if key in {"since", "until"}:
            continue
        assert cli_body[key] == api_body[key]


def test_slo_report_cli_passes_since_and_until_query_params(
    db_client,
    slo_report_transport: None,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)

    api_response = db_client.get(
        "/api/v1/slo-report",
        headers=HUMAN,
        params={"since": "2026-07-01T00:00:00", "until": "2026-07-08T00:00:00"},
    )
    cli_response = CliRunner().invoke(
        app,
        [
            "slo-report",
            "--since",
            "2026-07-01T00:00:00",
            "--until",
            "2026-07-08T00:00:00",
            "--json",
        ],
    )

    assert api_response.status_code == 200
    assert cli_response.exit_code == 0
    assert json.loads(cli_response.stdout) == api_response.json()
