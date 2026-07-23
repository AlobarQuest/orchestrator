from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER

OBSERVED_AT = datetime(2026, 7, 8, 22, 30, tzinfo=UTC).isoformat()


def observation_body(*, key: str = "observation-api") -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": "github",
        "source_reference": "github:AlobarQuest/orchestrator:check:28981229890",
        "source_url": "https://github.com/AlobarQuest/orchestrator/actions/runs/28981229890",
        "trust_classification": "delivery_system",
        "subject_type": "repo",
        "subject_reference": "AlobarQuest/orchestrator",
        "environment": None,
        "observation_type": "github_check",
        "status": "passed",
        "severity": "info",
        "observed_at": OBSERVED_AT,
        "summary": "Quality workflow passed",
        "facts": {
            "workflow": "Quality",
            "run_id": "28981229890",
            "head_sha": "a6161e603686d8e85a4e7e80e4cdee30a624be79",
            "conclusion": "success",
            "attempt": 1,
        },
        "payload_digest": None,
    }


def uptime_body(*, key: str = "uptime-api") -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": "uptime_monitor",
        "source_reference": "uptime:sds-live",
        "source_url": "https://status.example.invalid/monitors/sds-live",
        "trust_classification": "monitor",
        "subject_type": "endpoint",
        "subject_reference": "https://sds.alobar.net/health/live",
        "environment": "production",
        "observation_type": "uptime",
        "status": "healthy",
        "severity": "info",
        "observed_at": OBSERVED_AT,
        "summary": "Live endpoint healthy",
        "facts": {"status_code": 200, "duration_ms": 83},
        "payload_digest": "sha256:" + "2" * 64,
    }


def test_observation_api_declares_routes_and_schemas(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/observations" in document["paths"]
    assert "ObservationCommandModel" in document["components"]["schemas"]
    assert "ObservationResponse" in document["components"]["schemas"]


def test_system_records_replays_and_lists_observations(db_client: TestClient) -> None:
    first = db_client.post("/api/v1/observations", headers=SYSTEM, json=observation_body())
    replay = db_client.post("/api/v1/observations", headers=SYSTEM, json=observation_body())
    uptime = db_client.post("/api/v1/observations", headers=SYSTEM, json=uptime_body())
    all_rows = db_client.get("/api/v1/observations", headers=SYSTEM)
    filtered = db_client.get(
        "/api/v1/observations",
        headers=SYSTEM,
        params={
            "source_system": "uptime_monitor",
            "subject_type": "endpoint",
            "environment": "production",
            "observation_type": "uptime",
            "observed_from": OBSERVED_AT,
        },
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert uptime.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["source_system"] == "github"
    assert first.json()["normalized_fact_hash"].startswith("sha256:")
    assert all_rows.status_code == 200
    assert [row["id"] for row in all_rows.json()] == [first.json()["id"], uptime.json()["id"]]
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()] == [uptime.json()["id"]]


def test_observation_api_rejects_missing_auth_worker_verifier_and_conflict(
    db_client: TestClient,
) -> None:
    missing = db_client.post("/api/v1/observations", json=observation_body(key="missing-auth"))
    worker = db_client.post(
        "/api/v1/observations",
        headers=WORKER,
        json=observation_body(key="worker-observation"),
    )
    verifier = db_client.post(
        "/api/v1/observations",
        headers=VERIFIER,
        json=observation_body(key="verifier-observation"),
    )
    first = db_client.post(
        "/api/v1/observations",
        headers=SYSTEM,
        json=observation_body(key="system-observation"),
    )
    changed = observation_body(key="changed-observation")
    facts = changed["facts"]
    assert isinstance(facts, dict)
    changed["facts"] = {**facts, "conclusion": "failure"}
    conflict = db_client.post("/api/v1/observations", headers=SYSTEM, json=changed)

    assert missing.status_code == 401
    assert worker.status_code == 403
    assert verifier.status_code == 403
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "observation_conflict"


def test_observation_route_records_a_reconciliation_condition_after_commit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """AC-001 end to end: a merged PR pushed for a not-yet-completed unit raises the
    never-auto-merge alarm on ingest -- and merges nothing, completes nothing."""
    from orchestrator.kernel.states import ActorRole
    from orchestrator.persistence.models import ReconciliationCondition, WorkUnit
    from orchestrator.services.lifecycle import ActorContext
    from orchestrator.services.pr_bindings import upsert_pr_binding
    from tests.services.test_dependencies import register_unit

    system = ActorContext("system", ActorRole.SYSTEM)
    with Session(migrated_engine) as session:
        unit = register_unit(session, "pr-route")
        upsert_pr_binding(
            session, actor=system, work_unit_id=unit.id, pr_number=42, head_sha="a" * 40, attempt=1
        )
        session.commit()
        unit_id, state_before, version_before = unit.id, unit.state, unit.version

    response = db_client.post(
        "/api/v1/observations",
        headers=SYSTEM,
        json={
            "idempotency_key": "pr-route-1",
            "expected_version": 0,
            "source_system": "github",
            "source_reference": "pr:42@" + "a" * 40,
            "source_url": None,
            "trust_classification": "delivery_system",
            "subject_type": "work_unit",
            "subject_reference": str(unit_id),
            "environment": None,
            "observation_type": "github_pr",
            "status": "observed",
            "severity": "info",
            "observed_at": "2026-07-11T12:00:00+00:00",
            "summary": "pull request 42 is merged",
            "facts": {
                "pr_number": 42,
                "head_sha": "a" * 40,
                "state": "closed",
                "merged": True,
            },
            "payload_digest": None,
        },
    )

    assert response.status_code == 201
    with Session(migrated_engine) as session:
        rows = list(session.scalars(select(ReconciliationCondition)))
        assert [row.condition_type for row in rows] == ["external_merge_alarm"]
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None
        assert (unit.state, unit.version) == (state_before, version_before)
