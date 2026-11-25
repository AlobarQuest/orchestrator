"""The lane secrets broker's registry row, read by the code that acts on it.

ADR-0055 amendment 30 item 8: the broker's own token is the one BWS token that stays manual until
Phase C, so its row is not handed to the SDS. The row is read here as the executor reads it, from
this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rotation_worker.adapters import bws_lane_token
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
TOKEN = "secrets-broker-bws-token"
KEEPER = "1d78f917-6065-4d40-ad1c-b508013d8c9a"
SERVICE = "wogvks6obmvv9fdotlgnw7r3"
VARIABLE = "SECRETS_BROKER_BWS_ACCESS_TOKEN"
PROJECTS = frozenset(
    {
        "b09acc9d-87af-4830-9361-b49700d2bc8a",  # SDS Operator
        "b3df5419-c5be-4abf-a157-b4fd01259df8",  # SDS / Lane Apps
    }
)
SECRETS = frozenset(
    {
        "314f276d-55ca-4ddc-a24d-b4a3013508cd",  # change-manager/M2M_TOKEN_READ
        "35557473-f80c-44e3-892a-b507013158b1",  # inert-landing-ping-url
        "11e940d7-e43a-4ebd-9026-b508017d9cef",  # estate-landing-ping-url
        "430691e2-4a36-4261-a28f-b50900e064b7",  # bump-driver-ping-url
        "750f737f-4cb6-4876-9a98-b48200ea1c0b",  # CODE_BRAIN_CONTRIBUTOR_KEY
        "da8134b0-565f-45c8-8965-b48200ea1c40",  # INFRA_BRAIN_CONTRIBUTOR_KEY
        "2f708d08-ea5d-4d11-bde0-b50200c9a60b",  # factory-events-read-password
        "46e714ca-32b7-4e89-9817-b509013363a5",  # follow-up-mint-ping-url
        "3d41395a-06c3-47d5-ac85-b50a00dfbc2f",  # change-window-coolify-token
        "aa7c76af-bd52-4796-9d13-b50a00e035de",  # change-manager/M2M_TOKEN_EXECUTE
        "0e9ebf2d-5b62-4f81-a731-b50a00e0df18",  # change-window-anthropic-key
        "71616bf5-14ba-4888-b3f8-b50a00e13ed2",  # ALERT_HUB_SENDER_TOKEN_REPORT_CHANGE_WINDOW
        "1d6a0ab4-e164-4fe2-8ccb-b50a00e255c3",  # change-window-ping-url
        # HQ gate G-100 (ADR-0055 Amendment 32, item 4): Phase C's first batch.
        "f793576f-e9aa-4f9d-8089-b4a000b9e2d5",  # orchestrator-observer
        "97802aac-5a7f-45e8-8185-b4fc00cd318e",  # execution-watch-ping-url
        "ebabdd63-2eff-4b8e-99b7-b4fd01754eae",  # landing-ledger-ping-url
        "872684c0-899e-43b7-9c2d-b50000e88b21",  # pin-watcher-ping-url
    }
)


@pytest.fixture
def entry(mirror: RepoMirror, origins: Origins) -> RegistryEntry:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
    return credentials(mirror)[TOKEN]


def test_the_row_is_not_handed_to_the_sds(mirror: RepoMirror, entry: RegistryEntry) -> None:
    assert TOKEN not in rotated_by_sds(mirror)
    assert "rotate_requested" not in entry.credential


def test_the_row_is_one_the_lane_token_adapter_accepts(entry: RegistryEntry) -> None:
    bws_lane_token.check_row(entry.credential)
    variable = bws_lane_token.lane_variable(entry.destinations, entry.keeper)
    assert variable["uuid"] == SERVICE
    assert variable["key"] == VARIABLE


def test_the_keeper_consumer_is_the_bws_uuid(entry: RegistryEntry) -> None:
    [keeper] = [c for c in entry.credential["consumer"] if c["kind"] == "bws-secret"]
    assert keeper["uuid"] == entry.credential["bws_uuid"] == KEEPER


def test_the_row_declares_the_granted_projects_and_secrets(entry: RegistryEntry) -> None:
    assert bws_lane_token.declared_reach(entry.credential) == (PROJECTS, SECRETS)


def test_no_other_lane_token_row_declares_the_same_reach(
    entry: RegistryEntry, mirror: RepoMirror
) -> None:
    lane_rows = provider_entries(mirror, bws_lane_token.PROVIDER)
    assert len(lane_rows) > 1
    assert bws_lane_token.sharing_reach(lane_rows, entry.credential) == []


def test_the_row_has_no_age_anchor(entry: RegistryEntry) -> None:
    assert "created" not in entry.credential
    assert "last_rotated" not in entry.credential
