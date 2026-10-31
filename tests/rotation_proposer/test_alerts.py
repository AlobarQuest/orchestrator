"""Keyed alerts: a finding fires once, an approval wait fires after 48 hours, both resolve."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from rotation_proposer.alerts import (
    APPROVAL_STATUSES,
    STATE_DIR_ENV,
    approval_key,
    finding_key,
    sync_alerts,
)
from rotation_proposer.outcome import FINDING_STATUSES, Outcome

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
CREDENTIAL = "openrouter-generic"


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


def _sync(hub: Hub, tmp_path: Path, outcomes: list[Outcome], now: datetime = NOW) -> str | None:
    return sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path,
        now=now,
        transport=httpx.MockTransport(hub),
    )


def _sent(hub: Hub) -> list[tuple[str, str]]:
    return [(row["key"], row["status"]) for row in hub.sent]


@pytest.mark.parametrize("status", sorted(FINDING_STATUSES))
def test_each_finding_status_raises_one_keyed_alert(hub: Hub, tmp_path: Path, status: str) -> None:
    outcome = Outcome(CREDENTIAL, status, "detail")
    _sync(hub, tmp_path, [outcome, outcome])
    assert _sent(hub) == [(finding_key(CREDENTIAL, status), "firing")]
    assert hub.sent[0]["message"] == "detail"


def test_a_finding_that_stops_resolves(hub: Hub, tmp_path: Path) -> None:
    key = finding_key(CREDENTIAL, "stacked")
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")])
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")])
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "revised", "x")])
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_a_status_that_is_not_a_finding_raises_nothing(hub: Hub, tmp_path: Path) -> None:
    for status in ("revised", "proposed", "replayed", "recorded", "legacy", "would-advance"):
        _sync(hub, tmp_path, [Outcome(CREDENTIAL, status, "x")])
    assert hub.sent == []


@pytest.mark.parametrize("status", ["awaiting-approval", "declined"])
def test_an_approval_wait_fires_at_48_hours(hub: Hub, tmp_path: Path, status: str) -> None:
    key = approval_key(CREDENTIAL)
    outcome = Outcome(CREDENTIAL, status, "needs a name")
    _sync(hub, tmp_path, [outcome])
    _sync(hub, tmp_path, [outcome], NOW + timedelta(hours=47, minutes=59))
    assert hub.sent == []
    _sync(hub, tmp_path, [outcome], NOW + timedelta(hours=48))
    _sync(hub, tmp_path, [outcome], NOW + timedelta(hours=60))
    assert _sent(hub) == [(key, "firing")]
    assert "48 hours" in hub.sent[0]["message"]


def test_awaiting_approval_waits_48_hours(hub: Hub, tmp_path: Path) -> None:
    test_an_approval_wait_fires_at_48_hours(hub, tmp_path, "awaiting-approval")


def test_declined_waits_48_hours(hub: Hub, tmp_path: Path) -> None:
    test_an_approval_wait_fires_at_48_hours(hub, tmp_path, "declined")


def test_an_approval_wait_that_ends_resolves_and_restarts_its_clock(
    hub: Hub, tmp_path: Path
) -> None:
    key = approval_key(CREDENTIAL)
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [waiting], NOW + timedelta(hours=49))
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "proposed", "x")], NOW + timedelta(hours=50))
    _sync(hub, tmp_path, [waiting], NOW + timedelta(hours=51))
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_the_first_sighting_survives_an_alert_hub_outage(hub: Hub, tmp_path: Path) -> None:
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    hub.status = 500
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [waiting], NOW + timedelta(hours=49))
    hub.status = 202
    hub.sent.clear()
    _sync(hub, tmp_path, [waiting], NOW + timedelta(hours=50))
    assert _sent(hub) == [(approval_key(CREDENTIAL), "firing")]


def test_a_hub_that_refuses_is_named(hub: Hub, tmp_path: Path) -> None:
    hub.status = 500
    assert _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")]) is not None


def test_an_unusable_state_directory_is_named(hub: Hub, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    reason = sync_alerts(
        [], token="t", state_dir=blocker / "sub", now=NOW, transport=httpx.MockTransport(hub)
    )
    assert reason is not None
    assert hub.sent == []


def test_no_key_is_a_substring_of_another_finding_kind() -> None:
    kinds = sorted(FINDING_STATUSES | APPROVAL_STATUSES)
    assert [(a, b) for a in kinds for b in kinds if a != b and a in b] == []


def test_the_state_directory_variable_is_the_proposers_own() -> None:
    assert STATE_DIR_ENV == "ROTATION_PROPOSER_STATE_DIR"
