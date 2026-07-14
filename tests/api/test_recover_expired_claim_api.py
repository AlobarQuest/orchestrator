import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.main import app
from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit
from tests.services.test_reclaim import expire


def test_recover_expired_claim_returns_the_ordinary_unit_projection(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    unit_id = uuid.UUID(_register_ready_unit(db_client, "expired-claim-recovery"))
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "expired-claim", "expected_version": 2},
    )
    assert claim.status_code == 200

    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, unit_id)
        assert unit is not None
        start_version = unit.version
        expire(session, uuid.UUID(claim.json()["claim_id"]))

    forbidden = db_client.post(
        f"/api/v1/work-units/{unit_id}/recover-expired-claim",
        headers=WORKER,
        json={
            "idempotency_key": "worker-expired-claim-recovery",
            "expected_version": start_version,
        },
    )
    assert forbidden.status_code == 403

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/recover-expired-claim",
        headers=SYSTEM,
        json={
            "idempotency_key": "system-expired-claim-recovery",
            "expected_version": start_version,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(unit_id),
        "state": "ready",
        "version": start_version + 2,
    }
    assert "lease_token" not in response.json()
    assert "claim_id" not in response.json()


def test_recover_expired_claim_openapi_contract_uses_unit_response() -> None:
    openapi = TestClient(app).get("/openapi.json").json()
    route = openapi["paths"]["/api/v1/work-units/{unit_id}/recover-expired-claim"]["post"]

    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UnitResponse"
    }
    lifecycle = openapi["components"]["schemas"]["LifecycleCommand"]
    assert "reason" in lifecycle["properties"]
    assert "reason" not in lifecycle["required"]
