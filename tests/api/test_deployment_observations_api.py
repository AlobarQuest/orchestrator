from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER
from tests.api.test_release_artifacts_api import completed_unit, release_body

DIGEST = "sha256:" + "a" * 64


def observation_body(*, key: str = "deployment-api-observation") -> dict[str, object]:
    observed_at = datetime(2026, 7, 8, 20, 0, tzinfo=UTC).isoformat()
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "environment": "production",
        "base_url": "https://sds.alobar.net",
        "observed_artifact_digest": DIGEST,
        "deployment_ref": "coolify:eqj5l7k705fhi12x9i74fqf0:ws53",
        "deployment_url": "https://coolify.example.invalid/project/orchestrator/ws53",
        "deployer": "coolify",
        "observed_at": observed_at,
        "probe_summary": {
            "probes": [
                {
                    "name": "live",
                    "method": "GET",
                    "endpoint": "/health/live",
                    "expected_status_min": 200,
                    "expected_status_max": 299,
                    "status_code": 200,
                    "observed_at": observed_at,
                }
            ]
        },
        "route_summary": {
            "routes": [
                {
                    "path": "/api/v1/release-artifacts/{binding_id}/deployment-observations",
                    "present": True,
                }
            ]
        },
        "auth_summary": {"missing_m2m_status": 401, "configured_m2m_status": 200},
        "dispatch_summary": {"dispatch_enabled": False},
        "status_summary": {"status": "observed", "summary": "bounded"},
    }


def release_artifact(db_client: TestClient, migrated_engine: Engine) -> str:
    revision_id, unit_id = completed_unit(db_client, migrated_engine, key="deployment-api")
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="deployment-api-binding"),
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_deployment_observation_api_declares_routes_and_schemas(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    path = "/api/v1/release-artifacts/{binding_id}/deployment-observations"
    assert path in document["paths"]
    assert "DeploymentObservationCommandModel" in document["components"]["schemas"]
    assert "DeploymentObservationResponse" in document["components"]["schemas"]


def test_system_records_and_lists_deployment_observation(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)

    first = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=observation_body(),
    )
    replay = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=observation_body(),
    )
    listing = db_client.get(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["release_artifact_binding_id"] == binding_id
    assert first.json()["environment"] == "production"
    assert first.json()["observed_artifact_digest"] == DIGEST
    assert first.json()["post_deploy_work_unit_id"]
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [first.json()["id"]]


def test_deployment_observation_api_rejects_worker_verifier_and_conflict(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)

    worker = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=WORKER,
        json=observation_body(key="worker-observation"),
    )
    verifier = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=VERIFIER,
        json=observation_body(key="verifier-observation"),
    )
    first = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=observation_body(key="system-observation"),
    )
    conflict_body = observation_body(key="changed-observation")
    conflict_body["route_summary"] = {"routes": [{"path": "/health/live", "present": False}]}
    conflict = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=conflict_body,
    )

    assert worker.status_code == 403
    assert verifier.status_code == 403
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "deployment_observation_conflict"


def test_a_digest_mismatch_is_rejected_and_the_condition_survives_the_rollback(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The digest guard raises and the ingest service rolls back. A condition written inside that
    transaction would be erased along with the rejected observation -- so it is written at the
    route layer, in its own transaction. The ingest STAYS rejected."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from orchestrator.persistence.models import DeploymentObservation, ReconciliationCondition

    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key="digest-divergence-1") | {
        "observed_artifact_digest": "sha256:" + "f" * 64,  # not the bound digest
    }

    response = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=body,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "deployment_observation_digest_mismatch"
    with Session(migrated_engine) as session:
        # The ingest really was rejected: no observation, no post-deploy unit.
        assert list(session.scalars(select(DeploymentObservation))) == []
        # ...and the condition survived the service's rollback.
        rows = list(session.scalars(select(ReconciliationCondition)))
        assert [row.condition_type for row in rows] == ["digest_divergence"]
        assert rows[0].observed_state["observed_artifact_digest"] == "sha256:" + "f" * 64
        assert rows[0].stored_state["artifact_digest"] == DIGEST
