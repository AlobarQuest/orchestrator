import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
AUTHORITY = {
    "capabilities": {
        "repo.read": "allowed",
        "repo.edit": "allowed",
        "command.run": "allowed",
        "github.pr.create": "allowed",
        "orchestrator.claim": "allowed",
        "orchestrator.evidence.write": "allowed",
    },
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
    "constraints": {
        "work_unit_id": "unit-1",
        "target_repository": "AlobarQuest/orchestrator",
        "allowed_commands": ["make check"],
    },
}


def test_runner_brief_requires_m2m_or_human_auth(db_client: TestClient) -> None:
    response = db_client.get(f"/api/v1/work-units/{uuid.uuid4()}/runner-brief")

    assert response.status_code == 401


def test_runner_brief_returns_canonical_unit_facts(db_client: TestClient) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-revision",
            "expected_version": 0,
            "package_id": "ws-4.1-pilot",
            "source_repository": "AlobarQuest/intent-packages",
            "revision": 1,
            "content_hash": "sha256:runner-brief",
            "source_path": "packages/ws-4.1/package.yaml",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": "evt-runner-brief",
            "enforcement_snapshot": {"required_context": {"capabilities": ["repository_write"]}},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    revision_id = revision.json()["id"]

    unit = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "runner-brief-unit",
            "expected_version": 0,
            "unit_key": "pilot",
            "title": "Pilot factory runner",
            "outcome": "Open a PR with evidence",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]

    response = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER)

    assert response.status_code == 200
    body = response.json()
    assert body["work_unit"]["id"] == unit_id
    assert body["work_unit"]["state"] == "draft"
    assert body["package"]["id"] == "ws-4.1-pilot"
    assert body["target"]["repository"] == "AlobarQuest/orchestrator"
    assert body["authority"]["envelope"] == AUTHORITY
    assert "token" not in str(body).lower()
