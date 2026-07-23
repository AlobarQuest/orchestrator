import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SOURCE_COMMIT = "4cd4132" + "d" * 33
MERGE_COMMIT = "4cd4132" + "c" * 33
PACKAGE_HASH = "sha256:release-api"


def completed_unit(db_client: TestClient, migrated_engine: Engine, *, key: str = "release-api"):
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-revision",
            "expected_version": 0,
            "package_id": f"{key}-package",
            "source_repository": "AlobarQuest/orchestrator",
            "revision": 1,
            "content_hash": PACKAGE_HASH,
            "source_path": "intent.md",
            "source_commit": SOURCE_COMMIT,
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
            "title": "Release API",
            "outcome": "Release artifact is recorded",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    with Session(migrated_engine) as session:
        stored = session.get(WorkUnit, unit_id)
        assert stored is not None
        stored.state = WorkUnitState.COMPLETED
        session.commit()
    return revision.json()["id"], unit_id


def release_body(revision_id: str, *, key: str = "release-api-binding") -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 1,
        "package_revision_id": revision_id,
        "package_revision_hash": PACKAGE_HASH,
        "source_repository": "AlobarQuest/orchestrator",
        "implementation_pr_number": 20,
        "source_commit": SOURCE_COMMIT,
        "merge_commit": MERGE_COMMIT,
        "artifact_registry": "ghcr.io",
        "artifact_repository": "alobarquest/orchestrator",
        "artifact_name": "orchestrator",
        "artifact_digest": DIGEST,
        "artifact_tag": "a04d094-ws52",
        "workflow_run_id": "123456789",
        "workflow_run_attempt": 1,
        "workflow_path": ".github/workflows/build.yml",
        "workflow_ref": "refs/heads/main",
        "workflow_run_url": "https://github.com/AlobarQuest/orchestrator/actions/runs/123456789",
        "builder_id": "github-actions",
        "builder_class": "github-hosted",
        "provenance_ref": "ghcr.io/alobarquest/orchestrator@sha256:" + "c" * 64,
        "provenance_digest": "sha256:" + "d" * 64,
        "sbom_ref": "ghcr.io/alobarquest/orchestrator/sbom@sha256:" + "e" * 64,
        "sbom_digest": "sha256:" + "f" * 64,
        "summary": {"status": "published"},
    }


def test_release_artifact_api_declares_routes_and_schemas(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    path = "/api/v1/work-units/{unit_id}/release-artifacts"
    assert path in document["paths"]
    assert "ReleaseArtifactCommandModel" in document["components"]["schemas"]
    assert "ReleaseArtifactResponse" in document["components"]["schemas"]


def test_system_records_and_lists_release_artifact(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, unit_id = completed_unit(db_client, migrated_engine)

    first = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id),
    )
    replay = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id),
    )
    listing = db_client.get(f"/api/v1/work-units/{unit_id}/release-artifacts", headers=SYSTEM)

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["artifact_digest"] == DIGEST
    assert first.json()["package_revision_hash"] == PACKAGE_HASH
    assert first.json()["workflow_run_id"] == "123456789"
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [first.json()["id"]]


def test_release_artifact_api_rejects_worker_and_digest_conflict(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, unit_id = completed_unit(db_client, migrated_engine, key="release-api-conflict")

    worker = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=WORKER,
        json=release_body(revision_id, key="worker-release"),
    )
    first = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="first-release"),
    )
    changed = release_body(revision_id, key="changed-digest")
    changed["artifact_digest"] = OTHER_DIGEST
    conflict = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=changed,
    )

    assert worker.status_code == 403
    assert worker.json()["error"]["code"] == "role_forbidden"
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "release_artifact_conflict"
