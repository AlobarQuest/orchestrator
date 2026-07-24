"""POST /work-units/{unit_id}/cost-actuals (WS-P2.4 Increment 1).

Wires CostActualsCommand/CostActualsResponse (Task 2) to record_cost_actuals (Task 3).
Uses the same claim-gated route-test harness as infra-lane-links: a DB-backed
`db_client`, worker M2M headers, and `ready_claimed_unit` to get a unit through
revision -> unit -> authority-approval -> ready -> claim before exercising the route.
"""

import uuid

from fastapi.testclient import TestClient

from tests.api.test_infra_links_api import ready_claimed_unit
from tests.api.test_lifecycle_api import WORKER
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_post_cost_actuals_persists_event(db_client: TestClient) -> None:
    unit_id, lease = ready_claimed_unit(db_client, key="cost-actuals-api")

    body = golden_cost_actuals() | {
        "idempotency_key": f"factory-runner:{unit_id}:cost:a{lease['attempt']}",
        "attempt": lease["attempt"],
        "lease_token": lease["lease_token"],
    }
    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/cost-actuals",
        headers=WORKER,
        json=body,
    )

    assert resp.status_code == 200
    assert resp.json()["cost_known"] is True


def test_post_cost_actuals_bad_body_is_422_not_500(db_client: TestClient) -> None:
    unit_id, lease = ready_claimed_unit(db_client, key="cost-actuals-api-bad-body")

    resp = db_client.post(
        f"/api/v1/work-units/{unit_id}/cost-actuals",
        headers=WORKER,
        json={
            "idempotency_key": "cost-actuals-api-bad-body-1",
            "attempt": lease["attempt"],
            "lease_token": lease["lease_token"],
            "cost_known": True,
        },
    )

    # cost_known true but numerics missing -> Pydantic model_validator rejects, never 500.
    assert resp.status_code == 422


def test_post_cost_actuals_unknown_unit_is_clean_4xx(db_client: TestClient) -> None:
    body = golden_cost_actuals() | {
        "idempotency_key": "cost-actuals-api-unknown-unit",
    }

    resp = db_client.post(
        f"/api/v1/work-units/{uuid.uuid4()}/cost-actuals",
        headers=WORKER,
        json=body,
    )

    # DomainError (work_unit_not_found) -> handled, never a bare 500.
    assert resp.status_code in (400, 404, 409)
    assert resp.status_code != 500
