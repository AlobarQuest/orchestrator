"""The repository's bump-proposer and rotation-proposer registry rows, read by the code that acts
on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 4: each lane's two rows are handed to the SDS
and due for their first issue. The rows are read here as the executor reads them, at `main`
through `RepoMirror`, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Lane:
    name: str
    service: str
    variable: str
    check: str
    # The `.bws-secrets.toml` secrets the lane reads, its ping URL's keeper among them.
    reach: frozenset[str]

    @property
    def token(self) -> str:
        return f"{self.name}-bws-token"

    @property
    def ping_url(self) -> str:
        return f"{self.name}-ping-url"


BUMP_PROPOSER = Lane(
    name="bump-proposer",
    service="dsmi1mj0xbjrsu8kamdxocp8",
    variable="BUMP_PROPOSER_BWS_ACCESS_TOKEN",
    check="sds-bump-proposer",
    reach=frozenset(
        {
            "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",  # orchestrator-observer
            "314f276d-55ca-4ddc-a24d-b4a3013508cd",  # change-manager/M2M_TOKEN_READ
            "acccb346-4baa-43ec-a1d4-b4a400c048ee",  # change-manager/M2M_TOKEN_PROPOSE
            "b415d03b-f92d-46b1-8a9d-b4f10103df49",  # ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE
            "314a966c-5654-4e3c-8358-b4fd01268775",  # lanes-read-app-private-key
            "b8f547b9-67d7-4417-adc0-b4fd0126b0f0",  # lanes-push-app-private-key
            "2ce0dbc7-eeac-4bf0-8204-b50200c97516",  # factory-events-append-password
            "786f75e2-3d02-4667-8607-b50601312a9d",  # bump-proposer-ping-url's keeper
        }
    ),
)
ROTATION_PROPOSER = Lane(
    name="rotation-proposer",
    service="chyzh4utebg7ruouxxp42d0s",
    variable="ROTATION_PROPOSER_BWS_ACCESS_TOKEN",
    check="sds-rotation-proposer",
    reach=frozenset(
        {
            "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",  # orchestrator-observer
            "acccb346-4baa-43ec-a1d4-b4a400c048ee",  # change-manager/M2M_TOKEN_PROPOSE
            "b415d03b-f92d-46b1-8a9d-b4f10103df49",  # ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE
            "b8f547b9-67d7-4417-adc0-b4fd0126b0f0",  # lanes-push-app-private-key
            "506a5436-4643-4040-8a8b-b50601312be2",  # rotation-proposer-ping-url's keeper
        }
    ),
)
LANES = (BUMP_PROPOSER, ROTATION_PROPOSER)
BY_NAME = pytest.mark.parametrize("lane", LANES, ids=lambda lane: lane.name)
# The `bump-proposer` account's Mac token, the account G-75 also gave the lane's reads.
MAC_TOKEN = "bws-bump-proposer-token"


def _commit_registry(origins: Origins) -> None:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    _commit_registry(origins)
    return rotated_by_sds(mirror)


def _lane_secrets(lane: Lane) -> set[str]:
    manifest: dict[str, Any] = tomllib.loads((ROOT / ".bws-secrets.toml").read_text())
    return {s["uuid"] for s in manifest["secret"] if lane.name in s.get("lanes", [])}


@BY_NAME
def test_both_rows_are_handed_to_the_sds(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    assert {lane.token, lane.ping_url} <= set(rows)


@BY_NAME
def test_the_token_row_is_one_the_lane_token_adapter_acts_on(
    rows: dict[str, RegistryEntry], lane: Lane
) -> None:
    entry = rows[lane.token]
    bws_lane_token.check_row(entry.credential)
    variable = bws_lane_token.lane_variable(entry.destinations, entry.keeper)
    assert variable["uuid"] == lane.service
    # The variable the lane's compose maps to the container's BWS_ACCESS_TOKEN.
    assert variable["key"] == lane.variable
    # The lane phases redeploy the service, never the executor. Read from the row itself: the
    # destination parser reads a missing `redeploy` as false.
    [env] = [c for c in entry.credential["consumer"] if c["kind"] == "coolify-env"]
    assert env["redeploy"] is False
    compose = (ROOT / "lanes" / lane.name / "compose.yaml").read_text()
    assert f"BWS_ACCESS_TOKEN: ${{{lane.variable}}}" in compose


@BY_NAME
def test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper(
    rows: dict[str, RegistryEntry], lane: Lane
) -> None:
    ping_keeper = rows[lane.ping_url].credential["bws_uuid"]
    projects, secrets = bws_lane_token.declared_reach(rows[lane.token].credential)
    assert projects == frozenset()
    assert ping_keeper in _lane_secrets(lane)
    assert secrets == _lane_secrets(lane)
    assert secrets == lane.reach


@BY_NAME
def test_no_other_lane_token_row_declares_the_same_reach(
    rows: dict[str, RegistryEntry], mirror: RepoMirror, lane: Lane
) -> None:
    """The rule compares whole reach sets: this row passes beside every other lane's, and a row
    given another lane's whole reach is caught."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    mine = bws_lane_token.declared_reach(rows[lane.token].credential)[1]
    others = [entry for entry in lane_rows if entry.credential["id"] != lane.token]
    assert others
    assert bws_lane_token.sharing_reach(lane_rows, rows[lane.token].credential) == []
    for entry in others:
        other = entry.credential["id"]
        projects, theirs = bws_lane_token.declared_reach(entry.credential)
        assert theirs != mine
        twin = {
            **rows[lane.token].credential,
            "reach_secrets": sorted(theirs),
            "reach_projects": sorted(projects),
        }
        assert bws_lane_token.sharing_reach(lane_rows, twin) == [other]


def test_the_two_proposers_share_secrets_and_still_differ(rows: dict[str, RegistryEntry]) -> None:
    bump = bws_lane_token.declared_reach(rows[BUMP_PROPOSER.token].credential)[1]
    rotation = bws_lane_token.declared_reach(rows[ROTATION_PROPOSER.token].credential)[1]
    assert len(bump & rotation) == 4
    assert bump != rotation


def test_the_bump_proposer_account_s_mac_token_is_no_lane_token_row(
    mirror: RepoMirror, origins: Origins
) -> None:
    """The Mac token and the lane token belong to one account and see the same secrets, but the
    reach rule and the inbox's other-keeper check read only rows of the lane-token provider, and
    the Mac token's row is neither that provider's nor handed to the SDS."""
    _commit_registry(origins)
    mac = credentials(mirror)[MAC_TOKEN]
    assert mac.credential["provider"] == "bitwarden-sm"
    assert not mac.rotated_by_sds
    lane_ids = {entry.credential["id"] for entry in provider_entries(mirror, "bws-lane-token")}
    assert MAC_TOKEN not in lane_ids
    assert BUMP_PROPOSER.token in lane_ids


@BY_NAME
def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    healthchecks_ping_url.check_row(rows[lane.ping_url].credential)
    # The check the lane's launcher pings, whose URL the executor reads (ADR-0055 amendment 23).
    assert rows[lane.ping_url].credential["healthchecks_check"] == lane.check


@BY_NAME
def test_both_rows_are_due(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    """The ping URL's row is dated only after the token's `store` unit completed, so the two issue
    units never waited on a mint at once."""
    assert rows[lane.token].credential["rotate_requested"] == "2026-11-18"
    assert rows[lane.ping_url].credential["rotate_requested"] == "2026-11-19"


@BY_NAME
@pytest.mark.parametrize("which", ["token", "ping_url"])
def test_each_row_s_keeper_consumer_is_its_bws_uuid(
    rows: dict[str, RegistryEntry], lane: Lane, which: str
) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    credential = rows[getattr(lane, which)].credential
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == credential["bws_uuid"]


@BY_NAME
@pytest.mark.parametrize("which", ["token", "ping_url"])
def test_neither_row_has_an_age_anchor(
    rows: dict[str, RegistryEntry], lane: Lane, which: str
) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue."""
    credential = rows[getattr(lane, which)].credential
    assert "created" not in credential
    assert "last_rotated" not in credential


@BY_NAME
@pytest.mark.parametrize("which", ["token", "ping_url"])
def test_a_first_issue_derives_the_issue_plan(
    rows: dict[str, RegistryEntry], lane: Lane, which: str
) -> None:
    _, plan = plan_for(rows[getattr(lane, which)], first_issue=True)
    assert plan.issue
