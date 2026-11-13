"""The repository's change-proposer registry rows, read by the code that acts on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 4: both rows are handed to the SDS and due
for their first issue. The rows are read here as the executor reads them, at `main` through
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
    provider_entries,
    rotated_by_sds,
)
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "change-proposer-bws-token"
PING_URL = "change-proposer-ping-url"
OTHER_LANE_TOKENS = (
    "deploy-watcher-bws-token",
    "execution-watch-bws-token",
    "landing-ledger-bws-token",
    "membership-check-bws-token",
    "pin-watcher-bws-token",
    "revision-watcher-bws-token",
    "unit-verifier-bws-token",
)
LANE = "change-proposer"
LAUNCHER = "scripts/run-change-proposer.sh"
# The four secrets Devon granted the lane's account a direct read on (HQ gate G-49).
GRANTED = frozenset(
    {
        "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",  # orchestrator-observer
        "acccb346-4baa-43ec-a1d4-b4a400c048ee",  # change-manager/M2M_TOKEN_PROPOSE
        "314a966c-5654-4e3c-8358-b4fd01268775",  # lanes-read-app-private-key
        "7651ccc2-7e13-417b-86da-b50100c0d4b2",  # change-proposer-ping-url's keeper
    }
)


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
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
    assert variable["uuid"] == "w35r4jsmylloghoexdc18gfl"
    # The variable `lanes/change-proposer/compose.yaml` maps to the container's BWS_ACCESS_TOKEN.
    assert variable["key"] == "CHANGE_PROPOSER_BWS_ACCESS_TOKEN"
    # The lane phases redeploy the service, never the executor. Read from the row itself: the
    # destination parser reads a missing `redeploy` as false.
    [env] = [c for c in entry.credential["consumer"] if c["kind"] == "coolify-env"]
    assert env["redeploy"] is False
    compose = (ROOT / "lanes" / LANE / "compose.yaml").read_text()
    assert "BWS_ACCESS_TOKEN: ${CHANGE_PROPOSER_BWS_ACCESS_TOKEN}" in compose


def test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper(
    rows: dict[str, RegistryEntry],
) -> None:
    ping_keeper = rows[PING_URL].credential["bws_uuid"]
    projects, secrets = bws_lane_token.declared_reach(rows[TOKEN].credential)
    assert projects == frozenset()
    assert ping_keeper in _lane_secrets()
    assert secrets == _lane_secrets()
    assert secrets == GRANTED


@pytest.mark.parametrize("other", OTHER_LANE_TOKENS)
def test_no_other_lane_token_row_declares_the_same_reach(
    rows: dict[str, RegistryEntry], mirror: RepoMirror, other: str
) -> None:
    """Every other lane shares the observer bearer or the read App's key with this one, and the
    rule compares whole reach sets."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    mine = bws_lane_token.declared_reach(rows[TOKEN].credential)[1]
    theirs = bws_lane_token.declared_reach(rows[other].credential)[1]
    assert mine & theirs
    assert bws_lane_token.sharing_reach(lane_rows, rows[TOKEN].credential) == []
    twin = {**rows[TOKEN].credential, "reach_secrets": sorted(theirs)}
    assert bws_lane_token.sharing_reach(lane_rows, twin) == [other]


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    healthchecks_ping_url.check_row(rows[PING_URL].credential)
    # The check the lane's launcher pings (`scripts/run-change-proposer.sh`), whose URL the
    # executor reads (ADR-0055 amendment 23).
    assert rows[PING_URL].credential["healthchecks_check"] == "sds-change-proposer"


def test_both_rows_are_due(rows: dict[str, RegistryEntry]) -> None:
    """The ping URL's row is dated only after the token's `store` unit completed, so the two issue
    units never waited on a mint at once."""
    assert rows[TOKEN].credential["rotate_requested"] == "2026-11-13"
    assert rows[PING_URL].credential["rotate_requested"] == "2026-11-13"


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_each_row_s_keeper_consumer_is_its_bws_uuid(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    credential = rows[credential_id].credential
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == credential["bws_uuid"]


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_neither_row_has_an_age_anchor(rows: dict[str, RegistryEntry], credential_id: str) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue."""
    credential = rows[credential_id].credential
    assert "created" not in credential
    assert "last_rotated" not in credential


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_a_first_issue_derives_the_issue_plan(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    _, plan = plan_for(rows[credential_id], first_issue=True)
    assert plan.issue


def test_the_read_app_key_is_read_by_the_lane_s_launcher() -> None:
    """The lane container's reader; the Mac readers on the other rows stay until the Mac retires."""
    registry: dict[str, Any] = tomllib.loads((ROOT / REGISTRY_PATH).read_text())
    [row] = [c for c in registry["credential"] if c["id"] == "lanes-read-app-private-key"]
    [reader] = [c for c in row["consumer"] if c.get("ref") == LAUNCHER]
    assert reader["kind"] == "runtime-fetch"
    assert reader["repo"] == "orchestrator"
    assert reader["healthchecks_check"] == "sds-change-proposer"
    assert row["bws_uuid"] in (ROOT / reader["ref"]).read_text()
