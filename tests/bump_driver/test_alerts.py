"""Keyed alerts: each fires once, resolves when its subject clears, and is held while unread."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bump_driver.alerts import sync_alerts
from bump_driver.cli import (
    Outcome,
    act_refused_key,
    bump_failed_key,
    decompose_refused_key,
    run_unclaimed_key,
)

REPOSITORY = "AlobarQuest/orchestrator"
UNIT = "0c7a484c-1111-4111-8111-111111111111"
REVISION = "f962475a-1964-46df-81ac-2b3029dec2a7"
FAILED = Outcome(
    "unit", "failed", "unit failed", repository=REPOSITORY, key=bump_failed_key(REPOSITORY, UNIT)
)
REFUSED = Outcome(
    "rev",
    "refused",
    "no pin site",
    repository=REPOSITORY,
    key=decompose_refused_key(REPOSITORY, REVISION),
)


class Hub:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.status = 202

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        return httpx.Response(self.status, json={})


@pytest.fixture
def hub() -> Hub:
    return Hub()


def _sync(hub: Hub, tmp_path: Path, outcomes: list[Outcome]) -> str | None:
    return sync_alerts(outcomes, token="t", state_dir=tmp_path, transport=httpx.MockTransport(hub))


def _sent(hub: Hub) -> list[tuple[str, str]]:
    return [(row["key"], row["status"]) for row in hub.sent]


def test_the_keys_are_the_ones_the_brief_names() -> None:
    assert bump_failed_key(REPOSITORY, UNIT) == f"bump-unit-failed:alobarquest/orchestrator:{UNIT}"
    assert decompose_refused_key(REPOSITORY, REVISION) == (
        f"bump-decompose-refused:alobarquest/orchestrator:{REVISION}"
    )


def test_a_failed_unit_alerts_once_and_resolves_when_it_clears(hub: Hub, tmp_path: Path) -> None:
    assert FAILED.key is not None
    _sync(hub, tmp_path, [FAILED])
    _sync(hub, tmp_path, [FAILED])
    assert _sent(hub) == [(FAILED.key, "firing")]
    _sync(hub, tmp_path, [])
    assert _sent(hub) == [(FAILED.key, "firing"), (FAILED.key, "resolved")]


def test_a_refused_revision_alerts_and_resolves_when_it_clears(hub: Hub, tmp_path: Path) -> None:
    assert REFUSED.key is not None
    _sync(hub, tmp_path, [REFUSED])
    _sync(hub, tmp_path, [Outcome("rev", "approved", repository=REPOSITORY)])
    assert _sent(hub) == [(REFUSED.key, "firing"), (REFUSED.key, "resolved")]


def test_the_new_keys_are_the_ones_the_ruling_names() -> None:
    assert act_refused_key(REPOSITORY, UNIT) == f"bump-act-refused:alobarquest/orchestrator:{UNIT}"
    assert run_unclaimed_key(REPOSITORY, UNIT) == (
        f"bump-run-unclaimed:alobarquest/orchestrator:{UNIT}"
    )


@pytest.mark.parametrize(
    "key", [act_refused_key(REPOSITORY, UNIT), run_unclaimed_key(REPOSITORY, UNIT)]
)
def test_every_key_is_held_while_its_repository_is_unread(
    hub: Hub, tmp_path: Path, key: str
) -> None:
    outcome = Outcome("unit", "refused", "no", repository=REPOSITORY, key=key)
    _sync(hub, tmp_path, [outcome])
    _sync(hub, tmp_path, [Outcome("record", "unread", "503", repository=REPOSITORY)])
    assert _sent(hub) == [(key, "firing")]
    _sync(hub, tmp_path, [])
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_an_unread_repository_holds_its_alerts(hub: Hub, tmp_path: Path) -> None:
    assert FAILED.key is not None
    _sync(hub, tmp_path, [FAILED])
    _sync(hub, tmp_path, [Outcome("record", "unread", "503", repository=REPOSITORY)])
    assert _sent(hub) == [(FAILED.key, "firing")]


def test_an_unread_repository_holds_only_its_own_alerts(hub: Hub, tmp_path: Path) -> None:
    assert FAILED.key is not None
    _sync(hub, tmp_path, [FAILED])
    _sync(hub, tmp_path, [Outcome("record", "unread", "503", repository="AlobarQuest/brain")])
    assert _sent(hub) == [(FAILED.key, "firing"), (FAILED.key, "resolved")]


def test_an_unread_with_no_repository_holds_everything(hub: Hub, tmp_path: Path) -> None:
    _sync(hub, tmp_path, [FAILED])
    _sync(hub, tmp_path, [Outcome("record", "unread", "503")])
    assert len(hub.sent) == 1


def test_a_hub_that_refuses_is_named(hub: Hub, tmp_path: Path) -> None:
    hub.status = 500
    assert _sync(hub, tmp_path, [FAILED]) is not None
