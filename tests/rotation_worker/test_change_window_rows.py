"""The repository's change-window registry rows, read by the code that acts on them.

ADR-0055 amendments 19 (item 8) and 30, and ADR-0059 increment 8: the change window reads its work
secrets through the lane secrets broker, so it has no token row. Its ping URL's row is handed to
the SDS and due for its first issue, read from the Mac window's existing check, whose name has
spaces and whose slug is empty; its digest sender token's row is not handed over. The rows are
read here as the executor reads them, at `main` through `RepoMirror`, from this repository's own
`.cred-consumers.toml`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from rotation_proposer.generate import plan_for, render
from rotation_worker.adapters import bws_lane_token, healthchecks_ping_url
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    credentials,
    provider_entries,
)
from scripts.secrets_broker import load_allowlist
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
LANE = "change-window"
PING_URL = "change-window-ping-url"
PING_KEEPER = "1d6a0ab4-e164-4fe2-8ccb-b50a00e255c3"
SENDER = "alert-hub-report-change-window-sender"
SENDER_KEEPER = "71616bf5-14ba-4888-b3f8-b50a00e13ed2"
CHECK = "Change Manager OPs Check"
LAUNCHER = "scripts/run-change-window.sh"
COMPOSE = ROOT / "lanes" / LANE / "compose.yaml"
ALLOWLIST = ROOT / "lanes" / "secrets-broker" / "allowlist.toml"


def _commit_registry(origins: Origins) -> None:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    _commit_registry(origins)
    return credentials(mirror)


def _keeper(credential: object) -> str:
    assert isinstance(credential, dict)
    [keeper] = [c for c in credential["consumer"] if c["kind"] == "bws-secret"]
    return str(keeper["uuid"])


def test_the_ping_url_row_is_handed_to_the_sds(rows: dict[str, RegistryEntry]) -> None:
    assert rows[PING_URL].rotated_by_sds
    assert rows[PING_URL].credential["bws_uuid"] == PING_KEEPER


def test_the_ping_url_row_names_only_its_keeper_and_the_launcher_s_check(
    rows: dict[str, RegistryEntry],
) -> None:
    healthchecks_ping_url.check_row(rows[PING_URL].credential)
    # The executor reads the URL of the one check with this exact name (`readers.PingUrls`), so
    # the name is the launcher's, spaces and capitals included; no slug is read.
    assert rows[PING_URL].credential["healthchecks_check"] == CHECK
    launcher = (ROOT / LAUNCHER).read_text()
    assert f'CHECK_NAME="{CHECK}"' in launcher
    assert f'sds_outcome_begin "$CHECK_NAME" {LANE}' in launcher


def test_each_keeper_consumer_is_the_bws_uuid(rows: dict[str, RegistryEntry]) -> None:
    """The executor derives the keeper from `bws_uuid`, so a keeper consumer naming another id
    would declare a copy the executor never writes."""
    for credential_id, keeper in ((PING_URL, PING_KEEPER), (SENDER, SENDER_KEEPER)):
        credential = rows[credential_id].credential
        assert credential["bws_uuid"] == keeper
        assert _keeper(credential) == keeper


def test_the_ping_url_row_is_due_for_its_first_issue(rows: dict[str, RegistryEntry]) -> None:
    credential = rows[PING_URL].credential
    assert credential["rotate_requested"] == "2026-11-22"
    # infraops serves `created` as `last_rotated`, which would make the proposer skip a first
    # issue.
    assert "created" not in credential
    assert "last_rotated" not in credential


def test_a_first_issue_renders_an_issue_package_reading_the_check(
    rows: dict[str, RegistryEntry],
) -> None:
    _, plan = plan_for(rows[PING_URL], first_issue=True)
    assert plan.issue
    package = render(
        rows[PING_URL], "requested-2026-11-22", "2026-11-22T00:00:00Z", first_issue=True
    )
    assert package["package_id"] == f"rotation-{PING_URL}"
    assert CHECK in str(package["acceptance"])


def test_the_sender_row_is_not_handed_to_the_sds(rows: dict[str, RegistryEntry]) -> None:
    sender = rows[SENDER]
    assert not sender.rotated_by_sds
    assert "rotate_requested" not in sender.credential
    assert sender.credential["class"] == "alert-hub-sender-token"
    assert "report-change-window" in sender.credential["provider_identity"]
    [reader] = [c for c in sender.credential["consumer"] if c["kind"] == "runtime-fetch"]
    assert reader["ref"] == LAUNCHER
    assert reader["healthchecks_check"] == CHECK
    assert SENDER_KEEPER in (ROOT / LAUNCHER).read_text()


def test_the_lane_has_no_token_row_and_reads_through_the_broker(
    rows: dict[str, RegistryEntry], mirror: RepoMirror
) -> None:
    """The lane holds no token, so no lane-token row names its variable, and the reach rule, which
    compares lane-token rows only, still passes for every one of them beside these rows."""
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    variables = {c.get("key") for entry in lane_rows for c in entry.credential.get("consumer", [])}
    assert "CHANGE_WINDOW_BWS_ACCESS_TOKEN" not in variables
    assert f"{LANE}-bws-token" not in rows
    for entry in lane_rows:
        assert bws_lane_token.sharing_reach(lane_rows, entry.credential) == []
    compose = COMPOSE.read_text()
    assert "BWS_ACCESS_TOKEN:" not in compose
    assert f"- /data/sds/broker/{LANE}:/run/sds-broker:ro" in compose


def test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry(
    rows: dict[str, RegistryEntry],
) -> None:
    """The lane reads its ping-URL secret through the broker, so the keeper the row names must be
    both a manifest secret for the lane (its `--token` proof) and in its allowlist entry (or the
    broker refuses the outcome alarm's read on a `--submit` pass)."""
    keeper = rows[PING_URL].credential["bws_uuid"]
    manifest = tomllib.loads((ROOT / ".bws-secrets.toml").read_text())
    assert keeper in {s["uuid"] for s in manifest["secret"] if LANE in s.get("lanes", [])}
    assert keeper in load_allowlist(ALLOWLIST)[LANE]
