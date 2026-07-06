import json
import uuid

import httpx
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture
def recorded_transport(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append({"method": request.method, "path": request.url.path, "body": body})
        payload: object = (
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "source_system": "orchestrator",
                "source_kind": "event",
                "source_id": "22222222-2222-2222-2222-222222222222",
                "source_action": "work_unit.transitioned",
                "event_id": "evt-" + "a" * 64,
                "mapping_version": "ws34.v1",
                "status": "pending",
                "skip_reason": None,
                "export_ref": None,
                "attempt_count": 0,
                "last_error": None,
                "created_at": "2026-07-06T00:00:00Z",
                "updated_at": "2026-07-06T00:00:00Z",
                "last_attempted_at": None,
                "published_at": None,
            }
            if request.url.path.endswith("/retry")
            else []
        )
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr("orchestrator.cli.HTTP_TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://testserver")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "human-token")
    monkeypatch.delenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", raising=False)
    return requests


def test_event_publications_cli_list_hits_api(recorded_transport: list[dict[str, object]]) -> None:
    result = CliRunner().invoke(app, ["event-publications", "list", "--json"])

    assert result.exit_code == 0
    assert recorded_transport == [
        {"method": "GET", "path": "/api/v1/event-publications", "body": None}
    ]


def test_event_publications_cli_queue_hits_api(recorded_transport: list[dict[str, object]]) -> None:
    source_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    result = CliRunner().invoke(
        app,
        [
            "event-publications",
            "queue",
            "--idempotency-key",
            "queue-1",
            "--expected-version",
            "0",
            "--source-kind",
            "event",
            "--source-id",
            str(source_id),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert recorded_transport == [
        {
            "method": "POST",
            "path": "/api/v1/event-publications/queue",
            "body": {
                "idempotency_key": "queue-1",
                "expected_version": 0,
                "source_kind": "event",
                "source_id": str(source_id),
            },
        }
    ]


def test_event_publications_cli_export_hits_api(
    recorded_transport: list[dict[str, object]],
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "event-publications",
            "export",
            "--idempotency-key",
            "export-1",
            "--expected-version",
            "0",
            "--output-path",
            "/tmp/factory-events.jsonl",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert recorded_transport == [
        {
            "method": "POST",
            "path": "/api/v1/event-publications/export",
            "body": {
                "idempotency_key": "export-1",
                "expected_version": 0,
                "output_path": "/tmp/factory-events.jsonl",
            },
        }
    ]


def test_event_publications_cli_retry_hits_api(recorded_transport: list[dict[str, object]]) -> None:
    publication_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    result = CliRunner().invoke(
        app,
        [
            "event-publications",
            "retry",
            str(publication_id),
            "--idempotency-key",
            "retry-1",
            "--expected-version",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert recorded_transport == [
        {
            "method": "POST",
            "path": f"/api/v1/event-publications/{publication_id}/retry",
            "body": {"idempotency_key": "retry-1", "expected_version": 0},
        }
    ]
