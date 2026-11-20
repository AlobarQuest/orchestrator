"""The repository's estate-landing registry row, read by the code that acts on it.

ADR-0055 amendments 19 (item 8) and 30, and ADR-0059 increment 5: estate landing reads its secrets
through the lane secrets broker, so it has no token row, and its one row, the ping URL's, is handed
to the SDS and dated by a later change. The row is read here as the executor reads it, at `main`
through `RepoMirror`, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

from pathlib import Path

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
LANE = "estate-landing"
PING_URL = "estate-landing-ping-url"
KEEPER = "11e940d7-e43a-4ebd-9026-b508017d9cef"
CHECK = "sds-estate-landing"
LAUNCHER = "scripts/run-estate-landing.sh"
COMPOSE = ROOT / "lanes" / LANE / "compose.yaml"


def _commit_registry(origins: Origins) -> None:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    _commit_registry(origins)
    return rotated_by_sds(mirror)


def test_the_ping_url_row_is_handed_to_the_sds(rows: dict[str, RegistryEntry]) -> None:
    assert PING_URL in rows
    assert rows[PING_URL].credential["bws_uuid"] == KEEPER


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    healthchecks_ping_url.check_row(rows[PING_URL].credential)
    # The check the lane's launcher pings, whose URL the executor reads (ADR-0055 amendment 23).
    assert rows[PING_URL].credential["healthchecks_check"] == CHECK
    assert f"sds_deadman_arm {CHECK} " in (ROOT / LAUNCHER).read_text()


def test_the_keeper_consumer_is_the_bws_uuid(rows: dict[str, RegistryEntry]) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    credential = rows[PING_URL].credential
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == credential["bws_uuid"]


def test_the_row_is_not_yet_due(rows: dict[str, RegistryEntry]) -> None:
    """A later change dates it (deploy.md, estate landing's cut-over item 5)."""
    assert "rotate_requested" not in rows[PING_URL].credential


def test_the_row_has_no_age_anchor(rows: dict[str, RegistryEntry]) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue."""
    credential = rows[PING_URL].credential
    assert "created" not in credential
    assert "last_rotated" not in credential


def test_a_first_issue_derives_the_issue_plan(rows: dict[str, RegistryEntry]) -> None:
    _, plan = plan_for(rows[PING_URL], first_issue=True)
    assert plan.issue


def test_the_lane_has_no_token_row_and_reads_through_the_broker(
    rows: dict[str, RegistryEntry], mirror: RepoMirror
) -> None:
    """The lane holds no token, so no lane-token row names its variable, and the reach rule, which
    compares lane-token rows only, still passes for every one of them beside this row."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    variables = {
        c.get("key") for entry in lane_rows for c in entry.credential.get("consumer", [])
    }
    assert "ESTATE_LANDING_BWS_ACCESS_TOKEN" not in variables
    assert f"{LANE}-bws-token" not in rows
    for entry in lane_rows:
        assert bws_lane_token.sharing_reach(lane_rows, entry.credential) == []
    compose = COMPOSE.read_text()
    assert "BWS_ACCESS_TOKEN:" not in compose
    assert f"- /data/sds/broker/{LANE}:/run/sds-broker:ro" in compose


def test_the_read_app_key_row_lists_the_lane_s_container_reader(
    mirror: RepoMirror, origins: Origins
) -> None:
    """The container reads the read App's key every pass, bare or `--submit`, and pings its check
    every pass. The row is hand-rotated, so it is read through `credentials`, not
    `rotated_by_sds`."""
    _commit_registry(origins)
    key = credentials(mirror)["lanes-read-app-private-key"].credential
    readers = [
        c
        for c in key["consumer"]
        if c["kind"] == "runtime-fetch" and c["repo"] == "orchestrator" and c["ref"] == LAUNCHER
    ]
    [reader] = readers
    assert reader["healthchecks_check"] == CHECK
    assert "secrets broker" in reader["note"]
    assert key["bws_uuid"] in (ROOT / LAUNCHER).read_text()
