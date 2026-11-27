"""The lane-token rows' `helper_target`, read from this repository's own `.cred-consumers.toml`.

ADR-0055 amendment 29 item 3 (D-63 C): a lane token's reissue proves the restarted lane loaded the
new token by the VPS helper's `loaded` fingerprint, through one consumers-only helper target per
lane. Every lane that loads its own token names its target; a broker lane's container loads no
`BWS_ACCESS_TOKEN`, so its row names none. The broker's own token is the one row that isn't a lane
and names a target, `secrets-broker`, the broker's container (amendment 34 item 4). The field is
the `bws_lane_token` adapter's alone: the package's destinations, plan and rendered revision are
the same with it and without it, and every other in-repo loader of the file reads it unchanged.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from membership_check.declaration import hands_over
from rotation_proposer.generate import plan_for, render
from rotation_worker.adapters import bws_lane_token
from rotation_worker.destinations import credential_destinations
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import (
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    credentials,
)
from scripts.deploy_orchestrator import lanes_in_registry
from tests.rotation_worker.conftest import Origins

ROOT = Path(__file__).resolve().parents[2]
BROKER_TOKEN = "secrets-broker-bws-token"
BROKER_TARGET = "secrets-broker"
TOKEN_SUFFIX = "_BWS_ACCESS_TOKEN"


def _registry_text() -> str:
    return (ROOT / REGISTRY_PATH).read_text()


def _broker_lanes() -> set[str]:
    allowlist: dict[str, Any] = tomllib.loads(
        (ROOT / "lanes/secrets-broker/allowlist.toml").read_text()
    )
    return set(allowlist["lanes"])


def _lane(credential: Mapping[str, Any]) -> str:
    """The lane a lane-token row's `coolify-env` variable belongs to: `<LANE>_BWS_ACCESS_TOKEN`."""
    (variable,) = [c for c in credential["consumer"] if c["kind"] == "coolify-env"]
    key = str(variable["key"])
    assert key.endswith(TOKEN_SUFFIX)
    return key.removesuffix(TOKEN_SUFFIX).lower().replace("_", "-")


def _loading_service(credential: Mapping[str, Any]) -> str:
    """The one compose service in the lane's file whose `BWS_ACCESS_TOKEN` is the row's variable."""
    (variable,) = [c for c in credential["consumer"] if c["kind"] == "coolify-env"]
    compose = yaml.safe_load((ROOT / "lanes" / _lane(credential) / "compose.yaml").read_text())
    loading = [
        name
        for name, service in compose["services"].items()
        if (service.get("environment") or {}).get("BWS_ACCESS_TOKEN") == f"${{{variable['key']}}}"
    ]
    assert len(loading) == 1, loading
    return loading[0]


def _without_target(credential: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in credential.items() if k != bws_lane_token.HELPER_TARGET_FIELD}


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    """Every registry row, read as the executor reads it: at `main`, through `RepoMirror`."""
    for repository in REGISTRY_REPOSITORIES:
        text = _registry_text() if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
    return credentials(mirror)


def _lane_tokens(rows: Mapping[str, RegistryEntry]) -> dict[str, Mapping[str, Any]]:
    return {
        cid: entry.credential
        for cid, entry in rows.items()
        if entry.credential.get("class") == bws_lane_token.PROVIDER
    }


def test_every_lane_holding_its_own_token_names_its_helper_target(
    rows: dict[str, RegistryEntry],
) -> None:
    brokered = _broker_lanes()
    missing = sorted(
        cid
        for cid, credential in _lane_tokens(rows).items()
        if credential.get("rotated_by_sds") is True
        and _lane(credential) not in brokered
        and bws_lane_token.HELPER_TARGET_FIELD not in credential
    )
    assert missing == []


def test_no_broker_lane_names_one_and_the_broker_s_own_token_names_the_broker(
    rows: dict[str, RegistryEntry],
) -> None:
    """A broker lane's container loads no token, so its `loaded` could never match; the broker's
    own container loads its token as `BWS_ACCESS_TOKEN` (ADR-0055 amendment 34 item 4)."""
    brokered = _broker_lanes()
    naming = sorted(
        cid
        for cid, credential in _lane_tokens(rows).items()
        if bws_lane_token.HELPER_TARGET_FIELD in credential and _lane(credential) in brokered
    )
    assert naming == []
    assert bws_lane_token.helper_target(rows[BROKER_TOKEN].credential) == BROKER_TARGET


def test_each_target_is_the_service_that_loads_the_row_s_variable(
    rows: dict[str, RegistryEntry],
) -> None:
    for credential in _lane_tokens(rows).values():
        target = bws_lane_token.helper_target(credential)
        if target is not None:
            assert target == _loading_service(credential), credential["id"]


def test_every_row_naming_a_target_passes_the_row_check(rows: dict[str, RegistryEntry]) -> None:
    named = [c for c in _lane_tokens(rows).values() if bws_lane_token.HELPER_TARGET_FIELD in c]
    assert named
    for credential in named:
        bws_lane_token.check_row(credential)


def test_the_field_is_a_row_field_never_a_consumer_s() -> None:
    """A line after the row's first `[[credential.consumer]]` would belong to that consumer."""
    document: dict[str, Any] = tomllib.loads(_registry_text())
    for credential in document["credential"]:
        for consumer in credential.get("consumer", []):
            assert bws_lane_token.HELPER_TARGET_FIELD not in consumer, credential["id"]


def test_the_package_is_the_same_with_and_without_the_field(
    rows: dict[str, RegistryEntry],
) -> None:
    """Destinations, the plan and the rendered revision ignore `helper_target`, so no standing
    package in intent-packages changes."""
    for cid, entry in rows.items():
        if bws_lane_token.HELPER_TARGET_FIELD not in entry.credential:
            continue
        bare = RegistryEntry(entry.repository, entry.commit, _without_target(entry.credential))
        assert credential_destinations(entry.credential) == credential_destinations(
            bare.credential
        ), cid
        for first_issue in (True, False):
            assert plan_for(entry, first_issue=first_issue) == plan_for(
                bare, first_issue=first_issue
            ), cid
            assert render(
                entry, "2026-11-22-requested", "2026-11-22T00:00:00Z", first_issue=first_issue
            ) == render(
                bare, "2026-11-22-requested", "2026-11-22T00:00:00Z", first_issue=first_issue
            ), cid


def test_the_other_in_repo_loaders_read_the_file_unchanged() -> None:
    """The membership check's parse (D-59) and the deploy command's lane read accept the file, and
    answer what they answer without the field."""
    text = _registry_text()
    bare = "\n".join(line for line in text.split("\n") if not line.startswith("helper_target = "))
    assert text != bare
    assert hands_over(text) == hands_over(bare)
    assert hands_over(text).target is True
    assert lanes_in_registry(text) == lanes_in_registry(bare)


def test_the_install_page_lists_each_target_s_container_as_derived(
    rows: dict[str, RegistryEntry],
) -> None:
    """The operations page's table is `<service>-<resource uuid>`, from the lane's compose file and
    the row's `coolify-env` consumer, for exactly the rows naming a target."""
    page = (ROOT / "docs/operations/rotation-executor.md").read_text()
    listed = dict(re.findall(r"^ {3}\| `([a-z-]+)` \| `([a-z0-9-]+)` \|$", page, re.MULTILINE))
    derived = {}
    for credential in _lane_tokens(rows).values():
        target = bws_lane_token.helper_target(credential)
        if target is not None:
            (variable,) = [c for c in credential["consumer"] if c["kind"] == "coolify-env"]
            derived[target] = f"{_loading_service(credential)}-{variable['uuid']}"
    assert listed == derived
    assert listed[BROKER_TARGET] == "secrets-broker-wogvks6obmvv9fdotlgnw7r3"
