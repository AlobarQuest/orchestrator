import json
from pathlib import Path

from typer.testing import CliRunner

from orchestrator.cli import app
from tests.api.test_context_api import standing_context


def test_claim_accepts_context_file_and_posts_standing_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(standing_context()), encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {
            "claim_id": "claim-1",
            "attempt": 1,
            "lease_token": "token",
            "expires_at": "2026-07-06T12:00:00Z",
            "context_snapshot_id": "snapshot-1",
        }

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "claim",
            "unit-1",
            "--idempotency-key",
            "claim-1",
            "--expected-version",
            "2",
            "--context",
            f"@{context_path}",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["context_snapshot_id"] == "snapshot-1"
    assert observed == {
        "method": "POST",
        "path": "/api/v1/work-units/unit-1/claim",
        "payload": {
            "idempotency_key": "claim-1",
            "expected_version": 2,
            "standing_context": standing_context(),
        },
    }


def test_claim_preserves_data_payload_compatibility(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {
            "claim_id": "claim-1",
            "attempt": 1,
            "lease_token": "token",
            "expires_at": "2026-07-06T12:00:00Z",
            "context_snapshot_id": None,
        }

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "claim",
            "unit-1",
            "--data",
            '{"idempotency_key":"claim-1","expected_version":2}',
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": "/api/v1/work-units/unit-1/claim",
        "payload": {"idempotency_key": "claim-1", "expected_version": 2},
    }


def test_start_accepts_context_file_and_posts_standing_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(standing_context()), encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"unit_id": "unit-1", "state": "executing", "version": 4, "event_id": "event-1"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "start",
            "unit-1",
            "--idempotency-key",
            "start-1",
            "--expected-version",
            "3",
            "--attempt",
            "1",
            "--lease-token",
            "token",
            "--context",
            f"@{context_path}",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": "/api/v1/work-units/unit-1/commands/start",
        "payload": {
            "idempotency_key": "start-1",
            "expected_version": 3,
            "attempt": 1,
            "lease_token": "token",
            "standing_context": standing_context(),
            "context_snapshot_id": None,
        },
    }


def test_preflight_command_posts_context_payload(monkeypatch, tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(standing_context()), encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return {"id": "snapshot-1", "decision": "accepted"}

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "preflight",
            "unit-1",
            "--idempotency-key",
            "preflight-1",
            "--expected-version",
            "3",
            "--context",
            f"@{context_path}",
            "--purpose",
            "diagnostic",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "method": "POST",
        "path": "/api/v1/work-units/unit-1/preflight",
        "payload": {
            "idempotency_key": "preflight-1",
            "expected_version": 3,
            "standing_context": standing_context(),
            "previous_context_snapshot_id": None,
            "approval_id": None,
            "purpose": "diagnostic",
            "attempt": None,
            "lease_token": None,
        },
    }


def test_list_context_snapshots_forwards_get(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_request(method: str, path: str, payload=None):
        observed.update(method=method, path=path, payload=payload)
        return [{"id": "snapshot-1"}]

    monkeypatch.setattr("orchestrator.cli.request", fake_request)

    result = CliRunner().invoke(app, ["list-context-snapshots", "unit-1", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [{"id": "snapshot-1"}]
    assert observed == {
        "method": "GET",
        "path": "/api/v1/work-units/unit-1/context-snapshots",
        "payload": None,
    }


def test_context_option_rejects_non_object_json() -> None:
    result = CliRunner().invoke(
        app,
        [
            "claim",
            "unit-1",
            "--idempotency-key",
            "claim-1",
            "--expected-version",
            "2",
            "--context",
            "[]",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "context must be a JSON object" in result.output
