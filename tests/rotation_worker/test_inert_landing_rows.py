"""The repository's inert-landing registry rows, read by the code that acts on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 5: both rows are handed to the SDS, and the
token's is due for its first issue; the ping URL's is dated by a later change, once the token's
`store` unit completes. The rows are read here as the executor reads them, at `main` through
`RepoMirror`, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from rotation_proposer.generate import plan_for
from rotation_worker.adapters import bws_lane_token, healthchecks_ping_url
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    credentials,
    provider_entries,
    rotated_by_sds,
)
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
LANE = "inert-landing"
TOKEN = "inert-landing-bws-token"
PING_URL = "inert-landing-ping-url"
SERVICE = "w1ujl75mr0sqdrtur294ldrk"
VARIABLE = "INERT_LANDING_BWS_ACCESS_TOKEN"
CHECK = "sds-inert-landing"
LAUNCHER = "scripts/run-inert-landing.sh"
REACH = frozenset(
    {
        "221a48d5-3f29-4898-b300-b4820140c880",  # ORCHESTRATOR_M2M_CREDENTIALS (SYSTEM bearer)
        "314f276d-55ca-4ddc-a24d-b4a3013508cd",  # change-manager/M2M_TOKEN_READ
        "b415d03b-f92d-46b1-8a9d-b4f10103df49",  # ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE
        "314a966c-5654-4e3c-8358-b4fd01268775",  # lanes-read-app-private-key
        "35557473-f80c-44e3-892a-b507013158b1",  # inert-landing-ping-url's keeper
    }
)
BOTH = pytest.mark.parametrize("row_id", [TOKEN, PING_URL])


def _commit_registry(origins: Origins) -> None:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    _commit_registry(origins)
    return rotated_by_sds(mirror)


def _lane_secrets() -> set[str]:
    manifest: dict[str, Any] = tomllib.loads((ROOT / ".bws-secrets.toml").read_text())
    return {s["uuid"] for s in manifest["secret"] if LANE in s.get("lanes", [])}


def test_both_rows_are_handed_to_the_sds(rows: dict[str, RegistryEntry]) -> None:
    assert {TOKEN, PING_URL} <= set(rows)


def test_the_token_row_is_one_the_lane_token_adapter_acts_on(
    rows: dict[str, RegistryEntry],
) -> None:
    entry = rows[TOKEN]
    bws_lane_token.check_row(entry.credential)
    variable = bws_lane_token.lane_variable(entry.destinations, entry.keeper)
    assert variable["uuid"] == SERVICE
    # The variable the lane's compose maps to the container's BWS_ACCESS_TOKEN.
    assert variable["key"] == VARIABLE
    # The lane phases redeploy the service, never the executor. Read from the row itself: the
    # destination parser reads a missing `redeploy` as false.
    [env] = [c for c in entry.credential["consumer"] if c["kind"] == "coolify-env"]
    assert env["redeploy"] is False
    compose = (ROOT / "lanes" / LANE / "compose.yaml").read_text()
    assert f"BWS_ACCESS_TOKEN: ${{{VARIABLE}}}" in compose


def test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper(
    rows: dict[str, RegistryEntry],
) -> None:
    """The ping URL's keeper joins the manifest's list for the lane in a later change (deploy.md,
    the inert lander's cut-over item 6), so for now it is the one secret the row adds."""
    ping_keeper = rows[PING_URL].credential["bws_uuid"]
    projects, secrets = bws_lane_token.declared_reach(rows[TOKEN].credential)
    assert projects == frozenset()
    assert ping_keeper not in _lane_secrets()
    assert secrets == _lane_secrets() | {ping_keeper}
    assert secrets == REACH


def test_no_other_lane_token_row_declares_the_same_reach(
    rows: dict[str, RegistryEntry], mirror: RepoMirror
) -> None:
    """The rule compares whole reach sets: this row passes beside every other lane's, and a row
    given another lane's whole reach is caught."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    mine = bws_lane_token.declared_reach(rows[TOKEN].credential)[1]
    others = [entry.credential["id"] for entry in lane_rows if entry.credential["id"] != TOKEN]
    assert others
    assert bws_lane_token.sharing_reach(lane_rows, rows[TOKEN].credential) == []
    for other in others:
        theirs = bws_lane_token.declared_reach(rows[other].credential)[1]
        assert theirs != mine
        twin = {**rows[TOKEN].credential, "reach_secrets": sorted(theirs)}
        assert bws_lane_token.sharing_reach(lane_rows, twin) == [other]


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    healthchecks_ping_url.check_row(rows[PING_URL].credential)
    # The check the lane's launcher pings, whose URL the executor reads (ADR-0055 amendment 23).
    assert rows[PING_URL].credential["healthchecks_check"] == CHECK
    assert f"sds_deadman_arm {CHECK} " in (ROOT / LAUNCHER).read_text()


def test_only_the_token_row_is_due(rows: dict[str, RegistryEntry]) -> None:
    """The ping URL's row is dated only after the token's `store` unit completes, so the two
    issue units never wait on a mint at once."""
    assert rows[TOKEN].credential["rotate_requested"] == "2026-11-19"
    assert "rotate_requested" not in rows[PING_URL].credential


@BOTH
def test_each_row_s_keeper_consumer_is_its_bws_uuid(
    rows: dict[str, RegistryEntry], row_id: str
) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    credential = rows[row_id].credential
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == credential["bws_uuid"]


@BOTH
def test_neither_row_has_an_age_anchor(rows: dict[str, RegistryEntry], row_id: str) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue."""
    credential = rows[row_id].credential
    assert "created" not in credential
    assert "last_rotated" not in credential


@BOTH
def test_a_first_issue_derives_the_issue_plan(rows: dict[str, RegistryEntry], row_id: str) -> None:
    _, plan = plan_for(rows[row_id], first_issue=True)
    assert plan.issue


def test_the_read_app_key_row_lists_the_lane_s_container_reader(
    mirror: RepoMirror, origins: Origins
) -> None:
    """The container reads the read App's key every pass, bare or `--submit`, and pings its check
    every pass. The row is hand-rotated, so it is read through `credentials`, not
    `rotated_by_sds`."""
    _commit_registry(origins)
    key = credentials(mirror)["lanes-read-app-private-key"].credential
    [reader] = [
        c
        for c in key["consumer"]
        if c["kind"] == "runtime-fetch" and c["repo"] == "orchestrator" and c["ref"] == LAUNCHER
    ]
    assert reader["healthchecks_check"] == CHECK
    assert key["bws_uuid"] in (ROOT / LAUNCHER).read_text()
