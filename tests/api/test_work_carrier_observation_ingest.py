"""The work carrier's standing-approval record, put through the REAL route (ADR-0055 amendment 22).

The carrier's own tests use a double for the client that would refuse this body. Here the route,
the request model, the secret detector (a 64-hex content hash and fact keys among them) and the
database's CHECK constraints each have their say, and a second pass over the same revision and
registry commit replays the row rather than conflicting.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.config import Settings, get_settings
from orchestrator.persistence.models import Observation
from tests.api.test_observer_role_confinement import OBSERVER
from tests.services import test_standing_approval_gate as gate_tests
from tests.work_carrier.test_standing import _check


def _composed(tmp_path: Path) -> dict:
    checked = _check(tmp_path)
    assert not isinstance(checked, str), checked
    return checked.observation


def _stored(engine: Engine) -> int | None:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(Observation.source_system == "work_carrier")
        )


def test_the_composed_record_is_accepted_by_the_real_route(
    db_client: TestClient, tmp_path: Path
) -> None:
    response = db_client.post("/api/v1/observations", headers=OBSERVER, json=_composed(tmp_path))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["source_system"], body["observation_type"], body["subject_type"]) == (
        "work_carrier",
        "standing_approval_check",
        "package_revision",
    )


def test_a_second_pass_names_the_same_row(
    db_client: TestClient, migrated_engine: Engine, tmp_path: Path
) -> None:
    first = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=_composed(tmp_path / "one")
    )
    second = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=_composed(tmp_path / "two")
    )

    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert _stored(migrated_engine) == 1


# ---- the route hands the gate its setting -----------------------------------------------------

SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}


def _propose_through_the_route(
    db_client: TestClient, migrated_engine: Engine, carrier: str | None
) -> str:
    """A ping URL's revision 1 the policy approved, the record the `observer` agent made, and a
    SYSTEM proposal through the route with the carrier setting as given; the proposal's state."""
    with Session(migrated_engine) as session:
        revision = gate_tests._revision(session)
        gate_tests._record(session, actor="observer")
        session.commit()
        revision_id = revision.id
        ac_ids = gate_tests.package_ac_ids(session, revision_id)
    settings = Settings.model_validate({"work_carrier_agent_id": carrier})
    cast(FastAPI, db_client.app).dependency_overrides[get_settings] = lambda: settings
    response = db_client.post(
        f"/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        headers=SYSTEM,
        json={
            "idempotency_key": f"rotation-decomposer:{revision_id}",
            "expected_version": 0,
            "rationale": "The ping URL's issue plan.",
            "proposed_units": [
                {
                    "unit_key": "landing-ledger-ping-url-store",
                    "title": "landing-ledger-ping-url: store",
                    "outcome": "the store unit's steps complete",
                    "required_capability": "operational_action",
                    "authority": gate_tests._envelope(),
                    "max_attempts": 3,
                }
            ],
            "ac_mappings": [
                {"ac_id": str(ac_ids["AC-001"]), "unit_key": "landing-ledger-ping-url-store"}
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["state"]


def test_the_route_passes_the_configured_carrier_to_the_gate(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    assert _propose_through_the_route(db_client, migrated_engine, "observer") == "approved"


def test_the_route_with_no_carrier_configured_leaves_it_for_a_person(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    assert _propose_through_the_route(db_client, migrated_engine, None) == "proposed"
