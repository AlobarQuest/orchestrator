import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import (
    AUTHORITY,
    HUMAN,
    RUNTIME_OBSERVER,
    SYSTEM,
    WORKER,
)


def create_revision(
    db_client: TestClient,
    *,
    key: str,
    package_id: str = "ws-p2.1-recovery-controls-drills",
) -> str:
    response = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": package_id,
            "source_repository": "AlobarQuest/orchestrator",
            "revision": 1,
            "content_hash": "sha256:production-drill",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 12, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def record_runtime_observation(db_client: TestClient, *, key: str) -> str:
    response = db_client.post(
        "/api/v1/runtime-observations",
        headers=RUNTIME_OBSERVER,
        json={
            "idempotency_key": f"{key}-runtime",
            "expected_version": 0,
            "container_id": "a" * 64,
            "configured_image_ref": "ghcr.io/alobarquest/orchestrator:production",
            "observed_image_digest": "ghcr.io/alobarquest/orchestrator@sha256:" + "a" * 64,
            "openapi_sha256": "sha256:" + "b" * 64,
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def start_body(
    revision_id: str,
    runtime_observation_id: str,
    *,
    key: str = "production-drill-1",
) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "idempotency_key": key,
        "expected_version": 0,
        "runtime_observation_id": runtime_observation_id,
    }


def test_production_drill_api_declares_start_and_read_routes(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/production-drills" in document["paths"]
    assert "/api/v1/production-drills/{run_id}" in document["paths"]
    assert "/api/v1/runtime-observations" in document["paths"]
    assert "StartProductionDrillCommand" in document["components"]["schemas"]
    assert "ProductionDrillRunResponse" in document["components"]["schemas"]
    assert "RuntimeObservationResponse" in document["components"]["schemas"]


def test_only_dedicated_runtime_observer_may_attest_runtime(
    db_client: TestClient,
) -> None:
    body = {
        "idempotency_key": "runtime-observer-auth",
        "expected_version": 0,
        "container_id": "a" * 64,
        "configured_image_ref": "ghcr.io/alobarquest/orchestrator:production",
        "observed_image_digest": "ghcr.io/alobarquest/orchestrator@sha256:" + "a" * 64,
        "openapi_sha256": "sha256:" + "b" * 64,
        "observed_at": datetime.now(UTC).isoformat(),
    }

    rejected = db_client.post("/api/v1/runtime-observations", headers=SYSTEM, json=body)
    accepted = db_client.post("/api/v1/runtime-observations", headers=RUNTIME_OBSERVER, json=body)
    replay = db_client.post("/api/v1/runtime-observations", headers=RUNTIME_OBSERVER, json=body)

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "role_forbidden"
    assert accepted.status_code == replay.status_code == 201
    assert accepted.json()["id"] == replay.json()["id"]


def test_human_starts_and_replays_authorized_production_drill(
    db_client: TestClient,
) -> None:
    revision_id = create_revision(db_client, key="production-drill-api")
    runtime_observation_id = record_runtime_observation(db_client, key="production-drill-api")

    first = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id),
    )
    replay = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id),
    )
    fetched = db_client.get(f"/api/v1/production-drills/{first.json()['id']}", headers=HUMAN)

    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["owner_actor_id"] == "devon"
    assert fetched.status_code == 200
    assert fetched.json() == first.json()


def test_production_drill_api_rejects_non_human_and_unapproved_revisions(
    db_client: TestClient,
) -> None:
    authorized_revision = create_revision(db_client, key="production-drill-api-role")
    runtime_observation_id = record_runtime_observation(db_client, key="production-drill-api-role")

    worker = db_client.post(
        "/api/v1/production-drills",
        headers=WORKER,
        json=start_body(authorized_revision, runtime_observation_id, key="worker"),
    )
    system = db_client.post(
        "/api/v1/production-drills",
        headers=SYSTEM,
        json=start_body(authorized_revision, runtime_observation_id, key="system"),
    )
    assert worker.status_code == system.status_code == 403
    assert (
        worker.json()["error"]["code"] == system.json()["error"]["code"] == "human_actor_required"
    )


def test_production_drill_api_rejects_another_approved_package(db_client: TestClient) -> None:
    revision_id = create_revision(
        db_client,
        key="production-drill-api-wrong-package",
        package_id="unrelated-approved-package",
    )
    runtime_observation_id = record_runtime_observation(
        db_client,
        key="production-drill-api-wrong-package",
    )

    response = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id, key="wrong-package"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "production_drill_package_required"


def test_production_drill_start_rejects_caller_owned_state(
    db_client: TestClient,
) -> None:
    revision_id = create_revision(db_client, key="production-drill-api-shape")
    runtime_observation_id = record_runtime_observation(db_client, key="production-drill-api-shape")

    response = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json={
            **start_body(revision_id, runtime_observation_id),
            "owner_actor_id": "system",
            "status": "closed",
        },
    )

    assert response.status_code == 422
