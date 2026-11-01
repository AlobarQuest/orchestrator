"""The pass: seeding, the known set, unreadable passes, and the client's one permitted read."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from execution_watch import cli
from execution_watch.client import CallError, Orchestrator, permits
from execution_watch.decide import Condition, key_for

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
UNIT = "0c7a484c-1111-4111-8111-111111111111"
OTHER = "0c7a484c-2222-4222-8222-222222222222"
FAILED_KEY = key_for(Condition.UNIT_FAILED, UNIT)
UNREAD = Condition.WATCH_UNREAD.value


def status_row(unit: str = UNIT, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "unit_id": unit,
        "state": "failed",
        "change_class": "app-change",
        "is_rotation": False,
        "standing": False,
        "target_repository": "AlobarQuest/orchestrator",
        "granted": True,
        "latest_dispatch_status": None,
        "latest_dispatch_at": None,
        "has_claim": False,
        "lease_lapsed": False,
        "breaker_open": False,
    }
    return row | changes


class World:
    """The orchestrator's answer and alert-hub's inbox, both switchable per pass."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] | None = []
        self.sent: list[tuple[str, str]] = []

    def orchestrator(self) -> Orchestrator:
        def answer(request: httpx.Request) -> httpx.Response:
            if self.rows is None:
                return httpx.Response(503)
            return httpx.Response(200, json=self.rows)

        return Orchestrator(
            base_url="https://sds.example.invalid",
            credential_key_id="orchestrator-observer",
            token="t",
            transport=httpx.MockTransport(answer),
        )

    def hub(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append((body["key"], body["status"]))
        return httpx.Response(202, json={})

    def submit(self, state: Path, now: datetime = NOW) -> int:
        return cli.submit(
            self.orchestrator(),
            state,
            token="t",
            now=now,
            hub_transport=httpx.MockTransport(self.hub),
        )


@pytest.fixture
def world() -> World:
    return World()


def rows_of(world: World) -> list[Any]:
    return world.orchestrator().execution_status()


def test_seed_writes_known_subjects_and_sends_nothing(world: World, tmp_path: Path) -> None:
    world.rows = [status_row()]
    assert cli.seed(rows_of(world), tmp_path, NOW) == 0
    assert json.loads((tmp_path / "known.json").read_text()) == [FAILED_KEY]
    assert world.sent == []
    assert not (tmp_path / "alerts.json").exists()


def test_seed_never_overwrites(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row()]
    assert cli.seed(rows_of(world), tmp_path, NOW) == 1
    assert (tmp_path / "known.json").read_text() == "[]"


def test_submit_without_known_refuses_and_raises_unread(world: World, tmp_path: Path) -> None:
    world.rows = [status_row()]
    assert world.submit(tmp_path) == 1
    assert world.sent == [(UNREAD, "firing")]
    assert not (tmp_path / "known.json").exists()


def test_submit_with_no_known_file_holds_every_remembered_key(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row()]
    world.submit(tmp_path)
    (tmp_path / "known.json").unlink()
    world.rows = []
    world.sent.clear()
    assert world.submit(tmp_path, NOW + timedelta(minutes=15)) == 1
    assert world.sent == [(UNREAD, "firing")]


def test_the_first_seeded_pass_alerts_on_nothing(world: World, tmp_path: Path) -> None:
    world.rows = [status_row()]
    cli.seed(rows_of(world), tmp_path, NOW)
    assert world.submit(tmp_path) == 0
    assert world.sent == []


def test_a_new_subject_alerts_once(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row()]
    assert world.submit(tmp_path) == 0
    assert world.submit(tmp_path, NOW + timedelta(minutes=15)) == 0
    assert world.sent == [(FAILED_KEY, "firing")]


def test_an_unreadable_pass_holds_every_key(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row()]
    world.submit(tmp_path)
    world.sent.clear()
    world.rows = None
    assert world.submit(tmp_path, NOW + timedelta(minutes=15)) == 2
    assert world.sent == []
    first_seen = json.loads((tmp_path / "first-seen.json").read_text())
    assert FAILED_KEY in first_seen
    world.rows = [status_row()]
    assert world.submit(tmp_path, NOW + timedelta(minutes=30)) == 0
    assert world.sent == []


def test_unread_alerts_after_two_hours_and_resolves(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = None
    assert world.submit(tmp_path) == 2
    assert world.submit(tmp_path, NOW + timedelta(hours=1, minutes=59)) == 2
    assert world.sent == []
    assert world.submit(tmp_path, NOW + timedelta(hours=2)) == 2
    assert world.sent == [(UNREAD, "firing")]
    world.rows = []
    assert world.submit(tmp_path, NOW + timedelta(hours=2, minutes=15)) == 0
    assert world.sent == [(UNREAD, "firing"), (UNREAD, "resolved")]


def test_a_subject_gone_after_downtime_resolves(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row()]
    world.submit(tmp_path)
    world.rows = []
    assert world.submit(tmp_path, NOW + timedelta(days=2)) == 0
    assert world.sent == [(FAILED_KEY, "firing"), (FAILED_KEY, "resolved")]


def test_a_known_subject_that_clears_then_returns_alerts(world: World, tmp_path: Path) -> None:
    world.rows = [status_row()]
    cli.seed(rows_of(world), tmp_path, NOW)
    world.rows = []
    assert world.submit(tmp_path) == 0
    assert json.loads((tmp_path / "known.json").read_text()) == []
    world.rows = [status_row()]
    assert world.submit(tmp_path, NOW + timedelta(minutes=15)) == 0
    assert world.sent == [(FAILED_KEY, "firing")]


def test_the_dispatch_wait_runs_on_first_sightings(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    blocked = status_row(
        state="ready", latest_dispatch_status="blocked", latest_dispatch_at=NOW.isoformat()
    )
    key = key_for(Condition.DISPATCH_BLOCKED, UNIT)
    world.rows = [blocked]
    world.submit(tmp_path)
    assert world.sent == []
    world.submit(tmp_path, NOW + timedelta(hours=1))
    assert world.sent == [(key, "firing")]


def test_a_bad_row_is_an_unreadable_pass(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row(is_rotation="yes")]
    assert world.submit(tmp_path) == 2


def test_the_client_permits_only_execution_status() -> None:
    assert permits("GET", "/api/v1/execution-status")
    for method, path in [
        ("POST", "/api/v1/execution-status"),
        ("GET", "/api/v1/execution-status/"),
        ("GET", "/api/v1/execution-status/x"),
        ("GET", "/api/v1/execution-status\n"),
        ("GET", "/api/v1/status-ledger"),
        ("DELETE", "/api/v1/execution-status"),
    ]:
        assert not permits(method, path)


def test_the_client_refuses_other_paths_without_sending(world: World) -> None:
    client = world.orchestrator()
    with pytest.raises(CallError):
        client._client.request("POST", "/api/v1/observations")


def test_bare_prints_and_writes_nothing(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world.rows = [status_row(), status_row(OTHER)]
    (tmp_path / "known.json").write_text(json.dumps([FAILED_KEY]))
    before = sorted(p.name for p in tmp_path.iterdir())
    assert cli.dry_run(rows_of(world), tmp_path, NOW) == 0
    out = capsys.readouterr().out
    assert f"known  {FAILED_KEY}" in out
    assert "new" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before
