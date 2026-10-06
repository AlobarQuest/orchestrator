import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import DeploymentObservation
from orchestrator.services.release import deployment_observations
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


MACHINE_DIGEST = "sha256:" + "9" * 64


def machine_local_artifact(db_client: TestClient, migrated_engine: Engine) -> str:
    """A machine-local binding through the real route, so its observation meets the real gates."""
    revision_id, unit_id = completed_unit(db_client, migrated_engine, key="activation-api")
    body = release_body(revision_id, key="activation-api-binding")
    body.update(
        kind="machine_local",
        artifact_registry=None,
        artifact_repository=None,
        artifact_name=None,
        artifact_digest=MACHINE_DIGEST,
    )
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=body,
    )
    assert response.status_code == 201
    return response.json()["id"]


def activation_body(*, key: str = "activation-api-observation") -> dict[str, object]:
    """The producer's shape: no URLs, no deployer, no probe-shaped summaries."""
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "kind": "machine_local",
        "environment": "operator_machine",
        "observed_artifact_digest": MACHINE_DIGEST,
        "deployment_ref": "b" * 40,
        "observed_at": datetime(2026, 8, 23, 10, 34, 10, tzinfo=UTC).isoformat(),
        "activation_summary": {
            "merge_commit_present": "yes",
            "console_entry_points_present": "yes",
            "environment_matches_lock": "not_applicable",
        },
    }


def test_the_route_serves_a_machine_local_activation_with_its_summary(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    """A field the response model does not declare is silently dropped, so the assertion that
    matters is made against the SERVED body rather than against the service's return value."""
    binding_id = machine_local_artifact(db_client, migrated_engine)

    response = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=activation_body(),
    )

    assert response.status_code == 201
    served = response.json()
    assert served["kind"] == "machine_local"
    assert served["activation_summary"]["console_entry_points_present"] == "yes"
    assert served["base_url"] is None
    assert served["deployment_url"] is None
    assert served["deployer"] is None
    assert served["post_deploy_work_unit_id"] is None
    assert served["post_deploy_event_id"] is None


def test_the_route_refuses_an_activation_whose_binding_describes_a_hosted_image(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)
    body = activation_body(key="activation-on-a-hosted-binding")
    body["observed_artifact_digest"] = DIGEST

    response = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=body,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "deployment_observation_kind_mismatch"


OBSERVATIONS_PATH = "/api/v1/release-artifacts/{binding_id}/deployment-observations"

# Each declared summary and the properties the served document must publish for it.
DECLARED_SUMMARIES = {
    "ProbeSummary": {"probes"},
    "Probe": {"name", "endpoint", "method", "observed_at", "status_code"},
    "RouteSummary": {"routes"},
    "Route": {"path", "present"},
    "AuthSummary": {"missing_m2m_status", "configured_m2m_status"},
    "DispatchSummary": {"dispatch_enabled"},
    "StatusSummary": {"status", "summary"},
    "ActivationSummary": {
        "merge_commit_present",
        "console_entry_points_present",
        "environment_matches_lock",
    },
}


def test_the_served_document_publishes_every_summary_shape(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    for name, properties in DECLARED_SUMMARIES.items():
        assert set(schemas[name]["properties"]) == properties, name
        assert schemas[name]["additionalProperties"] is False, name
    command = schemas["DeploymentObservationCommandModel"]["properties"]
    refs = {
        field: {option.get("$ref") for option in command[field]["anyOf"]}
        for field in (
            "probe_summary",
            "route_summary",
            "auth_summary",
            "dispatch_summary",
            "status_summary",
            "activation_summary",
        )
    }
    assert refs["probe_summary"] == {"#/components/schemas/ProbeSummary", None}
    assert refs["activation_summary"] == {"#/components/schemas/ActivationSummary", None}
    assert schemas["AuthSummary"]["properties"]["missing_m2m_status"]["const"] == 401


def _post(db_client: TestClient, binding_id: str, body: dict[str, object]):
    return db_client.post(
        OBSERVATIONS_PATH.format(binding_id=binding_id), headers=SYSTEM, json=body
    )


def _error_locations(response) -> list[tuple[object, ...]]:
    return [tuple(error["loc"]) for error in response.json()["detail"]]


def test_the_route_refuses_an_undeclared_summary_key(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key="extra-summary-key")
    body["route_summary"] = {"routes": [{"path": "/health/live", "present": True}], "extra": 1}

    response = _post(db_client, binding_id, body)

    assert response.status_code == 422
    assert ("body", "route_summary", "extra") in _error_locations(response)


def test_the_route_refuses_the_retired_probe_range_fields(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key="retired-probe-fields")
    body["probe_summary"] = {
        "probes": [
            {
                "name": "live",
                "method": "GET",
                "endpoint": "/health/live",
                "status_code": 200,
                "observed_at": body["observed_at"],
                "expected_status_min": 200,
            }
        ]
    }

    response = _post(db_client, binding_id, body)

    assert response.status_code == 422
    assert ("body", "probe_summary", "probes", 0, "expected_status_min") in _error_locations(
        response
    )


@pytest.mark.parametrize("configured", [401, 403, 500])
def test_the_route_refuses_a_configured_m2m_status_other_than_200(
    db_client: TestClient, migrated_engine: Engine, configured: int
) -> None:
    """The evaluator requires 200 when the status is reported, so ingest does too."""
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key=f"configured-{configured}")
    body["auth_summary"] = {"missing_m2m_status": 401, "configured_m2m_status": configured}

    response = _post(db_client, binding_id, body)

    assert response.status_code == 422
    assert ("body", "auth_summary", "configured_m2m_status") in _error_locations(response)


def test_a_retry_that_omits_an_optional_key_replays_the_row_it_wrote(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The replay trap: the route hands the service exactly what was sent, so a default the
    model knows (`configured_m2m_status: None`) never appears in the stored payload and a
    byte-identical retry is a replay, not an `idempotency_conflict`."""
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key="omits-configured")
    body["auth_summary"] = {"missing_m2m_status": 401}
    del body["dispatch_summary"]

    first = _post(db_client, binding_id, body)
    retry = _post(db_client, binding_id, body)

    assert first.status_code == 201, first.json()
    assert retry.status_code == 201, retry.json()
    assert retry.json()["id"] == first.json()["id"]
    assert first.json()["auth_summary"] == {"missing_m2m_status": 401}
    assert first.json()["dispatch_summary"] == {}


def test_the_route_refuses_a_machine_local_row_carrying_a_hosted_summary(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    binding_id = machine_local_artifact(db_client, migrated_engine)
    body = activation_body(key="machine-local-with-probes")
    body["probe_summary"] = observation_body()["probe_summary"]

    response = _post(db_client, binding_id, body)

    # The wire admits either summary; the kind conditional is the service's, so a refusal.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "deployment_observation_invalid"
    assert "probe_summary" in response.json()["error"]["message"]


def test_the_route_refuses_a_hosted_row_carrying_an_activation_summary(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key="hosted-with-activation")
    body["activation_summary"] = activation_body()["activation_summary"]

    response = _post(db_client, binding_id, body)

    # The wire admits either summary; the kind conditional is the service's, so a refusal.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "deployment_observation_invalid"
    assert "activation summary" in response.json()["error"]["message"]


def test_a_row_stored_before_the_declared_shapes_is_still_served_and_verified(
    db_client: TestClient, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why response summaries stay plain dicts. Rows written before 5b carry the retired
    `expected_status_min/max`; a typed `extra="forbid"` response model would fail to serve them,
    and the verifier must still judge their evidence. The row is written the way the pre-change
    service admitted it: through the service, with the probe shape check stood down."""
    binding_id = release_artifact(db_client, migrated_engine)
    real = deployment_observations._validate_summary

    def pre_change(model: type, payload: dict[str, Any], label: str) -> None:
        if model is not deployment_observations.ProbeSummary:
            real(model, payload, label)

    monkeypatch.setattr(deployment_observations, "_validate_summary", pre_change)
    old_probe = {
        "name": "live",
        "method": "GET",
        "endpoint": "/health/live",
        "status_code": 200,
        "observed_at": datetime(2026, 7, 8, 20, 0, tzinfo=UTC).isoformat(),
        "expected_status_min": 200,
        "expected_status_max": 299,
    }
    with Session(migrated_engine) as session:
        row = deployment_observations.record_deployment_observation(
            session,
            deployment_observations.DeploymentObservationCommand(
                release_artifact_binding_id=uuid.UUID(binding_id),
                actor=ActorContext("system", ActorRole.SYSTEM),
                environment="production",
                base_url="https://sds.alobar.net",
                observed_artifact_digest=DIGEST,
                deployment_ref="pre-change",
                deployment_url="https://coolify.example.invalid/pre-change",
                deployer="coolify",
                observed_at=datetime(2026, 7, 8, 20, 0, tzinfo=UTC),
                probe_summary={"probes": [old_probe]},
                route_summary={"routes": [{"path": "/health/live", "present": True}]},
                auth_summary={"missing_m2m_status": 401, "configured_m2m_status": 200},
                dispatch_summary={},
                status_summary={"status": "observed", "summary": "bounded"},
                idempotency_key="pre-change-row",
            ),
        )
        assert isinstance(row, DeploymentObservation)
        post_deploy_unit_id = str(row.post_deploy_work_unit_id)
    monkeypatch.undo()

    listing = db_client.get(OBSERVATIONS_PATH.format(binding_id=binding_id), headers=SYSTEM)
    verified = db_client.post(
        f"/api/v1/work-units/{post_deploy_unit_id}/verify",
        headers=VERIFIER,
        json={"idempotency_key": "verify-pre-change-row", "expected_version": 1},
    )

    assert listing.status_code == 200
    [served] = listing.json()
    assert served["probe_summary"]["probes"][0]["expected_status_min"] == 200
    assert served["probe_summary"]["probes"][0]["expected_status_max"] == 299
    assert verified.status_code == 200, verified.json()
    assert verified.json()["result"] == "completed"


def test_an_explicit_empty_summary_is_the_absent_one_on_both_kinds(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """`{}` meant "not sent" when the summaries were dicts, and still does: a hosted row may
    send an empty activation and dispatch summary, a machine-local row empty hosted ones, and a
    retry that omits them instead replays the same row."""
    hosted_binding = release_artifact(db_client, migrated_engine)
    hosted = observation_body(key="hosted-explicit-empty")
    hosted["activation_summary"] = {}
    hosted["dispatch_summary"] = {}
    machine_binding = machine_local_artifact(db_client, migrated_engine)
    machine = activation_body(key="machine-explicit-empty")
    for name in (
        "probe_summary",
        "route_summary",
        "auth_summary",
        "dispatch_summary",
        "status_summary",
    ):
        machine[name] = {}

    hosted_first = _post(db_client, hosted_binding, hosted)
    machine_first = _post(db_client, machine_binding, machine)
    del hosted["activation_summary"], hosted["dispatch_summary"]
    hosted_retry = _post(db_client, hosted_binding, hosted)
    machine_retry = _post(db_client, machine_binding, activation_body(key="machine-explicit-empty"))

    assert hosted_first.status_code == 201, hosted_first.json()
    assert machine_first.status_code == 201, machine_first.json()
    assert hosted_retry.json()["id"] == hosted_first.json()["id"]
    assert machine_retry.json()["id"] == machine_first.json()["id"]
    assert hosted_first.json()["activation_summary"] == {}
    assert machine_first.json()["probe_summary"] == {}


@pytest.mark.parametrize(
    "auth_summary",
    [
        {"missing_m2m_status": 401.0},
        {"missing_m2m_status": 401, "configured_m2m_status": 200.0},
    ],
    ids=["missing-as-float", "configured-as-float"],
)
def test_the_route_refuses_an_m2m_status_sent_as_a_float(
    db_client: TestClient, migrated_engine: Engine, auth_summary: dict[str, object]
) -> None:
    """A `Literal` compares by equality, so 401.0 would pass it and be stored as sent."""
    binding_id = release_artifact(db_client, migrated_engine)
    body = observation_body(key=f"float-{len(auth_summary)}")
    body["auth_summary"] = auth_summary

    response = _post(db_client, binding_id, body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["body", "auth_summary"]
