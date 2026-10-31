"""First sightings and the heartbeat: the two additions the rotation signals lean on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from alert_hub_client import AlertHubError, FirstSightings, StateUnwritable

from .test_client import Hub

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def test_a_key_keeps_its_first_sighting(tmp_path: Path) -> None:
    sightings = FirstSightings(tmp_path / "s.json")
    assert sightings.update({"a"}, T0) == {"a": T0}
    later = T0 + timedelta(days=3)
    assert FirstSightings(tmp_path / "s.json").update({"a", "b"}, later) == {"a": T0, "b": later}


def test_a_key_gone_from_current_is_forgotten_so_its_clock_restarts(tmp_path: Path) -> None:
    sightings = FirstSightings(tmp_path / "s.json")
    sightings.update({"a"}, T0)
    sightings.update(set(), T0 + timedelta(days=1))
    back = T0 + timedelta(days=2)
    assert sightings.update({"a"}, back) == {"a": back}


def test_a_kept_key_survives_a_pass_that_did_not_read_it(tmp_path: Path) -> None:
    sightings = FirstSightings(tmp_path / "s.json")
    sightings.update({"a"}, T0)
    later = T0 + timedelta(days=5)
    assert sightings.update(set(), later, keep={"a"}) == {}
    assert sightings.update({"a"}, later) == {"a": T0}


def test_corrupt_state_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert FirstSightings(path).update({"a"}, T0) == {"a": T0}
    path.write_text(json.dumps({"a": "not a date", "b": 3}))
    assert FirstSightings(path).update({"a", "b"}, T0) == {"a": T0, "b": T0}


def test_unwritable_state_raises_state_unwritable(tmp_path: Path) -> None:
    with pytest.raises(StateUnwritable):
        FirstSightings(tmp_path / "missing" / "s.json").update({"a"}, T0)


def test_heartbeat_posts_resolved_and_writes_no_state(tmp_path: Path) -> None:
    hub = Hub()
    state = tmp_path / "s.json"
    hub.alerts(state).heartbeat("rotation:alive", "Rotation lane alive")
    assert [(m["key"], m["status"], m["title"]) for m in hub.sent] == [
        ("rotation:alive", "resolved", "Rotation lane alive")
    ]
    assert not state.exists()


def test_heartbeat_failure_raises_alert_hub_error(tmp_path: Path) -> None:
    hub = Hub(httpx.Response(500))
    with pytest.raises(AlertHubError):
        hub.alerts(tmp_path / "s.json").heartbeat("k", "t")
