"""The requeue route, over HTTP.

`routes.py::requeue` passes `**body.model_dump()` straight into `requeue_unit`, so the schema's
field NAMES are the service's keyword arguments. Every other requeue test calls the service
directly and cannot see that seam: rename `RequeueCommand.reason` and the service suite stays
green while every real request 500s on an unexpected keyword. This test goes through the wire,
and it re-reads the recorded event through a DIFFERENT session, so the reason is shown to have
crossed the seam and been committed rather than merely echoed.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Event
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.services.test_dependencies import register_unit
from tests.services.test_reclaim import authorize_readiness


def _failed_unit(engine: Engine, key: str) -> tuple[uuid.UUID, int]:
    with Session(engine) as session:
        unit = register_unit(session, key)
        authorize_readiness(session, unit)
        unit.state = WorkUnitState.FAILED
        unit.attempt_count = 1
        session.commit()
        return unit.id, unit.version


def test_requeue_over_http_moves_a_failed_unit_to_ready_and_records_the_reason(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, version = _failed_unit(migrated_engine, "requeue-http")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/requeue",
        headers=SYSTEM,
        json={
            "idempotency_key": "requeue-http-1",
            "expected_version": version,
            "reason": "runner host died",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(unit_id)
    assert response.json()["state"] == "ready"

    with Session(migrated_engine) as reader:
        event = reader.scalar(select(Event).where(Event.idempotency_key == "requeue-http-1"))
        assert event is not None
        assert (event.from_state, event.to_state) == (WorkUnitState.FAILED, WorkUnitState.READY)
        assert event.payload["reason"] == "runner host died"


def test_requeue_over_http_carries_expected_version_to_the_service(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """A stale version must be refused. If the schema field stopped reaching the service's
    `expected_version`, the service would see `None`, skip the check, and requeue anyway."""
    unit_id, version = _failed_unit(migrated_engine, "requeue-http-stale")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/requeue",
        headers=SYSTEM,
        json={
            "idempotency_key": "requeue-http-stale",
            "expected_version": version + 5,
            "reason": "stale",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "version_conflict"


def test_requeue_over_http_is_system_only(db_client: TestClient, migrated_engine: Engine) -> None:
    unit_id, version = _failed_unit(migrated_engine, "requeue-http-worker")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/requeue",
        headers=WORKER,
        json={"idempotency_key": "requeue-http-w", "expected_version": version, "reason": "no"},
    )

    assert response.status_code == 403, response.text
