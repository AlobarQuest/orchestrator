"""The execution-status route: operator and observer read, nothing else does."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from execution_watch.decide import SUBJECT_KINDS
from orchestrator.services.reporting import execution_status as status
from tests.api.test_decomposition_api import create_proposal, register_intake
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
            "subject_kind",
            "subject_id",
            "entered_at",
            "waiting",
            "overdue",
            "policy_not_applied",
            "proposed_by",
            "rotation_package",
        }
    assert db_client.get(PATH, headers=WORKER).status_code == 403
    assert db_client.get(PATH, headers=VERIFIER).status_code == 403


def test_a_row_that_is_not_a_unit_survives_the_response_model(db_client: TestClient) -> None:
    """A `response_model` drops undeclared keys and refuses a null it doesn't allow."""
    proposal = create_proposal(db_client, register_intake(db_client))

    response = db_client.get(PATH, headers=OBSERVER)

    assert response.status_code == 200
    [row] = [item for item in response.json() if item["subject_kind"] == "proposal"]
    assert row["subject_id"] == proposal["id"]
    assert row["unit_id"] is None
    assert row["state"] == "proposed"
    assert row["waiting"] is True
    assert row["entered_at"] is not None
    assert row["has_claim"] is None


def test_the_lane_knows_every_kind_the_server_serves() -> None:
    """A kind the lane doesn't know is skipped, so a new one would alert on nothing."""
    assert SUBJECT_KINDS == {
        status.SUBJECT_UNIT,
        status.SUBJECT_STAGED_INTAKE,
        status.SUBJECT_PACKAGE,
        status.SUBJECT_PROPOSAL,
    }
