"""The pass: seeding, the known set, unreadable passes, and the client's one permitted read."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from execution_watch import cli
from execution_watch.client import CallError, Orchestrator, parse_row, permits
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


def proposal_row(proposal: str = OTHER, **changes: Any) -> dict[str, Any]:
    """A row that isn't a unit, as the new route serves it."""
    row: dict[str, Any] = {
        "subject_kind": "proposal",
        "subject_id": proposal,
        "unit_id": None,
        "state": "proposed",
        "change_class": None,
        "is_rotation": False,
        "standing": None,
        "target_repository": None,
        "granted": None,
        "latest_dispatch_status": None,
        "latest_dispatch_at": None,
        "has_claim": None,
        "lease_lapsed": None,
        "breaker_open": None,
        "entered_at": NOW.isoformat(),
        "waiting": True,
        "overdue": False,
        "policy_not_applied": None,
        "proposed_by": "devon",
        "rotation_package": False,
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


def test_a_row_from_a_server_without_the_new_fields_is_a_unit_row() -> None:
    parsed = parse_row(status_row())
    assert parsed is not None
    assert (parsed.subject_kind, parsed.subject_id) == ("unit", UNIT)
    assert (parsed.waiting, parsed.overdue, parsed.policy_not_applied) == (False, False, None)


def test_a_row_that_is_not_a_unit_parses_with_empty_unit_fields() -> None:
    parsed = parse_row(proposal_row(overdue=True, policy_not_applied="attempts_over_budget"))
    assert parsed is not None
    assert parsed.unit_id is None
    assert parsed.subject_id == OTHER
    assert parsed.entered_at == NOW
    assert parsed.overdue is True
    assert parsed.proposed_by == "devon"
    assert parsed.policy_not_applied == "attempts_over_budget"
    assert (parsed.has_claim, parsed.lease_lapsed, parsed.breaker_open) == (False, False, False)


def test_a_kind_this_lane_does_not_know_is_skipped_not_unread(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row(subject_kind="future-thing"), status_row()]
    assert world.submit(tmp_path) == 0
    assert world.sent == [(FAILED_KEY, "firing")]


@pytest.mark.parametrize(
    "bad",
    [
        {"subject_id": None},
        {"state": None},
        {"waiting": "yes"},
        {"overdue": "yes"},
        {"entered_at": "not a time"},
        {"policy_not_applied": 3},
        {"is_rotation": "yes"},
    ],
)
def test_a_known_kind_with_a_bad_field_is_still_unreadable(
    world: World, tmp_path: Path, bad: dict[str, Any]
) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row(**bad)]
    assert world.submit(tmp_path) == 2


def test_a_unit_row_still_requires_its_unit_fields(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row(unit_id=None)]
    assert world.submit(tmp_path) == 2
    world.rows = [status_row(has_claim=None)]
    assert world.submit(tmp_path) == 2


def _known(state: Path) -> set[str]:
    return set(json.loads((state / "known.json").read_text()))


WAITING_KEY = f"admission-waiting:proposal:{OTHER}"
NOT_APPLIED_KEY = f"admission-policy-not-applied:{OTHER}"


def test_while_absorbing_a_pass_learns_every_admission_key_and_sends_none(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row(overdue=True, policy_not_applied="attempts_over_budget")]
    assert world.submit(tmp_path) == 0
    assert world.sent == []
    assert _known(tmp_path) == {WAITING_KEY, NOT_APPLIED_KEY}
    assert "2 raised, 0 firing" in capsys.readouterr().out


def test_absorbing_touches_no_execution_key(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [status_row(), proposal_row()]
    assert world.submit(tmp_path) == 0
    assert world.sent == [(FAILED_KEY, "firing")]
    assert _known(tmp_path) == {WAITING_KEY}


def test_an_absorbed_key_is_dropped_when_its_subject_goes(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row()]
    world.submit(tmp_path)
    world.rows = []
    world.submit(tmp_path, NOW + timedelta(minutes=15))
    assert _known(tmp_path) == set()


@pytest.fixture
def not_absorbing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ABSORB_ADMISSION", False)


@pytest.mark.usefixtures("not_absorbing")
def test_a_seeded_young_subject_stays_silent_once_overdue(world: World, tmp_path: Path) -> None:
    world.rows = [proposal_row()]
    cli.seed(rows_of(world), tmp_path, NOW)
    assert _known(tmp_path) == {WAITING_KEY}
    assert world.submit(tmp_path) == 0
    world.rows = [proposal_row(overdue=True)]
    assert world.submit(tmp_path, NOW + timedelta(days=8)) == 0
    assert world.sent == []


@pytest.mark.usefixtures("not_absorbing")
def test_a_new_subject_alerts_when_overdue(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row()]
    assert world.submit(tmp_path) == 0
    assert world.sent == []
    world.rows = [proposal_row(overdue=True)]
    assert world.submit(tmp_path, NOW + timedelta(days=8)) == 0
    assert world.sent == [(WAITING_KEY, "firing")]


@pytest.mark.usefixtures("not_absorbing")
def test_a_decided_proposal_resolves_on_the_next_pass(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row(overdue=True, policy_not_applied="attempts_over_budget")]
    world.submit(tmp_path)
    assert {key for key, _ in world.sent} == {WAITING_KEY, NOT_APPLIED_KEY}
    world.sent.clear()
    world.rows = []
    world.submit(tmp_path, NOW + timedelta(minutes=15))
    assert sorted(world.sent) == sorted([(WAITING_KEY, "resolved"), (NOT_APPLIED_KEY, "resolved")])


@pytest.mark.usefixtures("not_absorbing")
def test_a_policy_alert_fires_once_across_passes(world: World, tmp_path: Path) -> None:
    (tmp_path / "known.json").write_text("[]")
    world.rows = [proposal_row(policy_not_applied="attempts_over_budget")]
    world.submit(tmp_path)
    world.submit(tmp_path, NOW + timedelta(minutes=15))
    assert world.sent == [(NOT_APPLIED_KEY, "firing")]


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
