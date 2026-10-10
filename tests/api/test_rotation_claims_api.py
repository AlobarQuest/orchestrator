"""Rotation-unit claims over HTTP: the routes carry the configured executor to the service.

A service test passes `rotation_executor` itself and cannot see a route that forgets to. These go
through the wire, with the setting unset and then set to the worker credential's agent id.
"""

import uuid
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from orchestrator.config import get_settings
from orchestrator.services.lifecycle.rotation_claims import NON_HOSTED_CONSTRAINT
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.services.test_rotation_claims import seed_unit


@pytest.fixture
def executor_is_worker(db_client: TestClient) -> Iterator[None]:
    """Configure the worker credential's agent id as the rotation executor."""
    app = cast(FastAPI, db_client.app)
    configured = get_settings().model_copy(update={"rotation_executor_agent_id": "worker"})
    app.dependency_overrides[get_settings] = lambda: configured
    yield
    app.dependency_overrides.pop(get_settings, None)


def _rotation_unit(engine: Engine, key: str) -> tuple[uuid.UUID, int]:
    with Session(engine) as session:
        unit = seed_unit(session, key, constraints={NON_HOSTED_CONSTRAINT: True})
        return unit.id, unit.version


def _claim(db_client: TestClient, unit_id: uuid.UUID, version: int, key: str):
    return db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": key, "expected_version": version},
    )


def test_with_the_setting_unset_the_claim_route_refuses_a_rotation_unit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, version = _rotation_unit(migrated_engine, "api-rotation-unset")

    response = _claim(db_client, unit_id, version, "api-rotation-unset-1")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "rotation_claim_confined"


def test_the_configured_executor_claims_a_rotation_unit_over_http(
    db_client: TestClient, migrated_engine: Engine, executor_is_worker: None
) -> None:
    unit_id, version = _rotation_unit(migrated_engine, "api-rotation-set")

    response = _claim(db_client, unit_id, version, "api-rotation-set-1")

    assert response.status_code == 200, response.text


def test_the_reclaim_route_refuses_to_grant_other_work_to_the_executor(
    db_client: TestClient, migrated_engine: Engine, executor_is_worker: None
) -> None:
    with Session(migrated_engine) as session:
        unit = seed_unit(session, "api-reclaim-executor", capability="repo.edit")
        unit_id, version = unit.id, unit.version

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/reclaim-expired-claim",
        headers=SYSTEM,
        json={
            "idempotency_key": "api-reclaim-executor-1",
            "expected_version": version,
            "next_owner_id": "worker",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "rotation_executor_confined"


def test_the_release_route_returns_a_lapsed_rotation_unit_to_ready(
    db_client: TestClient, migrated_engine: Engine, executor_is_worker: None
) -> None:
    unit_id, version = _rotation_unit(migrated_engine, "api-release")
    claimed = _claim(db_client, unit_id, version, "api-release-claim")
    assert claimed.status_code == 200, claimed.text
    with Session(migrated_engine) as session:
        session.execute(
            text(
                "UPDATE claims SET lease_expires_at = transaction_timestamp() - interval '1 second'"
                " WHERE id = :claim_id"
            ),
            {"claim_id": claimed.json()["claim_id"]},
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/release-expired-claim",
        headers=SYSTEM,
        json={"idempotency_key": "api-release-1", "expected_version": version + 1},
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "ready"
