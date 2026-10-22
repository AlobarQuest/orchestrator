"""The executor reads the fingerprint of each credential the orchestrator holds for another service
(ADR-0055 amendment 13)."""

import hashlib
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from orchestrator.config import get_settings
from tests.api.test_lifecycle_api import SYSTEM, WORKER

ROUTE = "/api/v1/held-credentials"
HELD = "a-throwaway-read-bearer-for-tests-only-0123"


def _configure(client: TestClient, executor: str | None, held: str | None = HELD) -> None:
    app = cast(FastAPI, client.app)
    configured = get_settings().model_copy(
        update={
            "rotation_executor_agent_id": executor,
            "change_record_token": SecretStr(held) if held is not None else None,
        }
    )
    app.dependency_overrides[get_settings] = lambda: configured


def test_the_executor_reads_the_fingerprint_of_the_loaded_copy(client: TestClient) -> None:
    _configure(client, "worker")

    response = client.get(ROUTE, headers=WORKER)

    assert response.status_code == 200, response.text
    digest = hashlib.sha256(HELD.encode()).hexdigest()
    assert response.json() == {
        "ORCHESTRATOR_CHANGE_RECORD_TOKEN": {"sha256_prefix": digest[:16], "length": len(HELD)}
    }
    assert HELD not in response.text


def test_a_setting_that_isnt_set_is_left_out(client: TestClient) -> None:
    _configure(client, "worker", held=None)

    response = client.get(ROUTE, headers=WORKER)

    assert response.status_code == 200, response.text
    assert response.json() == {}


def test_anyone_but_the_configured_executor_is_refused(client: TestClient) -> None:
    for executor, headers in (("worker", SYSTEM), (None, WORKER), ("someone-else", WORKER)):
        _configure(client, executor)

        response = client.get(ROUTE, headers=headers)

        assert response.status_code == 403, (executor, response.text)
        assert response.json()["error"]["code"] == "role_forbidden"


def test_the_executor_s_reader_understands_the_route_s_answer(client: TestClient) -> None:
    """The route and `held.py` each spell the fields; this holds the two together."""
    from rotation_worker.coolify import CoolifyClient
    from rotation_worker.held import HeldSettings
    from rotation_worker.orchestrator_client import OrchestratorClient
    from rotation_worker.values import Value

    _configure(client, "worker")
    answer = client.get(ROUTE, headers=WORKER).json()

    class Orchestrator:
        def held(self) -> dict[str, dict[str, object]]:
            return answer

    def no_coolify(application: str) -> CoolifyClient:
        raise AssertionError("a read writes nothing")

    held = HeldSettings(cast(OrchestratorClient, Orchestrator()), no_coolify, "app")
    consumer = {"key": "ORCHESTRATOR_CHANGE_RECORD_TOKEN"}
    assert held.holds(consumer, Value(HELD))
    assert not held.holds(consumer, Value(HELD + "x"))
