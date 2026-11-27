"""The lane secrets broker's registry row, read by the code that acts on it.

ADR-0055 amendment 34: the broker's own token rotates through the `bws-lane-token` adapter's
rotation plan, anchored on the date Devon pasted the first token, and its row is handed to the SDS
once amendment 34 item 9's exit checks 1 to 3 pass, so the first real rotation (check 4) can run.
The row is read here as the executor reads it, from this repository's own `.cred-consumers.toml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rotation_proposer.cli import _first_issue, _pass
from rotation_proposer.findings import Due
from rotation_proposer.generate import plan_for
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
        # Phase C's second batch (ADR-0055 Amendment 32, item 4), given at an HQ gate before its
        # release.
        "3b9503da-eb7e-401d-b4a7-b4a400c07efb",  # change-manager/M2M_TOKEN_OBSERVE
        "1cfc8352-c960-4afc-bd49-b4ff00e8783e",  # unit-verifier-ping-url
        "5fc44897-b4df-44ae-8491-b5000129d9be",  # membership-check-ping-url
        "4f7f3d8b-a83e-4ae5-a665-b4ff0004b875",  # deploy-watcher-ping-url
        # Phase C's third batch (ADR-0055 Amendment 32, item 4), given at HQ gate G-106.
        "acccb346-4baa-43ec-a1d4-b4a400c048ee",  # change-manager/M2M_TOKEN_PROPOSE
        "7651ccc2-7e13-417b-86da-b50100c0d4b2",  # change-proposer-ping-url
        "936bbbf2-dc3b-4343-979a-b501013afc87",  # rotation-decomposer-ping-url
        "506a5436-4643-4040-8a8b-b50601312be2",  # rotation-proposer-ping-url
        # Phase C's fourth batch (ADR-0055 Amendment 32, item 4), given at HQ gate G-109.
        "55fbae20-e503-4eb5-b75c-b5040164d394",  # work-carrier-ping-url
        "786f75e2-3d02-4667-8607-b50601312a9d",  # bump-proposer-ping-url
        "9b2722ea-8ec1-425b-ac2e-b4ff00cba931",  # revision-watcher-ping-url
        "2ce0dbc7-eeac-4bf0-8204-b50200c97516",  # factory-events-append-password
        "0bf066e9-28c1-4aa7-8c3d-b50300d492a6",  # prod-coolify-read-token
        "726a18ba-7a38-4ecc-aa03-b49a015fd302",  # APP_BRAIN_READ_KEY
    }
)


@pytest.fixture
def entry(mirror: RepoMirror, origins: Origins) -> RegistryEntry:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
    return credentials(mirror)[TOKEN]


def test_the_row_is_handed_to_the_sds_with_a_requested_date(
    mirror: RepoMirror, entry: RegistryEntry
) -> None:
    assert TOKEN in rotated_by_sds(mirror)
    assert entry.credential["rotate_requested"] == "2026-11-27"


def test_a_dry_proposer_pass_would_generate_the_row_s_rotation_package(
    entry: RegistryEntry, tmp_path: Path
) -> None:
    """The proposer acts only on a handed-over row: its due item is built here from the row's own
    `rotated_by_sds` and `rotate_requested`, as infraops serves them, and a dry pass (no client, no
    observer, no grant read) generates revision 1 for the rotation plan without writing anything."""
    root = tmp_path / "intent-packages"
    root.mkdir()
    requested = entry.credential["rotate_requested"]
    due = Due(
        TOKEN, entry.credential["class"], "requested", requested, requested, entry.rotated_by_sds
    )
    [outcome] = _pass(
        [due],
        {},
        [],
        None,
        None,
        root,
        lambda: {TOKEN: entry},
        granted=None,
        last_rotated={TOKEN: entry.credential["created"]},
        report_not_handed_over=True,
    )
    assert (outcome.credential_id, outcome.status) == (TOKEN, "would-generate")
    assert list(root.iterdir()) == []


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


def test_the_row_is_anchored_on_the_paste_and_derives_the_rotation_plan(
    entry: RegistryEntry,
) -> None:
    """infraops serves `created` as `last_rotated` (`rotationAnchor` in infraops-mcp-server), so the
    proposer reads the token as issued and derives the rotation plan, which revokes the old token,
    never the issue plan, whose `store` would refuse the hand-pasted keeper (amendment 34 item 3).
    """
    assert entry.credential["created"] == "2026-11-20"
    assert "last_rotated" not in entry.credential
    first_issue = _first_issue({TOKEN: entry.credential["created"]}, TOKEN)
    assert first_issue is False
    adapter, plan = plan_for(entry, first_issue=first_issue)
    assert adapter == bws_lane_token.PROVIDER
    assert not plan.issue
