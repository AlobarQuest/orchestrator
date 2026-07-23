import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

HUMAN = {"X-Alobar-Proxy": "fixture-marker", "X-Alobar-Email": "devon@example.invalid"}
AUTHORITY = {
    "capabilities": {"repo.edit": "allowed"},
    "budgets": {"max_attempts": 3, "max_llm_calls": 4},
}
NOW = datetime(2026, 7, 5, tzinfo=UTC)


def intake_payload(**overrides: object) -> dict[str, object]:
    base = {
        "idempotency_key": "package-intake-1",
        "expected_version": 0,
        "package_id": "pkg-ws32",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:one",
        "source_path": "/tmp/pkg-ws32",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW.isoformat(),
        "approval_event_id": "evt-package-intake-1",
        "approval_ledger_commit": "a" * 40,
        "profile": "software",
        "status_at_intake": "approved",
        "verification_mode": "caller_attested_cli_verified",
        "verification_limitations": {
            "api_recomputes_remote_git_object": False,
            "cli_verified_local_package_hash": True,
            "cli_verified_approval_lineage": True,
        },
        "enforcement_snapshot": {
            "title": "Implement one",
            "outcome": "One works",
            "scope": {"in": ["feature"]},
            "dependencies": [],
            "applicable_standards": ["STD-1"],
        },
        "authority": AUTHORITY,
        "registry_version": 1,
        "acceptance_criteria": [
            {
                "ac_id": "AC-001",
                "condition": "The change is tested.",
                "evidence_type": "automated_test",
                "evidence": "gate: focused tests pass",
                "approver": "policy",
            },
            {
                "ac_id": "AC-002",
                "condition": "The review notes are captured.",
                "evidence_type": "human_review",
                "evidence": "review: reviewer confirmed coverage",
                "approver": "policy",
            },
        ],
    }
    return {**base, **overrides}


def unit_payload(**overrides: object) -> dict[str, object]:
    base = {
        "idempotency_key": "unit-1",
        "expected_version": 0,
        "unit_key": "unit-1",
        "title": "Implement one",
        "outcome": "One works",
        "required_capability": "repo.edit",
        "authority": AUTHORITY,
        "max_attempts": 3,
        "approved_by": "human-1",
        "approved_at": NOW.isoformat(),
    }
    return {**base, **overrides}


def revision_payload(**overrides: object) -> dict[str, object]:
    base = {
        "idempotency_key": "manual-revision-1",
        "expected_version": 0,
        "package_id": "pkg-manual",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:manual",
        "source_path": "/tmp/pkg-manual",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW.isoformat(),
        "approval_event_id": str(uuid.UUID(int=2)),
        "enforcement_snapshot": {"title": "Manual revision"},
        "authority": AUTHORITY,
        "registry_version": 1,
    }
    return {**base, **overrides}


def test_package_intake_post_returns_revision_identity(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_payload())

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["revision"] == 1
    assert body["intake_source"] == "package_cli"
    assert body["status_at_intake"] == "approved"
    assert body["approval_event_id"] == "evt-package-intake-1"


def test_package_intake_get_returns_persisted_intake_projection(db_client: TestClient) -> None:
    created = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_payload())
    revision_id = created.json()["id"]

    fetched = db_client.get(f"/api/v1/package-intakes/{revision_id}", headers=HUMAN)

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == revision_id
    assert body["package_id"] == "pkg-ws32"
    assert body["source_repository"] == "owner/repo"
    assert body["revision"] == 1
    assert body["profile"] == "software"
    assert body["status_at_intake"] == "approved"
    assert body["intake_source"] == "package_cli"
    assert body["approval_ledger_commit"] == "a" * 40
    assert body["verification_mode"] == "caller_attested_cli_verified"
    assert body["authority"] == {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": None,
        "conformance": None,
        "constraints": {},
        "unknown_fields": [],
    }
    assert [criterion["ac_id"] for criterion in body["acceptance_criteria"]] == [
        "AC-001",
        "AC-002",
    ]
    assert all(uuid.UUID(criterion["id"]) for criterion in body["acceptance_criteria"])


def test_package_intake_get_rejects_non_intaken_revision(db_client: TestClient) -> None:
    created = db_client.post("/api/v1/revisions", headers=HUMAN, json=revision_payload())
    revision_id = created.json()["id"]

    fetched = db_client.get(f"/api/v1/package-intakes/{revision_id}", headers=HUMAN)

    assert fetched.status_code == 404
    assert fetched.json()["error"]["code"] == "package_intake_not_found"


def test_direct_unit_registration_rejects_package_cli_revision_without_decomposition(
    db_client: TestClient,
) -> None:
    created = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_payload())
    revision_id = created.json()["id"]

    response = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json=unit_payload(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "decomposition_approval_required"
