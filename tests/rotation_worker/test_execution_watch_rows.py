"""The repository's execution-watch ping URL row, read by the code that acts on it.

ADR-0055 amendment 19 item 8 and ADR-0059 increment 2: the ping URL's row is handed to the SDS and
due for its first issue. The lane's token row was removed by ADR-0055 Amendment 32 item 5.3, and
`test_retired_lane_token_rows.py` holds it out. The row is read here as the executor reads it, at
`main` through `RepoMirror`, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

import tomllib
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
    rotated_by_sds,
)
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
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


def test_the_ping_url_row_is_handed_to_the_sds(rows: dict[str, RegistryEntry]) -> None:
    assert PING_URL in rows


def test_the_ping_url_row_names_only_its_keeper(rows: dict[str, RegistryEntry]) -> None:
    entry = rows[PING_URL]
    healthchecks_ping_url.check_row(entry.credential)
    assert entry.credential["bws_uuid"] in _lane_secrets()
    # The check the lane's launcher pings (`scripts/run-execution-watch.sh`), whose URL the
    # executor reads (ADR-0055 amendment 23).
    assert entry.credential["healthchecks_check"] == "sds-execution-watch"


def test_the_ping_url_row_is_due_and_has_no_age_anchor(rows: dict[str, RegistryEntry]) -> None:
    """infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    issue; `rotate_requested` is what makes a row due."""
    credential = rows[PING_URL].credential
    assert "rotate_requested" in credential
    assert "created" not in credential
    assert "last_rotated" not in credential


def test_a_first_issue_derives_the_issue_plan(rows: dict[str, RegistryEntry]) -> None:
    _, plan = plan_for(rows[PING_URL], first_issue=True)
    assert plan.issue
