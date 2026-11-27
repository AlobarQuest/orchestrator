"""The thirteen lane-token rows ADR-0055 Amendment 32 item 5.3 removed stay removed.

Each lane reads its secrets through the lane secrets broker, its token was revoked and its
`<LANE>_BWS_ACCESS_TOKEN` resource variable deleted, so a row for it would send the executor to
rotate a token that no longer exists. The registry is read whole, not through `rotated_by_sds`, so a
row that came back unhanded would fail too. The keeper secrets in `Rotation / Keepers` stay.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from rotation_worker.registry import REGISTRY_PATH

ROOT = Path(__file__).resolve().parents[2]
RETIRED_LANES = (
    "bump-proposer",
    "change-proposer",
    "deploy-watcher",
    "execution-watch",
    "inert-landing",
    "landing-ledger",
    "membership-check",
    "pin-watcher",
    "revision-watcher",
    "rotation-decomposer",
    "rotation-proposer",
    "unit-verifier",
    "work-carrier",
)
BY_LANE = pytest.mark.parametrize("lane", RETIRED_LANES)


def _credentials() -> list[dict[str, Any]]:
    return tomllib.loads((ROOT / REGISTRY_PATH).read_text())["credential"]


def _variable(lane: str) -> str:
    return f"{lane.upper().replace('-', '_')}_BWS_ACCESS_TOKEN"


@BY_LANE
def test_the_lane_s_token_row_is_absent(lane: str) -> None:
    assert f"{lane}-bws-token" not in {c["id"] for c in _credentials()}


@BY_LANE
def test_no_row_names_the_lane_s_token_variable(lane: str) -> None:
    keys = {consumer.get("key") for c in _credentials() for consumer in c.get("consumer", [])}
    assert _variable(lane) not in keys


@BY_LANE
def test_the_lane_s_compose_maps_no_token(lane: str) -> None:
    compose = (ROOT / "lanes" / lane / "compose.yaml").read_text()
    assert f"${{{_variable(lane)}}}" not in compose
