from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from orchestrator.services.knowledge_promotions import BrainProposalResult
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER

OBSERVED_AT = datetime(2026, 7, 9, 14, 0, tzinfo=UTC).isoformat()


def observation_body(key: str = "kp-observation") -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": "github",
        "source_reference": f"github:AlobarQuest/orchestrator:run:{key}",
        "source_url": "https://github.com/AlobarQuest/orchestrator/actions/runs/30000000000",
        "trust_classification": "delivery_system",
        "subject_type": "repo",
        "subject_reference": "AlobarQuest/orchestrator",
        "environment": None,
        "observation_type": "github_check",
        "status": "failed",
        "severity": "warning",
        "observed_at": OBSERVED_AT,
        "summary": "Event publication queue failed because runtime packaging missed a module",
        "facts": {
            "workflow": "Quality",
            "failure_class": "runtime_packaging",
            "normalized_module": "factory_events",
        },
        "payload_digest": None,
    }


def proposal_body(observation_id: str, key: str = "kp-proposal") -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "correlation_identity": "ws62:event-publication-runtime-packaging",
        "source_observation_ids": [observation_id],
        "correlation_summary": (
            "A bounded github_check observation shows the event publication runtime missed "
            "digest-covered factory event modules."
        ),
        "target_brain": "code",
        "target_type": "lesson",
        "authority": "recommended",
        "applicability": {"road_slug": "delivery-runtime"},
        "proposed_payload": {
            "title": "Package runtime dependencies with digest coverage",
            "content": (
                "When deployment code imports generated event modules, include those modules in "
                "the runtime image and artifact digest coverage before closeout."
            ),
            "road_slug": "delivery-runtime",
            "tags": ["ws62", "runtime-packaging"],
            "source_app": "orchestrator",
            "proposed_by": "devon",
        },
        "provenance": {"source": "orchestrator_observations"},
    }


def _record_observation(db_client: TestClient, key: str = "kp-observation") -> str:
    response = db_client.post("/api/v1/observations", headers=SYSTEM, json=observation_body(key))
    assert response.status_code == 201
    return response.json()["id"]


def test_knowledge_promotion_routes_declared(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/knowledge-promotion-proposals" in document["paths"]
    assert (
        "/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain" in document["paths"]
    )
    assert "KnowledgePromotionProposalCommandModel" in document["components"]["schemas"]


def test_human_creates_replays_lists_and_conflicts(db_client: TestClient) -> None:
    observation_id = _record_observation(db_client)

    first = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body(observation_id),
    )
    replay = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body(observation_id),
    )
    listed = db_client.get(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        params={"target_brain": "code", "state": "proposed"},
    )
    changed = proposal_body(observation_id, key="kp-proposal-changed")
    changed_payload = changed["proposed_payload"]
    assert isinstance(changed_payload, dict)
    changed["proposed_payload"] = {
        **changed_payload,
        "title": "Changed proposal title",
    }
    conflict = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=changed,
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["state"] == "proposed"
    assert first.json()["source_observation_ids"] == [observation_id]
    assert first.json()["source_observation_hashes"][0].startswith("sha256:")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [first.json()["id"]]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "knowledge_promotion_conflict"


def test_rejects_worker_missing_observation_and_unsupported_target(db_client: TestClient) -> None:
    observation_id = _record_observation(db_client, "kp-observation-reject")
    worker = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=WORKER,
        json=proposal_body(observation_id, "kp-worker"),
    )
    missing = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body("00000000-0000-0000-0000-000000000001", "kp-missing"),
    )
    unsupported_body = proposal_body(observation_id, "kp-unsupported")
    unsupported_body["target_brain"] = "open"
    unsupported = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=unsupported_body,
    )

    assert worker.status_code == 403
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "observation_not_found"
    assert unsupported.status_code == 409
    assert unsupported.json()["error"]["code"] == "knowledge_promotion_invalid"


def test_human_submits_to_brain_as_proposed_record(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_id = _record_observation(db_client, "kp-observation-submit")
    created = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body(observation_id, "kp-proposal-submit"),
    )
    proposal_id = created.json()["id"]
    calls = []

    class FakeBrainProposalClient:
        def __init__(self, **kwargs):
            pass

        def submit(self, proposal):
            calls.append((proposal.target_brain, proposal.target_type, proposal.authority))
            return BrainProposalResult(
                record_id="42",
                status="proposed",
                response={"lesson": {"id": 42, "status": "proposed"}},
            )

    import orchestrator.api.routes as routes

    monkeypatch.setattr(routes, "HttpBrainProposalClient", FakeBrainProposalClient)
    submitted = db_client.post(
        f"/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
        headers=HUMAN,
        json={"idempotency_key": "kp-submit", "expected_version": 0},
    )
    replay = db_client.post(
        f"/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
        headers=HUMAN,
        json={"idempotency_key": "kp-submit-replay", "expected_version": 0},
    )
    listed = db_client.get(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        params={"state": "submitted_to_brain"},
    )

    assert submitted.status_code == 200
    assert submitted.json()["action"] == "submitted_to_brain"
    assert submitted.json()["brain_status"] == "proposed"
    assert replay.status_code == 200
    assert replay.json()["id"] == submitted.json()["id"]
    assert calls == [("code", "lesson", "recommended")]
    assert [row["id"] for row in listed.json()] == [proposal_id]


def test_event_publication_maps_knowledge_promotion_events(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_id = _record_observation(db_client, "kp-observation-event")
    created = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body(observation_id, "kp-proposal-event"),
    )
    proposal_id = created.json()["id"]

    queued = db_client.post(
        "/api/v1/event-publications/queue",
        headers=HUMAN,
        json={
            "idempotency_key": "kp-event-publication",
            "expected_version": 0,
            "source_kind": "event",
            "source_id": created.json()["event_id"],
        },
    )

    assert queued.status_code == 200
    row = queued.json()[0]
    assert row["source_action"] == "knowledge_promotion.proposed"
    assert row["status"] == "pending"
    assert row["source_id"] == created.json()["event_id"]
    assert proposal_id


def test_knowledge_promotion_tables_are_append_only(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    observation_id = _record_observation(db_client, "kp-observation-append-only")
    created = db_client.post(
        "/api/v1/knowledge-promotion-proposals",
        headers=HUMAN,
        json=proposal_body(observation_id, "kp-proposal-append-only"),
    )
    assert created.status_code == 201

    with Session(migrated_engine) as session:
        with pytest.raises(DatabaseError):
            session.execute(
                text(
                    "UPDATE knowledge_promotion_proposals "
                    "SET correlation_summary = 'changed' WHERE id = :id"
                ),
                {"id": created.json()["id"]},
            )
