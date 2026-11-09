"""The repository's landing-ledger registry rows, read by the code that acts on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 3: the token's row is due for its first issue
and the ping URL's row is handed to the SDS but not yet due. The rows are read here as the
executor reads them, at `main` through `RepoMirror`, from this repository's own
`.cred-consumers.toml`.
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
TOKEN = "landing-ledger-bws-token"
PING_URL = "landing-ledger-ping-url"
OTHER_LANE_TOKEN = "execution-watch-bws-token"
LANE = "landing-ledger"
# The three secrets Devon granted the lane's account a direct read on (HQ gate G-18).
GRANTED = frozenset(
    {
        "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",  # orchestrator-observer
        "314a966c-5654-4e3c-8358-b4fd01268775",  # lanes-read-app-private-key
        "ebabdd63-2eff-4b8e-99b7-b4fd01754eae",  # landing-ledger-ping-url's keeper
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
    assert variable["uuid"] == "gs1z8orebdnq9ug574sia6fs"
    assert variable["key"] == "LANDING_LEDGER_BWS_ACCESS_TOKEN"


def test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper(
    rows: dict[str, RegistryEntry],
) -> None:
    ping_keeper = rows[PING_URL].credential["bws_uuid"]
    projects, secrets = bws_lane_token.declared_reach(rows[TOKEN].credential)
    assert projects == frozenset()
    assert secrets == _lane_secrets() | {ping_keeper}
    assert secrets == GRANTED


def test_no_other_lane_token_row_declares_the_same_reach(
    rows: dict[str, RegistryEntry], mirror: RepoMirror
) -> None:
    """The two lanes share the observer bearer, and the rule compares whole reach sets."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    mine = bws_lane_token.declared_reach(rows[TOKEN].credential)[1]
    theirs = bws_lane_token.declared_reach(rows[OTHER_LANE_TOKEN].credential)[1]
    assert mine & theirs
    assert bws_lane_token.sharing_reach(lane_rows, rows[TOKEN].credential) == []
    twin = {**rows[TOKEN].credential, "reach_secrets": sorted(theirs)}
    assert bws_lane_token.sharing_reach(lane_rows, twin) == [OTHER_LANE_TOKEN]


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    healthchecks_ping_url.check_row(rows[PING_URL].credential)


def test_only_the_token_row_is_due(rows: dict[str, RegistryEntry]) -> None:
    """The ping URL's row is dated by a later change, after the token's `store` unit completes."""
    assert rows[TOKEN].credential["rotate_requested"] == "2026-11-09"
    assert "rotate_requested" not in rows[PING_URL].credential


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
    registry: dict[str, Any] = tomllib.loads((ROOT / REGISTRY_PATH).read_text())
    [row] = [c for c in registry["credential"] if c["id"] == "lanes-read-app-private-key"]
    [reader] = [c for c in row["consumer"] if c["kind"] == "runtime-fetch"]
    assert reader["ref"] == "scripts/run-landing-ledger.sh"
    assert reader["healthchecks_check"] == "sds-landing-ledger"
    assert row["bws_uuid"] in (ROOT / reader["ref"]).read_text()
