import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Approval, WorkPackageRevision
from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER


def create_revision(db_client: TestClient, *, key: str) -> str:
    response = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
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


def authorize_revision(engine: Engine, revision_id: str) -> None:
    with Session(engine) as session:
        revision = session.get(WorkPackageRevision, uuid.UUID(revision_id))
        assert revision is not None
        session.add(
            Approval(
                subject_type="authority",
                subject_id=uuid.UUID(revision_id),
                subject_revision_or_fingerprint=revision.authority_fingerprint,
                decision="approved",
                approved_by="devon",
                reason="Production drill authorized",
                event_id=uuid.uuid4(),
                idempotency_key=f"production-drill-approval-{revision_id}",
            )
        )
        session.commit()


def start_body(revision_id: str, *, key: str = "production-drill-1") -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "idempotency_key": key,
        "expected_version": 0,
        "image_ref": "ghcr.io/alobarquest/orchestrator:production",
        "image_digest": "sha256:" + "a" * 64,
        "openapi_digest": "sha256:" + "b" * 64,
    }


def test_production_drill_api_declares_start_and_read_routes(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/production-drills" in document["paths"]
    assert "/api/v1/production-drills/{run_id}" in document["paths"]
    assert "StartProductionDrillCommand" in document["components"]["schemas"]
    assert "ProductionDrillRunResponse" in document["components"]["schemas"]


def test_human_starts_and_replays_authorized_production_drill(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id = create_revision(db_client, key="production-drill-api")
    authorize_revision(migrated_engine, revision_id)

    first = db_client.post("/api/v1/production-drills", headers=HUMAN, json=start_body(revision_id))
    replay = db_client.post(
        "/api/v1/production-drills", headers=HUMAN, json=start_body(revision_id)
    )
    fetched = db_client.get(f"/api/v1/production-drills/{first.json()['id']}", headers=HUMAN)

    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["owner_actor_id"] == "devon"
    assert fetched.status_code == 200
    assert fetched.json() == first.json()


def test_production_drill_api_rejects_non_human_and_unapproved_revisions(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    authorized_revision = create_revision(db_client, key="production-drill-api-role")
    authorize_revision(migrated_engine, authorized_revision)
    unapproved_revision = create_revision(db_client, key="production-drill-api-unapproved")

    worker = db_client.post(
        "/api/v1/production-drills",
        headers=WORKER,
        json=start_body(authorized_revision, key="worker"),
    )
    system = db_client.post(
        "/api/v1/production-drills",
        headers=SYSTEM,
        json=start_body(authorized_revision, key="system"),
    )
    unapproved = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(unapproved_revision, key="unapproved"),
    )

    assert worker.status_code == system.status_code == 403
    assert (
        worker.json()["error"]["code"] == system.json()["error"]["code"] == "human_actor_required"
    )
    assert unapproved.status_code == 409
    assert unapproved.json()["error"]["code"] == "production_drill_authority_approval_required"


def test_production_drill_start_rejects_caller_owned_state(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id = create_revision(db_client, key="production-drill-api-shape")
    authorize_revision(migrated_engine, revision_id)

    response = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json={**start_body(revision_id), "owner_actor_id": "system", "status": "closed"},
    )

    assert response.status_code == 422
