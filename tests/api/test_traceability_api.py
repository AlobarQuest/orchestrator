import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    TraceabilityAnchorResponse,
    TraceabilityChainResponse,
    TraceabilityDeploymentHop,
    TraceabilityIntentHop,
    TraceabilityResponse,
    TraceabilityUnitHop,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.api.test_release_artifacts_api import DIGEST, completed_unit, release_body


def _record_a_signal(migrated_engine: Engine) -> uuid.UUID:
    """A repository-scoped fact, written through the service rather than posted.

    The route under test is the READ; how the row arrives is not its subject, and going through
    the service keeps this test from also depending on the observation ingress's request shape.
    """
    with Session(migrated_engine) as session:
        result = record_observation(
            session,
            ObservationCommand(
                actor=ActorContext("system", ActorRole.SYSTEM),
                source_system="github",
                source_reference=f"signal:{uuid.uuid4()}",
                source_url=None,
                trust_classification="delivery_system",
                subject_type="repo",
                subject_reference="AlobarQuest/intent-packages",
                environment=None,
                observation_type="github_check",
                status="observed",
                severity="info",
                observed_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
                summary="A dependency update is available",
                facts={"package": "ruff"},
                payload_digest=None,
                idempotency_key=f"api-obs-{uuid.uuid4()}",
                expected_version=0,
            ),
        )
        assert not isinstance(result, DomainError), result
        return result.id


def test_traceability_response_is_json_serializable():
    chain = TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=1,
            content_hash="sha256:x",
            source_path="intent.md",
            source_commit="a" * 40,
            registered_by="human-1",
        ),
        unit=TraceabilityUnitHop(
            id=__import__("uuid").UUID(int=1),
            unit_key="u-1",
            title="Unit 1",
            state="completed",
            authority_fingerprint="fp",
            authority_approved_by="human-1",
            authority_decision="approved",
        ),
        pr=None,
        commit=[],
        artifact=[],
        deployment=[
            TraceabilityDeploymentHop(
                environment="prod",
                kind="container_image",
                observed_artifact_digest="sha256:d",
                digest_matches=True,
                deployment_ref="ref",
                deployment_url="https://x",
                deployer="deployer-1",
                observed_at=datetime(2026, 7, 25, tzinfo=UTC),
                status_summary={"code": 200},
                probe_summary={},
                activation_summary={},
            )
        ],
        conditions=[],
        observations=[],
    )
    response = TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on="environment", value="prod"),
        chains=[chain],
    )
    dumped = response.model_dump(mode="json")
    assert dumped["anchor"]["matched_on"] == "environment"
    assert dumped["chains"][0]["deployment"][0]["digest_matches"] is True


def test_traceability_bad_observation_uuid_is_clean_4xx_not_500(db_client: TestClient) -> None:
    """ADR-0026 amendment 1. The anchor builder wraps its parse: an unwrapped `uuid.UUID(bad)`
    raises `ValueError`, which has no registered handler and reaches the wire as a bare 500."""
    response = db_client.get(
        "/api/v1/traceability", headers=WORKER, params={"observation_id": "not-a-uuid"}
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "invalid_observation_id"


def test_traceability_unknown_observation_is_refused(db_client: TestClient) -> None:
    """A lookup, so the 404 mapping IS right here -- unlike the intake refusal, where a 404 from
    a POST would be indistinguishable from a route the deployed image does not serve."""
    response = db_client.get(
        "/api/v1/traceability",
        headers=WORKER,
        params={"observation_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "observation_not_found"


def test_traceability_observation_anchor_round_trip(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """An observation nobody has acted on yet resolves to an empty chain list, which is an
    ordinary answer -- and the existence check above is what stops it meaning two things."""
    observation_id = _record_a_signal(migrated_engine)

    response = db_client.get(
        "/api/v1/traceability", headers=WORKER, params={"observation_id": str(observation_id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["anchor"] == {"matched_on": "observation", "value": str(observation_id)}
    assert body["chains"] == []


def test_the_served_intent_hop_declares_the_originating_observation(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """A FastAPI `response_model` silently DROPS every key it does not declare, so the service
    can set this and the consumer receive nothing. Read at the wire, which is where it is lost."""
    revision_id, unit_id = completed_unit(db_client, migrated_engine, key="intent-hop-observation")
    created = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="intent-hop-observation-binding"),
    )
    assert created.status_code == 201

    response = db_client.get(
        "/api/v1/traceability", headers=WORKER, params={"work_unit_id": unit_id}
    )

    assert response.status_code == 200
    assert "originating_observation_id" in response.json()["chains"][0]["intent"]


def test_traceability_requires_auth(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/traceability", params={"environment": "prod"})
    assert response.status_code == 401


def test_traceability_no_anchor_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/traceability", headers=WORKER)

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "traceability_anchor_required"


def test_traceability_two_anchors_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get(
        "/api/v1/traceability",
        headers=WORKER,
        params={"environment": "prod", "commit": "a" * 40},
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "traceability_anchor_ambiguous"


def test_traceability_source_repository_without_pr_number_is_clean_4xx_not_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        "/api/v1/traceability",
        headers=WORKER,
        params={"environment": "prod", "source_repository": "AlobarQuest/orchestrator"},
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "traceability_anchor_invalid"


def test_traceability_bad_uuid_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get(
        "/api/v1/traceability", headers=WORKER, params={"work_unit_id": "not-a-uuid"}
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "invalid_work_unit_id"


def test_traceability_bad_commit_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/traceability", headers=WORKER, params={"commit": "xyz"})

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "invalid_commit"


def test_traceability_environment_roundtrip_empty(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/traceability", headers=WORKER, params={"environment": "prod"})

    assert response.status_code == 200
    body = response.json()
    assert body["anchor"] == {"matched_on": "environment", "value": "prod"}
    assert body["chains"] == []


def test_traceability_work_unit_round_trip_returns_a_non_empty_chain(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, unit_id = completed_unit(db_client, migrated_engine, key="traceability-positive")
    created = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="traceability-positive-binding"),
    )
    assert created.status_code == 201

    response = db_client.get(
        "/api/v1/traceability", headers=WORKER, params={"work_unit_id": unit_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["anchor"] == {"matched_on": "work_unit", "value": unit_id}
    assert len(body["chains"]) == 1
    chain = body["chains"][0]
    assert chain["unit"]["id"] == unit_id
    assert chain["artifact"][0]["artifact_digest"] == DIGEST
