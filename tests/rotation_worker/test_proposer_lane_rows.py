"""The repository's bump-proposer and rotation-proposer ping URL rows, read by the code that acts
on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 4: each lane's ping URL row is handed to the SDS
and due for its first issue. The lanes' token rows were removed by ADR-0055 Amendment 32 item 5.3,
and `test_retired_lane_token_rows.py` holds them out. The rows are read here as the executor reads
them, at `main` through `RepoMirror`, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rotation_proposer.generate import plan_for
from rotation_worker.adapters import healthchecks_ping_url
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
    check: str

    @property
    def ping_url(self) -> str:
        return f"{self.name}-ping-url"


BUMP_PROPOSER = Lane(name="bump-proposer", check="sds-bump-proposer")
ROTATION_PROPOSER = Lane(name="rotation-proposer", check="sds-rotation-proposer")
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
def test_the_ping_url_row_is_handed_to_the_sds(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    assert lane.ping_url in rows


def test_the_bump_proposer_account_s_mac_token_is_no_lane_token_row(
    mirror: RepoMirror, origins: Origins
) -> None:
    """The Mac token's row is neither the lane-token provider's nor handed to the SDS, so the
    reach rule and the inbox's other-keeper check, which read only that provider's rows, never
    read it."""
    _commit_registry(origins)
    mac = credentials(mirror)[MAC_TOKEN]
    assert mac.credential["provider"] == "bitwarden-sm"
    assert not mac.rotated_by_sds
    lane_ids = {entry.credential["id"] for entry in provider_entries(mirror, "bws-lane-token")}
    assert MAC_TOKEN not in lane_ids


@BY_NAME
def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    healthchecks_ping_url.check_row(rows[lane.ping_url].credential)
    assert rows[lane.ping_url].credential["bws_uuid"] in _lane_secrets(lane)
    # The check the lane's launcher pings, whose URL the executor reads (ADR-0055 amendment 23).
    assert rows[lane.ping_url].credential["healthchecks_check"] == lane.check


@BY_NAME
def test_the_ping_url_row_is_due(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    assert rows[lane.ping_url].credential["rotate_requested"] == "2026-11-19"


@BY_NAME
def test_the_ping_url_row_s_keeper_consumer_is_its_bws_uuid(
    rows: dict[str, RegistryEntry], lane: Lane
) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    credential = rows[lane.ping_url].credential
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == credential["bws_uuid"]


@BY_NAME
def test_the_ping_url_row_has_no_age_anchor(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue."""
    credential = rows[lane.ping_url].credential
    assert "created" not in credential
    assert "last_rotated" not in credential


@BY_NAME
def test_a_first_issue_derives_the_issue_plan(rows: dict[str, RegistryEntry], lane: Lane) -> None:
    _, plan = plan_for(rows[lane.ping_url], first_issue=True)
    assert plan.issue
