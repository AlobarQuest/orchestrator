"""The repository's execution-watch registry rows, read by the code that acts on them.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 2: both rows are due for their first issue.
The rows are read here as the executor reads them, at `main` through `RepoMirror`, from this
repository's own `.cred-consumers.toml`.
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
TOKEN = "execution-watch-bws-token"
PING_URL = "execution-watch-ping-url"
LANE = "execution-watch"


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
    rows: dict[str, RegistryEntry], mirror: RepoMirror
) -> None:
    entry = rows[TOKEN]
    bws_lane_token.check_row(entry.credential)
    variable = bws_lane_token.lane_variable(entry.destinations, entry.keeper)
    assert variable["uuid"] == "svkt4ux8o16dnl7n3k6qcd99"
    assert variable["key"] == "EXECUTION_WATCH_BWS_ACCESS_TOKEN"
    assert bws_lane_token.declared_reach(entry.credential) == (frozenset(), _lane_secrets())
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    assert bws_lane_token.sharing_reach(lane_rows, entry.credential) == []


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    entry = rows[PING_URL]
    healthchecks_ping_url.check_row(entry.credential)
    assert entry.credential["bws_uuid"] in _lane_secrets()


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_each_row_is_due_and_has_no_age_anchor(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue; `rotate_requested` is what makes a row due."""
    credential = rows[credential_id].credential
    assert "rotate_requested" in credential
    assert "created" not in credential
    assert "last_rotated" not in credential


@pytest.mark.parametrize("credential_id", [TOKEN, PING_URL])
def test_a_first_issue_derives_the_issue_plan(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    _, plan = plan_for(rows[credential_id], first_issue=True)
    assert plan.issue
