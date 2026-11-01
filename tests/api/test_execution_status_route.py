"""The execution-status route: operator and observer read, nothing else does."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER
from tests.api.test_observer_role_confinement import OBSERVER
from tests.services.test_dispatch import ready_unit

PATH = "/api/v1/execution-status"


def test_observer_may_read_and_a_worker_may_not(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        unit = ready_unit(session, key="es-route")
        unit_id = str(unit.id)

    for headers in (OBSERVER, SYSTEM):
        response = db_client.get(PATH, headers=headers)
        assert response.status_code == 200
        [row] = response.json()
        assert row["unit_id"] == unit_id
        assert set(row) == {
            "unit_id",
            "state",
            "change_class",
            "is_rotation",
            "standing",
            "target_repository",
            "granted",
            "latest_dispatch_status",
            "latest_dispatch_at",
            "has_claim",
            "lease_lapsed",
            "breaker_open",
        }
    assert db_client.get(PATH, headers=WORKER).status_code == 403
    assert db_client.get(PATH, headers=VERIFIER).status_code == 403
