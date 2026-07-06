import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def standing_context(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "code_standards_version": "1.0",
        "security_standards_version": "1.0",
        "project_standards_version": "1.0",
        "agent_id": "worker",
        "authority_profile": "agent-queue-v1",
        "runtime_name": "codex",
        "runtime_version": "1.0",
        "skill_bundle_id": "ws-3.3-protocol-smoke-runtime-semantics",
        "skill_bundle_version": "1",
        "capabilities": ["repository_write"],
    }
    value.update(overrides)
    return value


def register_context_unit(db_client: TestClient) -> str:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "context-api-revision",
            "expected_version": 0,
            "package_id": "context-api-package",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:context-api",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {
                "acceptance_criteria": ["ac-1"],
                "required_context": standing_context(),
            },
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "context-api-unit",
            "expected_version": 0,
            "unit_key": "context-api-unit",
            "title": "Context API",
            "outcome": "Context API works",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    authority = db_client.post(
        f"/api/v1/work-units/{unit.json()['id']}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": "context-api-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert authority.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit.json()['id']}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": "context-api-ready", "expected_version": 1},
    )
    assert ready.status_code == 200
    return str(unit.json()["id"])


def test_context_api_openapi_declares_preflight_and_snapshot_routes(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/preflight" in document["paths"]
    assert "/api/v1/work-units/{unit_id}/context-snapshots" in document["paths"]
    assert "PreflightCommandModel" in document["components"]["schemas"]
    assert "ContextSnapshotResponse" in document["components"]["schemas"]


def test_preflight_records_and_lists_context_snapshots(db_client: TestClient) -> None:
    unit_id = register_context_unit(db_client)

    result = db_client.post(
        f"/api/v1/work-units/{unit_id}/preflight",
        headers=WORKER,
        json={
            "idempotency_key": "context-api-preflight",
            "expected_version": 2,
            "standing_context": standing_context(),
            "purpose": "diagnostic",
        },
    )

    assert result.status_code == 200
    snapshot = result.json()
    assert snapshot["work_unit_id"] == unit_id
    assert snapshot["classification"] == "accepted"
    assert snapshot["decision"] == "accepted"
    listed = db_client.get(f"/api/v1/work-units/{unit_id}/context-snapshots", headers=WORKER)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [snapshot["id"]]


def test_claim_api_accepts_standing_context_and_returns_snapshot_id(
    db_client: TestClient,
) -> None:
    unit_id = register_context_unit(db_client)

    result = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={
            "idempotency_key": "context-api-claim",
            "expected_version": 2,
            "standing_context": standing_context(),
        },
    )

    assert result.status_code == 200
    assert result.json()["context_snapshot_id"]
