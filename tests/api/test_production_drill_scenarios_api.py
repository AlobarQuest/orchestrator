import uuid

import pytest
from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_production_drills_api import create_revision, start_body

SCENARIOS = (
    "crash_recovery",
    "evidence_recovery",
    "external_pr_conflict",
    "deploy_split_brain",
    "stalled_approval",
)


def _run(client: TestClient, *, key: str) -> str:
    revision_id = create_revision(client, key=key)
    response = client.post("/api/v1/production-drills", headers=HUMAN, json=start_body(revision_id))
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_routes_are_system_only_and_reject_arbitrary_payloads(
    db_client: TestClient, scenario: str
) -> None:
    run_id = _run(db_client, key=f"scenario-{scenario}")
    body = {"idempotency_key": f"{scenario}-1", "expected_version": 0}

    worker = db_client.post(
        f"/api/v1/production-drills/{run_id}/scenarios/{scenario}", headers=WORKER, json=body
    )
    arbitrary = db_client.post(
        f"/api/v1/production-drills/{run_id}/scenarios/{scenario}",
        headers=SYSTEM,
        json={**body, "work_unit_id": str(uuid.uuid4()), "shell_command": "false"},
    )
    accepted = db_client.post(
        f"/api/v1/production-drills/{run_id}/scenarios/{scenario}", headers=SYSTEM, json=body
    )

    assert worker.status_code == 403
    assert worker.json()["error"]["code"] == "role_forbidden"
    assert arbitrary.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["run_id"] == run_id
    assert accepted.json()["status"] == "asserting"


def test_scenarios_reject_unknown_runs_and_the_fail_route_is_terminal_and_audited(
    db_client: TestClient,
) -> None:
    unknown = db_client.post(
        f"/api/v1/production-drills/{uuid.uuid4()}/scenarios/crash_recovery",
        headers=SYSTEM,
        json={"idempotency_key": "unknown", "expected_version": 0},
    )
    run_id = _run(db_client, key="scenario-fail")
    failed = db_client.post(
        f"/api/v1/production-drills/{run_id}/fail",
        headers=SYSTEM,
        json={
            "idempotency_key": "fail-1",
            "expected_version": 0,
            "failure_code": "runner_preflight_failed",
            "diagnostic_ref": "drill://redacted/preflight",
        },
    )
    state = db_client.get(f"/api/v1/production-drills/{run_id}/state", headers=HUMAN)

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "production_drill_run_not_found"
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert state.json()["status"] == "failed"


def test_fail_rejects_workers_and_unrecognized_or_unredacted_inputs(db_client: TestClient) -> None:
    run_id = _run(db_client, key="scenario-fail-inputs")
    body = {
        "idempotency_key": "fail-inputs",
        "expected_version": 0,
        "failure_code": "runner_preflight_failed",
        "diagnostic_ref": "drill://redacted/preflight",
    }

    worker = db_client.post(f"/api/v1/production-drills/{run_id}/fail", headers=WORKER, json=body)
    invalid = db_client.post(
        f"/api/v1/production-drills/{run_id}/fail",
        headers=SYSTEM,
        json={**body, "failure_code": "anything", "resource_id": str(uuid.uuid4())},
    )

    assert worker.status_code == 403
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    "failure_code",
    (
        "runner_preflight_failed",
        "crash_recovery_failed",
        "evidence_recovery_failed",
        "external_pr_conflict_failed",
        "deploy_split_brain_failed",
        "stalled_approval_failed",
    ),
)
def test_every_failure_code_requires_a_redacted_diagnostic(
    db_client: TestClient, failure_code: str
) -> None:
    run_id = _run(db_client, key=f"failure-{failure_code}")
    response = db_client.post(
        f"/api/v1/production-drills/{run_id}/fail",
        headers=SYSTEM,
        json={
            "idempotency_key": f"{failure_code}-1",
            "expected_version": 0,
            "failure_code": failure_code,
            "diagnostic_ref": "https://unsafe.example/secret",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "failure_code",
    (
        "runner_preflight_failed",
        "crash_recovery_failed",
        "evidence_recovery_failed",
        "external_pr_conflict_failed",
        "deploy_split_brain_failed",
        "stalled_approval_failed",
    ),
)
def test_every_failure_code_is_accepted_with_a_redacted_diagnostic(
    db_client: TestClient, failure_code: str
) -> None:
    run_id = _run(db_client, key=f"valid-failure-{failure_code}")
    response = db_client.post(
        f"/api/v1/production-drills/{run_id}/fail",
        headers=SYSTEM,
        json={
            "idempotency_key": f"valid-{failure_code}",
            "expected_version": 0,
            "failure_code": failure_code,
            "diagnostic_ref": f"drill://redacted/{failure_code}",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
