"""Public-surface drill (WS-P2.4 Increment 2, exit-#12): over-budget unit halts, records the
breach, and the SLO metric counts it -- driven end-to-end through the real HTTP surface (the
WS-P2.1 reachability lesson: a service with only unit-test callers is dead).

Reuses the exact harness as `tests/api/test_cost_actuals_drill.py`: a DB-backed `db_client`,
`ready_claimed_unit` to get a unit through revision -> unit -> authority-approval -> ready ->
claim (the standard fixture authority declares `max_llm_calls: 4`), WORKER headers for the
claim-holding worker's mutating POSTs, and SYSTEM headers for the SYSTEM-only lifecycle edge
and the read-only GETs (a bare GET on `/api/v1/slo-report` is 401 without a bearer).

Placed under `tests/api/`, not `tests/drills/`: `db_client` is provided by
`tests/api/conftest.py` and is not visible outside that subtree, so a `tests/drills/`
location would need to duplicate the DB-backed TestClient fixture rather than reuse it.

Full choreography chosen over the minimal fallback: `POST /cost-actuals` requires an
`attempt` + `lease_token` proving a live claim, so cost cannot be reported against a unit that
was merely seeded READY -- it must first be claimed. Driving the actual enforcement point
(`claim_unit`'s over-budget check runs at claim time, not at cost-report time) therefore needs
a *second* claim attempt: fail the first attempt (worker), return the unit to READY (system,
a plain `FAILED -> READY` edge with no approval guard), then claim again. That second claim is
where the ceiling trips.
"""

from fastapi.testclient import TestClient

from tests.api.test_infra_links_api import ready_claimed_unit
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_over_budget_unit_halts_and_slo_counts_the_breach(db_client: TestClient) -> None:
    key = "budget-enforcement-drill"
    unit_id, lease = ready_claimed_unit(db_client, key=key)

    # Step 1: as the claim-holding worker, report cost-actuals at the ceiling (the standard
    # fixture authority declares `max_llm_calls: 4`) for the current attempt.
    cost_body = golden_cost_actuals() | {
        "idempotency_key": f"factory-runner:{unit_id}:cost:a{lease['attempt']}",
        "attempt": lease["attempt"],
        "lease_token": lease["lease_token"],
        "llm_calls": 4,
    }
    cost = db_client.post(
        f"/api/v1/work-units/{unit_id}/cost-actuals",
        headers=WORKER,
        json=cost_body,
    )
    assert cost.status_code == 200
    assert cost.json()["cost_known"] is True

    # Step 2: drive the unit back toward READY for a second attempt -- fail the current
    # attempt (worker), then the SYSTEM `FAILED -> READY` edge (no approval guard, unlike
    # `AWAITING_APPROVAL -> READY`).
    fail = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/fail",
        headers=WORKER,
        json={
            "idempotency_key": f"{key}-fail",
            "expected_version": 3,
            "attempt": lease["attempt"],
            "lease_token": lease["lease_token"],
            "reason": "coding_action_failed",
        },
    )
    assert fail.status_code == 200
    assert fail.json()["state"] == "failed"

    ready_again = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={
            "idempotency_key": f"{key}-ready-2",
            "expected_version": fail.json()["version"],
        },
    )
    assert ready_again.status_code == 200
    assert ready_again.json()["state"] == "ready"

    # Step 3: a fresh claim attempt now trips the cumulative ceiling -- the claim is refused
    # with a clean 4xx (`budget_exceeded`), never a 500, and the unit is halted to `failed`.
    claim_again = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={
            "idempotency_key": f"{key}-claim-2",
            "expected_version": ready_again.json()["version"],
        },
    )
    assert claim_again.status_code == 409
    assert claim_again.json()["error"]["code"] == "budget_exceeded"

    history = db_client.get(f"/api/v1/work-units/{unit_id}/history", headers=SYSTEM)
    assert history.status_code == 200
    halt_event = next(
        event
        for event in history.json()
        if event["idempotency_key"] == f"{key}-claim-2:budget-halt"
    )
    assert halt_event["to_state"] == "failed"
    assert halt_event["payload"]["reason"] == "budget_exceeded"

    # Step 4: the SLO report counts the breach.
    report = db_client.get("/api/v1/slo-report", headers=SYSTEM)
    assert report.status_code == 200
    data = report.json()
    assert data["budget_breach"]["status"] == "computed"
    assert data["budget_breach"]["value"] >= 1
