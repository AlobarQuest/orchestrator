import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def _register_ready_unit(db_client: TestClient, suffix: str = "") -> str:
    key_suffix = f"-{suffix}" if suffix else ""
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"ledger-api-revision{key_suffix}",
            "expected_version": 0,
            "package_id": f"ledger-api{key_suffix}",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": f"sha256:ledger-api{key_suffix}",
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
            "idempotency_key": f"ledger-api-unit{key_suffix}",
            "expected_version": 0,
            "unit_key": f"ledger-api-unit{key_suffix}",
            "title": f"Ledger API unit{key_suffix}",
            "outcome": "Ledger API is inspectable",
            "required_capability": "repository_write",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approved = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"ledger-api-authority{key_suffix}",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approved.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": f"ledger-api-ready{key_suffix}", "expected_version": 1},
    )
    assert ready.status_code == 200
    return unit_id


def test_status_ledger_get_returns_read_only_projection(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client)
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "ledger-api-claim", "expected_version": 2},
    )
    assert claim.status_code == 200

    response = db_client.get("/api/v1/status-ledger", headers=HUMAN)

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    row = next(item for item in payload if item["unit_id"] == unit_id)
    assert row["actor_id"] == "worker"
    assert row["unit_key"] == "ledger-api-unit"
    assert row["unit_title"] == "Ledger API unit"
    assert row["unit_state"] == "claimed"
    assert row["claim_id"] == claim.json()["claim_id"]
    assert row["claim_attempt"] == 1
    assert row["claim_lease_expires_at"] == claim.json()["expires_at"]
    assert row["claim_released_at"] is None
    assert row["claim_terminal_reason"] is None
    assert row["blockers"] == []
    assert row["pending_human_approvals"] == []
    assert row["latest_evidence"] is None
    assert row["latest_adjudication"] is None
    assert row["last_failure"] is None


def test_status_ledger_get_applies_filters(db_client: TestClient) -> None:
    first_unit_id = _register_ready_unit(db_client, "first")
    first_claim = db_client.post(
        f"/api/v1/work-units/{first_unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "ledger-api-claim", "expected_version": 2},
    )
    assert first_claim.status_code == 200

    second_unit_id = _register_ready_unit(db_client, "second")
    second_claim = db_client.post(
        f"/api/v1/work-units/{second_unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "ledger-api-claim-2", "expected_version": 2},
    )
    assert second_claim.status_code == 200

    response = db_client.get(
        "/api/v1/status-ledger",
        headers=HUMAN,
        params={
            "actor_id": "worker",
            "work_unit_id": second_unit_id,
            "state": "claimed",
            "include_inactive": "true",
        },
    )

    assert response.status_code == 200
    assert [row["unit_id"] for row in response.json()] == [second_unit_id]


def test_status_ledger_has_no_mutation_routes() -> None:
    document = TestClient(__import__("orchestrator.main").main.app).get("/openapi.json").json()

    operations = document["paths"]["/api/v1/status-ledger"]
    assert set(operations) == {"get"}


def test_status_ledger_openapi_exposes_nullable_claim_release_properties() -> None:
    document = TestClient(__import__("orchestrator.main").main.app).get("/openapi.json").json()

    properties = document["components"]["schemas"]["StatusLedgerRowResponse"]["properties"]
    assert properties["claim_released_at"] == {
        "anyOf": [
            {"type": "string", "format": "date-time"},
            {"type": "null"},
        ],
        "title": "Claim Released At",
    }
    assert properties["claim_terminal_reason"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": "Claim Terminal Reason",
    }
