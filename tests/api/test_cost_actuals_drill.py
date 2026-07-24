"""Public-surface drill (WS-P2.4 Increment 1): POST cost-actuals -> SLO report computes.

Drives the real HTTP surface end-to-end (the WS-P2.1 reachability lesson: a service
with only unit-test callers is dead). Reuses the exact route-test harness as
`tests/api/test_cost_actuals_route.py`: a DB-backed `db_client`, `ready_claimed_unit`
to get a unit through revision -> unit -> authority-approval -> ready -> claim, WORKER
headers for the mutating POST, and SYSTEM headers for the read-only GET (a bare GET on
`/api/v1/slo-report` is 401 without a bearer).

Placed under `tests/api/`, not `tests/drills/`: `db_client` is provided by
`tests/api/conftest.py` and is not visible outside that subtree, so a `tests/drills/`
location would need to duplicate the DB-backed TestClient fixture rather than reuse it.
"""

from fastapi.testclient import TestClient

from tests.api.test_infra_links_api import ready_claimed_unit
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_posted_cost_actuals_flow_into_the_slo_report(db_client: TestClient) -> None:
    unit_id, lease = ready_claimed_unit(db_client, key="cost-actuals-drill")

    body = golden_cost_actuals() | {
        "idempotency_key": f"factory-runner:{unit_id}:cost:a{lease['attempt']}",
        "attempt": lease["attempt"],
        "lease_token": lease["lease_token"],
        "cost_usd": 3.0,
        "input_tokens": 1000,
        "output_tokens": 500,
    }
    post = db_client.post(
        f"/api/v1/work-units/{unit_id}/cost-actuals",
        headers=WORKER,
        json=body,
    )
    assert post.status_code == 200
    assert post.json()["cost_known"] is True

    report = db_client.get("/api/v1/slo-report", headers=SYSTEM)
    assert report.status_code == 200
    data = report.json()
    assert data["cost_per_unit"]["status"] in ("computed", "partial")
    assert data["cost_per_unit"]["value"] == 3.0
    assert data["token_consumption"]["value"] == 1500.0
