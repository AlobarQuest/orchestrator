from fastapi.testclient import TestClient

from orchestrator.config import get_settings
from tests.api.test_lifecycle_api import HUMAN, WORKER
from tests.api.test_production_drills_api import (
    create_revision,
    record_runtime_observation,
    start_body,
)


def test_state_projection_is_run_scoped_and_workers_are_rejected(db_client: TestClient) -> None:
    revision_id = create_revision(db_client, key="drill-controls-state")
    runtime_observation_id = record_runtime_observation(db_client, key="drill-controls-state")
    first = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json={
            **start_body(revision_id, runtime_observation_id, key="controls-first"),
            "lease_duration_seconds": 61,
        },
    )
    second = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json=start_body(revision_id, runtime_observation_id, key="controls-second"),
    )

    state = db_client.get(f"/api/v1/production-drills/{first.json()['id']}/state", headers=HUMAN)
    worker = db_client.get(f"/api/v1/production-drills/{first.json()['id']}/state", headers=WORKER)

    assert state.status_code == 200
    assert state.json()["run_id"] == first.json()["id"]
    assert state.json()["run_id"] != second.json()["id"]
    assert state.json()["lease_duration_seconds"] == 61
    assert worker.status_code == 403


def test_deadline_controls_are_bounded_without_mutating_global_thresholds(
    db_client: TestClient,
) -> None:
    revision_id = create_revision(db_client, key="drill-controls-bounds")
    runtime_observation_id = record_runtime_observation(db_client, key="drill-controls-bounds")
    settings = get_settings()
    original = (
        settings.dead_letter_stalled_approval_seconds,
        settings.reconcile_split_brain_stall_seconds,
    )

    short = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json={
            **start_body(revision_id, runtime_observation_id, key="controls-short"),
            "lease_duration_seconds": 0,
        },
    )
    long = db_client.post(
        "/api/v1/production-drills",
        headers=HUMAN,
        json={
            **start_body(revision_id, runtime_observation_id, key="controls-long"),
            "reporting_deadline_seconds": settings.production_drill_max_deadline_seconds + 1,
        },
    )

    assert short.status_code == long.status_code == 409
    assert short.json()["error"]["code"] == "production_drill_deadline_too_short"
    assert long.json()["error"]["code"] == "production_drill_deadline_too_long"
    assert (
        settings.dead_letter_stalled_approval_seconds,
        settings.reconcile_split_brain_stall_seconds,
    ) == original
