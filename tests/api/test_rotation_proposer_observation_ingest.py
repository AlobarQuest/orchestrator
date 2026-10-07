"""The rotation proposer's composed row, put through the REAL ingestion route (ADR-0054).

Every other test of this payload mocks the client that would refuse it. Here the route, the request
model, the service's secret detector and the database's CHECK constraints each have their say, and
the replay property -- the one that lets a scheduled pass run twice -- is proven on the database.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Observation
from rotation_proposer.findings import Due
from rotation_proposer.observation import rotation_observation
from tests.api.test_observer_role_confinement import OBSERVER

DUE = Due("openrouter-generic", "openrouter-key", "requested", "2026-10-07", "2026-10-07")


def _stored(engine: Engine) -> int | None:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(Observation)
            .where(Observation.source_system == "rotation_proposer")
        )


def test_the_composed_row_is_ACCEPTED_by_the_real_route(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=rotation_observation(DUE)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["source_system"], body["observation_type"], body["subject_type"]) == (
        "rotation_proposer",
        "rotation_due",
        "credential",
    )
    assert isinstance(body["id"], str) and body["id"]


def test_a_SECOND_PASS_names_the_SAME_row(db_client: TestClient, migrated_engine: Engine) -> None:
    first = db_client.post("/api/v1/observations", headers=OBSERVER, json=rotation_observation(DUE))
    second = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=rotation_observation(DUE)
    )

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert _stored(migrated_engine) == 1


def test_the_NEXT_rotation_takes_its_own_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    db_client.post("/api/v1/observations", headers=OBSERVER, json=rotation_observation(DUE))
    later = replace(DUE, trigger="age", basis="2026-10-08", dated="2026-10-08")
    response = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=rotation_observation(later)
    )

    assert response.status_code == 201, response.text
    assert _stored(migrated_engine) == 2
