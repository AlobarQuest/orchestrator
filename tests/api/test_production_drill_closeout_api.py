from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN, WORKER
from tests.api.test_production_drills_api import (
    create_revision,
    record_runtime_observation,
    start_body,
)


def close_body(*, reason: str = "reviewed") -> dict[str, object]:
    return {
        "idempotency_key": "close-production-drill",
        "expected_version": 0,
        "closure_reason": reason,
    }


def test_human_closes_empty_production_drill_and_replays_exactly(db_client: TestClient) -> None:
    revision_id = create_revision(db_client, key="production-drill-closeout")
    runtime_observation_id = record_runtime_observation(db_client, key="production-drill-closeout")
    run = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id),
    )
    assert run.status_code == 201

    first = db_client.post(
        f"/api/v1/production-drills/{run.json()['id']}/close",
        headers=HUMAN,
        json=close_body(),
    )
    replay = db_client.post(
        f"/api/v1/production-drills/{run.json()['id']}/close",
        headers=HUMAN,
        json=close_body(),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == "closed"
    assert replay.json() == first.json()


def test_close_route_requires_human_actor(db_client: TestClient) -> None:
    revision_id = create_revision(db_client, key="production-drill-closeout-role")
    runtime_observation_id = record_runtime_observation(
        db_client,
        key="production-drill-closeout-role",
    )
    run = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id),
    )
    assert run.status_code == 201

    response = db_client.post(
        f"/api/v1/production-drills/{run.json()['id']}/close",
        headers=WORKER,
        json=close_body(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "human_actor_required"
