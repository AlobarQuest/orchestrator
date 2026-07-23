import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def ready_claimed_unit(db_client: TestClient, *, key: str = "infra-link-api"):
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
            "source_repository": "AlobarQuest/orchestrator",
            "revision": 1,
            "content_hash": f"sha256:{key}",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
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
            "idempotency_key": f"{key}-unit",
            "expected_version": 0,
            "unit_key": key,
            "title": "Infra link API",
            "outcome": "Infra linkage is recorded",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approved = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approved.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": f"{key}-ready", "expected_version": 1},
    )
    assert ready.status_code == 200
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": f"{key}-claim", "expected_version": 2},
    )
    assert claim.status_code == 200
    return unit_id, claim.json()


def infra_link_body(lease: dict[str, object], *, key: str = "infra-link-api-1"):
    return {
        "idempotency_key": key,
        "expected_version": 3,
        "attempt": lease["attempt"],
        "lease_token": lease["lease_token"],
        "status": "approved",
        "change_manager_ref": "change-manager:item:42",
        "change_manager_url": "https://change-manager.invalid/items/42",
        "infraops_ref": "infraops:window:2026-07-08",
        "approval_ref": "https://change-manager.invalid/items/42#approval",
        "rollback_ref": "https://infraops.invalid/windows/2026-07-08/rollback",
        "verify_ref": "https://infraops.invalid/windows/2026-07-08/verify",
        "final_evidence_ref": "s3://evidence/ws44/final.json",
        "payload": {"summary": "linked to existing infra lane"},
    }


def test_infra_link_api_declares_routes_and_schemas(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    path = "/api/v1/work-units/{unit_id}/infra-lane-links"
    assert path in document["paths"]
    assert "InfraLaneLinkCommandModel" in document["components"]["schemas"]
    assert "InfraLaneLinkResponse" in document["components"]["schemas"]


def test_worker_records_and_lists_infra_link(db_client: TestClient) -> None:
    unit_id, lease = ready_claimed_unit(db_client)

    first = db_client.post(
        f"/api/v1/work-units/{unit_id}/infra-lane-links",
        headers=WORKER,
        json=infra_link_body(lease),
    )
    replay = db_client.post(
        f"/api/v1/work-units/{unit_id}/infra-lane-links",
        headers=WORKER,
        json=infra_link_body(lease),
    )
    listing = db_client.get(f"/api/v1/work-units/{unit_id}/infra-lane-links", headers=WORKER)

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["status"] == "approved"
    assert first.json()["change_manager_ref"] == "change-manager:item:42"
    assert first.json()["infraops_ref"] == "infraops:window:2026-07-08"
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [first.json()["id"]]


def test_infra_link_api_rejects_wrong_lease(db_client: TestClient) -> None:
    unit_id, lease = ready_claimed_unit(db_client, key="infra-link-api-wrong-lease")
    body = infra_link_body(lease, key="infra-link-api-wrong-lease-1")
    body["lease_token"] = "wrong-token"

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/infra-lane-links",
        headers=WORKER,
        json=body,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "claim_not_owned"
