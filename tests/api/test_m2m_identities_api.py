"""The executor reads the machine identities the process loaded (ADR-0055 amendment 9)."""

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.api.dependencies import AuthConfig
from orchestrator.config import get_settings
from orchestrator.identity.auth import M2MCredential, PreviousHash
from orchestrator.main import create_app
from tests.api.test_lifecycle_api import SYSTEM, WORKER

ROUTE = "/api/v1/m2m-identities"


def _configure(client: TestClient, executor: str | None) -> None:
    app = cast(FastAPI, client.app)
    configured = get_settings().model_copy(update={"rotation_executor_agent_id": executor})
    app.dependency_overrides[get_settings] = lambda: configured


@pytest.fixture
def overlapping(auth_config: AuthConfig) -> Iterator[tuple[TestClient, datetime]]:
    """The worker's entry carries a previous hash, as during a rotation's overlap."""
    until = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    worker = M2MCredential(
        agent_id="worker",
        token_hash=hashlib.sha256(b"fixture-token").hexdigest(),
        previous=PreviousHash(hashlib.sha256(b"retired-token").hexdigest(), until),
    )
    config = replace(
        auth_config, m2m_credentials={**auth_config.m2m_credentials, "worker-key": worker}
    )
    with TestClient(create_app(config)) as client:
        yield client, until


def test_the_executor_reads_every_loaded_identity(client: TestClient) -> None:
    _configure(client, "worker")

    response = client.get(ROUTE, headers=WORKER)

    assert response.status_code == 200, response.text
    assert response.json() == {
        key: {"agent_id": agent, "token_hash": hashlib.sha256(seed).hexdigest(), "previous": None}
        for key, agent, seed in (
            ("worker-key", "worker", b"fixture-token"),
            ("system-key", "system", b"system-token"),
            ("verifier-key", "verifier", b"verifier-token"),
            ("observer-key", "observer", b"observer-token"),
        )
    }


def test_a_previous_hash_and_its_expiry_are_served(
    overlapping: tuple[TestClient, datetime],
) -> None:
    client, until = overlapping
    _configure(client, "worker")

    previous = client.get(ROUTE, headers=WORKER).json()["worker-key"]["previous"]

    assert previous["token_hash"] == hashlib.sha256(b"retired-token").hexdigest()
    assert datetime.fromisoformat(previous["until"]) == until


def test_with_no_executor_configured_every_actor_is_refused(client: TestClient) -> None:
    _configure(client, None)

    response = client.get(ROUTE, headers=WORKER)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "role_forbidden"


def test_an_actor_other_than_the_executor_is_refused(client: TestClient) -> None:
    _configure(client, "worker")

    response = client.get(ROUTE, headers=SYSTEM)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "role_forbidden"


def test_an_unauthenticated_request_is_refused(client: TestClient) -> None:
    _configure(client, "worker")

    assert client.get(ROUTE).status_code == 401
