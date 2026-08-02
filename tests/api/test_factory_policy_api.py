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
    assert body["version"] == 4
    assert body["source"] == "factory-policy.toml"
    assert [row["member"] for row in body["reach"]] == sorted(REACH_VOCABULARY)
    assert all(row["rationale"] and row["decided"] for row in body["reach"])


def test_the_policy_surface_serves_the_change_window_a_row_declares(
    db_client: TestClient,
) -> None:
    # Same reason as the patterns below: a `response_model` drops what it does not declare, so the
    # only way to know an operator can read the window is to read it off the wire. Served in the
    # local terms it was written in, zone included -- an offset would be true for half the year.
    rows = {
        row["member"]: row
        for row in db_client.get("/api/v1/factory-policy", headers=SYSTEM).json()["reach"]
    }

    assert rows["source_repository"]["change_window"] is None
    window = rows["operator_machine"]["change_window"]
    assert (window["timezone"], window["start"], window["end"]) == (
        "America/New_York",
        "02:00",
        "06:00",
    )
    assert window["rationale"] and window["decided"]


def test_the_policy_surface_serves_the_known_good_patterns_a_row_declares(
    db_client: TestClient,
) -> None:
    # A `response_model` silently DROPS every key the service returns but the schema does not
    # declare, so "the loader reads it" is never evidence "an operator can see it". This is the
    # surface that answers whether the running image would recognise a given envelope.
    body = db_client.get("/api/v1/factory-policy", headers=SYSTEM).json()
    rows = {row["member"]: row for row in body["reach"]}

    assert rows["live_estate"]["known_good"] == []
    pattern = rows["source_repository"]["known_good"][0]
    assert pattern["command_prefixes"] == ["uv add", "uv lock"]
    assert pattern["change_class"] == "dependency-update"
    assert pattern["rationale"] and pattern["decided"] and pattern["capabilities"]


def test_the_policy_surface_carries_no_permission_of_any_kind(db_client: TestClient) -> None:
    # The artifact answers only in refusals. A field on this response that read as "allowed" would
    # be the one shape that lets policy appear to outrank the hard off-switch. Walked to the
    # LEAVES: schema 2 nests patterns inside rows, and a scan of the top level only would have
    # stopped seeing the values it exists to check the moment that nesting appeared.
    body = db_client.get("/api/v1/factory-policy", headers=SYSTEM).json()

    assert _leaves(body)  # the control: a walk that finds nothing proves nothing
    assert not any(isinstance(value, bool) for value in _leaves(body))


def _leaves(value: object) -> list[object]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]
