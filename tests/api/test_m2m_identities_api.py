"""The executor reads the machine identities the process loaded (ADR-0055 amendment 9)."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.api.dependencies import AuthConfig
from orchestrator.config import get_settings
from orchestrator.identity.auth import M2MCredential, PreviousHash
from orchestrator.main import _m2m_credentials, create_app
from rotation_worker.m2m_setting import Change, build
from tests.api.test_lifecycle_api import SYSTEM, WORKER

ROUTE = "/api/v1/m2m-identities"


def _configure(client: TestClient, executor: str | None) -> None:
    app = cast(FastAPI, client.app)
    configured = get_settings().model_copy(update={"rotation_executor_agent_id": executor})
    app.dependency_overrides[get_settings] = lambda: configured


@pytest.fixture
def overlapping(auth_config: AuthConfig) -> Iterator[tuple[TestClient, datetime]]:
    """The worker's entry carries a previous hash, as during a rotation's overlap."""
    until = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0)
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
        key: {"agent_id": agent, "token_hash": hashlib.sha256(seed).hexdigest()}
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


def test_the_answer_is_a_setting_the_orchestrator_boots_with(
    overlapping: tuple[TestClient, datetime], auth_config: AuthConfig
) -> None:
    """The executor writes this answer back as the setting, so the boot parser must accept it."""
    client, _ = overlapping
    _configure(client, "worker")
    served = client.get(ROUTE, headers=WORKER).json()

    loaded = _m2m_credentials(auth_config.registry, json.dumps(served))

    assert loaded == cast(FastAPI, client.app).state.auth_config.m2m_credentials


def test_a_rotation_built_from_the_answer_boots_through_the_orchestrator_s_parser(
    client: TestClient, auth_config: AuthConfig
) -> None:
    """The executor's rewrite of the served setting is one the orchestrator boots with: the
    worker entry gets the new hash with the old as previous, and every other entry is unchanged."""
    _configure(client, "worker")
    served = client.get(ROUTE, headers=WORKER).json()
    old = hashlib.sha256(b"fixture-token").hexdigest()
    new = hashlib.sha256(b"rotated-token").hexdigest()

    hosted = build(served, "worker-key", Change.HOST, new, old, datetime.now(UTC)).text
    assert hosted is not None
    loaded = _m2m_credentials(auth_config.registry, hosted)
    assert loaded["worker-key"].token_hash == new
    previous = loaded["worker-key"].previous
    assert previous is not None and previous.token_hash == old
    assert {k: v for k, v in loaded.items() if k != "worker-key"} == {
        k: v for k, v in auth_config.m2m_credentials.items() if k != "worker-key"
    }

    retired = build(json.loads(hosted), "worker-key", Change.RETIRE, new, old, datetime.now(UTC))
    assert retired.text is not None
    assert _m2m_credentials(auth_config.registry, retired.text)["worker-key"].previous is None


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


def test_a_host_without_an_overlap_boots_through_the_orchestrator_s_parser(
    client: TestClient, auth_config: AuthConfig
) -> None:
    """Where another entry holds the old hash as current (the observer and drift-reporter case),
    the executor's no-overlap rewrite is one the orchestrator boots with, and the sharing entry
    keeps the old hash."""
    _configure(client, "worker")
    served = client.get(ROUTE, headers=WORKER).json()
    old = served["observer-key"]["token_hash"]
    served["verifier-key"]["token_hash"] = old
    _m2m_credentials(auth_config.registry, json.dumps(served))
    new = hashlib.sha256(b"rotated-observer-token").hexdigest()

    replaced = build(served, "observer-key", Change.REPLACE, new, old, datetime.now(UTC)).text
    assert replaced is not None
    loaded = _m2m_credentials(auth_config.registry, replaced)
    assert loaded["observer-key"].token_hash == new
    assert loaded["observer-key"].previous is None
    assert loaded["verifier-key"].token_hash == old
