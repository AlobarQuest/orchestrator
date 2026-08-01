"""WS-P2.18 Increment 2: the read surface that says what policy a running process is enforcing.

Merged is not deployed. A policy correct on `main` says nothing about the image serving traffic,
and until this route existed there was no way to ask the running instance which artifact it holds.
"""

from fastapi.testclient import TestClient

from orchestrator.reach_vocabulary import REACH_VOCABULARY
from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_the_policy_surface_is_operator_only(db_client: TestClient) -> None:
    assert db_client.get("/api/v1/factory-policy", headers=WORKER).status_code == 403


def test_the_policy_surface_reports_the_version_and_every_reach_row(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/factory-policy", headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["source"] == "factory-policy.toml"
    assert [row["member"] for row in body["reach"]] == sorted(REACH_VOCABULARY)
    assert all(row["rationale"] and row["decided"] for row in body["reach"])


def test_the_policy_surface_carries_no_permission_of_any_kind(db_client: TestClient) -> None:
    # The artifact answers only in refusals. A field on this response that read as "allowed" would
    # be the one shape that lets policy appear to outrank the hard off-switch.
    body = db_client.get("/api/v1/factory-policy", headers=SYSTEM).json()

    values = [body["version"], body["source"], *[v for row in body["reach"] for v in row.values()]]
    assert not any(isinstance(value, bool) for value in values)
