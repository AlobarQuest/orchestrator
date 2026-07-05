import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orchestrator.cli import app

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
AUTHORITY = {
    "capabilities": {"repository_write": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}


@pytest.fixture
def in_process_transport(monkeypatch: pytest.MonkeyPatch, db_client: TestClient) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        response = db_client.request(
            request.method,
            request.url.path,
            headers=dict(request.headers),
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


def test_real_http_api_and_cli_have_success_and_error_parity(
    db_client: TestClient,
    in_process_transport: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-revision",
            "expected_version": 0,
            "package_id": "cli-http-package",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:cli-http",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-unit",
            "expected_version": 0,
            "unit_key": "cli-http-unit",
            "title": "Exercise real CLI HTTP",
            "outcome": "CLI and API agree",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approval = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "cli-http-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approval.status_code == 200

    body = {"idempotency_key": "cli-http-ready", "expected_version": 1}
    api_success = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready", headers=SYSTEM, json=body
    )
    assert api_success.status_code == 200
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "system-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "system-key")
    cli_success = CliRunner().invoke(
        app,
        [
            "ready",
            unit_id,
            "--idempotency-key",
            "cli-http-ready",
            "--expected-version",
            "1",
            "--json",
        ],
    )
    assert cli_success.exit_code == 0
    assert json.loads(cli_success.stdout) == api_success.json()
    assert {
        key: json.loads(cli_success.stdout)[key] for key in ("state", "version", "event_id")
    } == {key: api_success.json()[key] for key in ("state", "version", "event_id")}

    error_body = {"idempotency_key": "cli-http-stale", "expected_version": 1}
    api_error = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/start", headers=WORKER, json=error_body
    )
    assert api_error.status_code == 409
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "fixture-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "worker-key")
    cli_error = CliRunner().invoke(
        app,
        [
            "start",
            unit_id,
            "--idempotency-key",
            "cli-http-stale",
            "--expected-version",
            "1",
            "--json",
        ],
    )
    assert cli_error.exit_code == 1
    assert {
        key: json.loads(cli_error.stdout)["error"].get(key)
        for key in ("code", "current_version", "recovery")
    } == {
        key: api_error.json()["error"].get(key) for key in ("code", "current_version", "recovery")
    }

    api_history = db_client.get(f"/api/v1/work-units/{unit_id}/history", headers=WORKER)
    assert api_history.status_code == 200
    assert isinstance(api_history.json(), list)
    cli_history = CliRunner().invoke(app, ["history", unit_id, "--json"])
    assert cli_history.exit_code == 0
    assert json.loads(cli_history.stdout) == api_history.json()
