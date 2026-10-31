"""Every unit a person should hear about raises one alert, from the instance that owns it, and an
alert or its clock never fails a unit or a pass (rotation Part 1)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from alert_hub_client import alert_key
from rotation_worker import worker
from rotation_worker.notify import (
    unit_failed_key,
    unit_review_key,
    unit_stuck_key,
    unit_waiting_key,
)
from rotation_worker.orchestrator_client import OrchestratorError
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_keychain_instance import Recording
from tests.rotation_worker.test_notify import Hub

UNIT = "11111111-2222-3333-4444-555555555555"
START = datetime(2026, 10, 1, tzinfo=UTC)


class Estate:
    """The ledger, the unit reads and the package reads, as a pass asks them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.units: dict[str, dict[str, Any]] = {}
        self.ready: list[str] = []
        self.unreachable: set[str] = set()
        self.unit_reads: list[str] = []
        self.ledger_down = False

    def add(self, state: str, adapter: str | None = "github-pat", **row: Any) -> None:
        constraints = {"rotation_adapter": adapter} if adapter else {}
        self.units[UNIT] = {
            "id": UNIT,
            "version": 3,
            "work_package_revision_id": "revision",
            "authority": {"constraints": constraints},
        }
        self.rows = [
            {
                "unit_id": UNIT,
                "unit_key": "pat-mint",
                "unit_title": "pat: mint",
                "unit_state": state,
                "blockers": [],
                **row,
            }
        ]

    def active_units(self) -> list[dict[str, Any]]:
        if self.ledger_down:
            raise OrchestratorError("the ledger didn't answer")
        return self.rows

    def ready_units(self) -> list[str]:
        return self.ready

    def unit(self, unit_id: str) -> dict[str, Any]:
        self.unit_reads.append(unit_id)
        if unit_id in self.unreachable:
            raise OrchestratorError("no answer")
        return self.units[unit_id]

    def package_intake(self, revision_id: str) -> dict[str, Any]:
        return {"id": revision_id, "package_id": "rotation-github-pat"}

    def claim(self, *_arguments: Any) -> Any:
        raise OrchestratorError("the window is shut")


class Pass:
    """Passes run against one state directory, on a clock the test moves."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, store: object | None = None
    ) -> None:
        self.estate = Estate()
        self.hub = Hub()
        self.state = tmp_path
        self.now = START
        self.notifier = self.hub.notifier(tmp_path)
        self.syncs = 0
        self.fetch_fails = False
        self.store = store if store is not None else MemoryStore()
        monkeypatch.setattr(worker, "_now", lambda: self.now)
        # The behind-main alert has its own tests (`test_behind_main.py`).
        monkeypatch.setattr(worker, "behind", lambda *_a: ({}, set()))
        monkeypatch.setattr(
            worker, "approved_package", lambda *_a: {"profile_fields": {"occurrence": "o-7"}}
        )

    def run(self, hours: float = 0) -> list[tuple[str, str]]:
        """A pass `hours` after the start; the alerts it sent, as (key, status)."""
        self.now = START + timedelta(hours=hours)
        before = len(self.hub.sent)
        setup = SimpleNamespace(
            client=self.estate,
            store=self.store,
            notifier=self.notifier,
            state_dir=self.state,
            mirror=SimpleNamespace(sync=self._sync),
            work_project_id="work",
        )
        worker.run_pass(cast(worker.Setup, setup))
        return [(m["key"], m["status"]) for m in self.hub.sent[before:]]

    def _sync(self, _repository: str) -> str:
        self.syncs += 1
        if self.fetch_fails:
            raise worker.MirrorError("git didn't answer")
        return "main-commit"

    def message(self, key: str) -> dict[str, Any]:
        return next(m for m in reversed(self.hub.sent) if m["key"] == key)

    def clock(self) -> dict[str, str]:
        return json.loads((self.state / "wait-since.json").read_text())


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Pass:
    return Pass(monkeypatch, tmp_path)


def _waits_in(monkeypatch: pytest.MonkeyPatch, wait: Callable[[worker.PassActs], None]) -> None:
    """A `_take` that gets past `_ours` and stops on `wait`."""

    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        wait(found)
        return 0

    monkeypatch.setattr(worker, "_take", take)


def test_a_failed_owned_unit_raises_one_alert_naming_the_reason(run: Pass) -> None:
    long = "x" * 600
    run.estate.add("failed", last_failure={"reason": f"the host refused {long}"})
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    sent = run.message(unit_failed_key(UNIT))
    assert "github-pat" in sent["message"]
    assert "rotation/github-pat/o-7/staging" in sent["message"]
    assert "the host refused" in sent["message"]
    assert "/work-units/" not in sent["message"] and "requeue" in sent["message"]
    assert "If a rotation stalls after host" in sent["message"]
    assert len(sent["message"]) < 700
    assert sent["link"] == f"https://sds.alobar.net/review/units/{UNIT}"


def test_a_revision_required_unit_leaves_the_decision_to_a_person(run: Pass) -> None:
    run.estate.add("revision_required", last_failure={"reason": "an earlier attempt's failure"})
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    sent = run.message(unit_failed_key(UNIT))
    assert "/review" in sent["message"] and "decides" in sent["message"]
    assert "github-pat" in sent["message"]
    assert "requeue" not in sent["message"]
    assert "earlier attempt" not in sent["message"]
    assert "failed" not in sent["title"]


def test_a_blocked_unit_alerts_with_the_requeue_step_and_no_stale_failure(run: Pass) -> None:
    run.estate.add("blocked", last_failure={"reason": "an earlier attempt's failure"})
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    sent = run.message(unit_failed_key(UNIT))
    assert "blocked" in sent["message"]
    assert "requeue" in sent["message"]
    assert "earlier attempt" not in sent["message"]
    assert "failed" not in sent["title"]


def test_a_blocked_foreign_unit_raises_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = Pass(monkeypatch, tmp_path, Recording())
    run.estate.add("blocked")
    assert run.run() == []


def test_a_failed_foreign_unit_raises_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = Pass(monkeypatch, tmp_path, Recording())
    run.estate.add("failed", last_failure={"reason": "no"})
    assert run.run() == []


def test_an_unknown_adapter_s_failure_is_the_vm_s(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = Pass(monkeypatch, tmp_path)
    run.estate.add("failed", adapter="brand-new-adapter")
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    keychain = Pass(monkeypatch, tmp_path / "k", Recording())
    (tmp_path / "k").mkdir()
    keychain.estate.add("failed", adapter="brand-new-adapter")
    assert keychain.run() == []


def test_a_package_that_can_t_be_read_doesn_t_silence_a_failure(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_arguments: Any) -> None:
        raise worker.ContractError("the commit isn't on main")

    monkeypatch.setattr(worker, "approved_package", refused)
    run.estate.add("failed", last_failure={"reason": "no"})
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    assert "the commit isn't on main" in run.message(unit_failed_key(UNIT))["message"]


def test_a_requeued_unit_resolves_its_failed_alert(run: Pass) -> None:
    run.estate.add("failed")
    run.run()
    run.estate.add("ready")
    assert run.run() == [(unit_failed_key(UNIT), "resolved")]


def test_a_bump_unit_in_revision_required_raises_nothing(run: Pass) -> None:
    run.estate.add("revision_required", adapter=None)
    assert run.run() == []


def test_awaiting_review_alerts_at_48_hours_and_not_before(run: Pass) -> None:
    run.estate.add("awaiting_review", last_event_at=START.isoformat())
    assert run.run(47.9) == []
    assert run.run(48) == [(unit_review_key(UNIT), "firing")]


def test_a_row_with_nothing_to_say_is_not_read(run: Pass) -> None:
    run.estate.add("awaiting_review", last_event_at=START.isoformat())
    run.run(1)
    run.estate.add("in_progress")
    run.run(2)
    assert run.estate.unit_reads == []


def test_a_wait_alerts_at_48_hours_and_not_at_31(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a shut window")}))
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(31) == []
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]
    assert "a shut window" in run.message(unit_waiting_key(UNIT))["message"]


def _at_retire(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real `_take` as far as the readers a retire waits for."""
    approved = SimpleNamespace(reads={"r": object()}, rotation=SimpleNamespace(keeper_id="k"))
    reads = SimpleNamespace(pending=lambda _at: ["weekly-reader"])
    monkeypatch.setattr(worker, "check_unit", lambda *_a: approved)
    monkeypatch.setattr(worker, "_grant_waits", lambda *_a: False)
    monkeypatch.setattr(worker, "_held", lambda *_a: None)
    monkeypatch.setattr(worker, "_verifies", lambda _a: True)
    monkeypatch.setattr(worker, "_gates_retire", lambda _a: True)
    monkeypatch.setattr(worker, "_consumer_reads", lambda *_a: reads)
    monkeypatch.setattr(worker, "_overlap_until", lambda *_a: "2026-11-05")


def test_a_reader_wait_alerts_at_192_hours_and_not_at_168(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _at_retire(monkeypatch)
    run.store = SimpleNamespace(read_back_kind="bws-secret", revised=lambda _k: START)
    run.estate.add("ready")
    run.estate.ready = [UNIT]
    run.estate.rows = []
    assert run.run(0) == []
    assert run.run(168) == []
    assert run.run(192) == [(unit_waiting_key(UNIT), "firing")]
    message = run.message(unit_waiting_key(UNIT))["message"]
    assert "weekly-reader" in message
    assert "2026-11-05" in message


@pytest.mark.parametrize("busy", [["busy-reader"], None])
def test_a_reader_mid_run_waits_on_the_ordinary_clock(
    run: Pass, monkeypatch: pytest.MonkeyPatch, busy: list[str] | None
) -> None:
    class Busy:
        def readers_mid_run(self) -> list[str]:
            if busy is None:
                raise worker.ReadUnavailable("the listing didn't answer")
            return busy

    approved = SimpleNamespace(reads={})
    monkeypatch.setattr(worker, "check_unit", lambda *_a: approved)
    monkeypatch.setattr(worker, "_grant_waits", lambda *_a: False)
    monkeypatch.setattr(worker, "_held", lambda *_a: None)
    monkeypatch.setattr(worker, "_provider", lambda *_a: Busy())
    monkeypatch.setattr(worker, "_writes", lambda _a: True)
    for gate in ("_mint_waits", "_adapter_waits", "_revoke_waits", "_overlap_waits"):
        monkeypatch.setattr(worker, gate, lambda *_a: False)
    monkeypatch.setattr(worker, "_restart_waits", lambda *_a: False)
    run.estate.add("ready")
    run.estate.ready = [UNIT]
    run.estate.rows = []
    assert run.run(0) == []
    assert run.run(47) == []
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]
    assert "Readers mid-run" in run.message(unit_waiting_key(UNIT))["message"]


def test_an_adapter_wait_records_itself() -> None:
    found = worker.PassActs()
    setup = cast(worker.Setup, SimpleNamespace(client=SimpleNamespace(history=lambda _unit: [])))
    adapter = SimpleNamespace(wait=lambda *_a: "the old value's sessions")
    approved = cast(worker.Approved, SimpleNamespace(adapter=adapter, unit=None))
    assert worker._adapter_waits(setup, approved, UNIT, found)
    assert found.waits == {UNIT: worker.Wait("the old value's sessions")}


def test_an_overlap_wait_records_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "mid_overlap", lambda *_a: ["other (ready)"])
    found = worker.PassActs()
    adapter = SimpleNamespace(overlap_waits={"mint"})
    approved = SimpleNamespace(adapter=adapter, unit=SimpleNamespace(name="mint"))
    unit = {"id": UNIT, "work_package_revision_id": "r"}
    setup = cast(worker.Setup, SimpleNamespace(client=None))
    assert worker._overlap_waits(setup, cast(worker.Approved, approved), unit, found)
    assert "other (ready)" in found.waits[UNIT].reason
    assert found.waits[UNIT].hours == 192


def test_a_restart_wait_records_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "stranded_by_a_restart", lambda _c: ["a run"])
    found = worker.PassActs()
    step = SimpleNamespace(kind=worker.StepKind.DISTRIBUTE)
    approved = SimpleNamespace(unit=SimpleNamespace(steps=[step]))
    setup = cast(worker.Setup, SimpleNamespace(client=None))
    held = cast(Any, object())
    assert worker._restart_waits(setup, cast(worker.Approved, approved), held, UNIT, found)
    assert "a run" in found.waits[UNIT].reason
    assert found.waits[UNIT].hours == 48


def _reader_wait_until(run: Pass, monkeypatch: pytest.MonkeyPatch, hours: int) -> None:
    """Readers hold the unit for `hours`; after that, each pass is skipped on a shut window."""

    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        if run.now < START + timedelta(hours=hours):
            found.waits[unit_id] = worker.Wait("weekly reader", reader=True)
            return 0
        raise OrchestratorError("the window is shut")

    monkeypatch.setattr(worker, "_take", take)
    run.estate.ready = [UNIT]


def test_a_window_skip_inside_a_reader_wait_raises_nothing(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reader_wait_until(run, monkeypatch, 168)
    assert run.run(0) == []
    assert run.run(100) == []
    assert run.run(168) == []


def test_an_ordinary_wait_after_a_reader_wait_runs_its_own_48_hours(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reader_wait_until(run, monkeypatch, 168)
    run.run(0)
    run.run(168)
    assert run.run(168 + 47) == []
    assert run.run(168 + 49) == [(unit_waiting_key(UNIT), "firing")]


def test_a_skip_inside_a_reader_wait_keeps_the_reader_clock(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        if run.now == START + timedelta(hours=100):
            raise OrchestratorError("the window is shut")
        found.waits[unit_id] = worker.Wait("weekly reader", reader=True)
        return 0

    monkeypatch.setattr(worker, "_take", take)
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(100)
    assert run.run(192) == [(unit_waiting_key(UNIT), "firing")]


def test_an_unread_pass_keeps_the_reader_clock(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        if run.now == START + timedelta(hours=100):
            raise OrchestratorError("the unit didn't answer")
        found.seen.add(unit_id)
        found.waits[unit_id] = worker.Wait("weekly reader", reader=True)
        return 0

    monkeypatch.setattr(worker, "_take", take)
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(100)
    assert run.run(192) == [(unit_waiting_key(UNIT), "firing")]


ORDINARY = worker.Wait("a shut window")
READER = worker.Wait("weekly reader", reader=True)
STUCK = worker.Wait("nobody foresaw this", stuck=True)


def _waits_by_hour(
    run: Pass, monkeypatch: pytest.MonkeyPatch, plan: dict[float, worker.Wait]
) -> None:
    """Each pass records the wait `plan` names for its hour."""

    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        found.waits[unit_id] = plan[(run.now - START) / timedelta(hours=1)]
        return 0

    monkeypatch.setattr(worker, "_take", take)
    run.estate.ready = [UNIT]


def test_an_ordinary_clock_survives_a_pass_of_reader_wait(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 47: ORDINARY, 48: READER, 49: ORDINARY})
    assert run.run(0) == []
    assert run.run(47) == []
    assert run.run(48) == []
    assert run.run(49) == [(unit_waiting_key(UNIT), "firing")]


def test_a_reader_pass_keeps_both_clocks(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 1: READER, 2: ORDINARY})
    run.run(0)
    run.run(1)
    run.run(2)
    assert set(run.clock()) == {unit_waiting_key(UNIT), f"rotation-unit-reader-wait:{UNIT}"}
    assert run.clock()[unit_waiting_key(UNIT)] == START.isoformat()


def test_a_reader_pass_past_the_ordinary_threshold_raises_nothing(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 60: READER})
    run.run(0)
    assert run.run(60) == []


def test_an_open_ordinary_alert_survives_a_reader_pass(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 48: ORDINARY, 49: READER, 50: ORDINARY})
    run.run(0)
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]
    assert run.run(49) == []
    assert run.run(50) == [(unit_waiting_key(UNIT), "firing")]


def test_an_open_reader_alert_survives_an_ordinary_pass(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: READER, 192: READER, 193: ORDINARY, 194: READER})
    run.run(0)
    assert run.run(192) == [(unit_waiting_key(UNIT), "firing")]
    assert run.run(193) == []
    assert run.run(194) == [(unit_waiting_key(UNIT), "firing")]


def test_an_open_stuck_alert_survives_a_reader_pass(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: STUCK, 48: STUCK, 49: READER})
    run.run(0)
    assert run.run(48) == [(unit_stuck_key(UNIT), "firing")]
    assert run.run(49) == []


def test_a_unit_no_longer_sighted_clears_its_clocks_and_alert(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 48: ORDINARY, 49: READER})
    run.run(0)
    run.run(48)
    run.run(49)
    run.estate.ready = []
    assert run.run(50) == [(unit_waiting_key(UNIT), "resolved")]
    assert run.clock() == {}


def _revoke_state(
    monkeypatch: pytest.MonkeyPatch, state: worker.Revoke, reason: str | None
) -> worker.PassActs:
    monkeypatch.setattr(worker, "person_revokes_beside", lambda *_a: True)
    monkeypatch.setattr(worker, "revoke_wait", lambda *_a: (state, reason))
    monkeypatch.setattr(worker, "ObservedRevokes", lambda _d: None)
    rotation = SimpleNamespace(
        credential_id="c", occurrence="o", work_project_id="w", quarantine_key="q"
    )
    approved = SimpleNamespace(
        plan=None,
        unit=None,
        rotation=rotation,
        adapter=SimpleNamespace(revoke_settles=None),
    )
    setup = SimpleNamespace(
        store=SimpleNamespace(find=lambda *_a: None),
        client=SimpleNamespace(history=lambda _u: []),
        state_dir=None,
    )
    found = worker.PassActs()
    worker._revoke_waits(
        cast(worker.Setup, setup),
        cast(worker.Approved, approved),
        cast(Any, None),
        UNIT,
        found,
    )
    return found


def test_a_revoke_nobody_can_look_for_waits_on_the_ordinary_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = _revoke_state(monkeypatch, worker.Revoke.UNKNOWN, "the provider didn't answer")
    assert found.waits == {UNIT: worker.Wait("the provider didn't answer")}
    assert f"rotation-revoke:{UNIT}" in found.keep


def test_a_revoke_a_person_owes_takes_no_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    found = _revoke_state(monkeypatch, worker.Revoke.LIVE, "the old value still works")
    assert found.waits == {}
    assert f"rotation-revoke:{UNIT}" in found.due


def test_a_requeued_unit_starts_its_clock_fresh(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    run.estate.ready = []
    run.run(10)
    assert run.clock() == {}
    run.estate.ready = [UNIT]
    assert run.run(47) == []
    assert run.run(48) == []
    assert run.run(47 + 48) == [(unit_waiting_key(UNIT), "firing")]


def test_a_skip_past_ours_waits_on_the_clock(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    def skip(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        raise OrchestratorError("the window is shut")

    monkeypatch.setattr(worker, "_take", skip)
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]
    assert "the window is shut" in run.message(unit_waiting_key(UNIT))["message"]


def test_a_skip_on_a_unit_never_reached_past_ours_starts_no_clock(run: Pass) -> None:
    run.estate.add("ready")
    run.estate.rows = []
    run.estate.ready = [UNIT]
    run.estate.unreachable = {UNIT}
    assert run.run(0) == []
    assert run.run(100) == []
    assert run.clock() == {}


def test_a_foreign_unit_starts_no_clock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = Pass(monkeypatch, tmp_path, Recording())
    run.estate.add("ready")
    run.estate.rows = []
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(100) == []
    assert run.clock() == {}


def test_not_worked_raises_stuck_after_the_threshold(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        raise KeyError("nobody foresaw this")

    monkeypatch.setattr(worker, "_take", broken)
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(47) == []
    assert run.run(48) == [(unit_stuck_key(UNIT), "firing")]


def test_not_worked_before_ours_raises_nothing(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_setup: object, _unit_id: str, _found: worker.PassActs) -> int:
        raise KeyError("the read was malformed")

    monkeypatch.setattr(worker, "_take", broken)
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(100) == []


def test_a_refusal_whose_claim_raised_is_stuck(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_arguments: Any) -> None:
        raise worker.ContractError("the registry disagrees")

    monkeypatch.setattr(worker, "check_unit", refuse)
    run.estate.add("ready")
    run.estate.rows = []
    run.estate.ready = [UNIT]
    assert run.run(0) == []
    assert run.run(48) == [(unit_stuck_key(UNIT), "firing")]
    assert "the registry disagrees" in run.message(unit_stuck_key(UNIT))["message"]


def test_a_unit_alternating_between_stuck_and_waiting_still_comes_due(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def alternate(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        found.seen.add(unit_id)
        if run.now == START:
            raise KeyError("nobody foresaw this")
        found.waits[unit_id] = worker.Wait("a window")
        return 0

    monkeypatch.setattr(worker, "_take", alternate)
    run.estate.ready = [UNIT]
    run.run(0)
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]


def test_a_stuck_unit_that_moves_to_waiting_keeps_its_stuck_alert(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: STUCK, 48: STUCK, 49: ORDINARY})
    run.run(0)
    assert run.run(48) == [(unit_stuck_key(UNIT), "firing")]
    assert run.run(49) == [(unit_waiting_key(UNIT), "firing")]


def test_a_unit_alternating_when_overdue_resolves_neither_alert(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: ORDINARY, 48: ORDINARY, 49: STUCK, 50: ORDINARY})
    run.run(0)
    assert run.run(48) == [(unit_waiting_key(UNIT), "firing")]
    assert run.run(49) == [(unit_stuck_key(UNIT), "firing")]
    assert run.run(50) == [(unit_waiting_key(UNIT), "firing")]


def test_a_unit_no_longer_sighted_resolves_both_alerts(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_by_hour(run, monkeypatch, {0: STUCK, 48: STUCK, 49: ORDINARY})
    run.run(0)
    run.run(48)
    run.run(49)
    run.estate.ready = []
    assert set(run.run(50)) == {
        (unit_stuck_key(UNIT), "resolved"),
        (unit_waiting_key(UNIT), "resolved"),
    }


def test_the_clock_holds_one_entry_per_unit(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("x", stuck=True)}))
    run.estate.ready = [UNIT]
    run.run(0)
    assert run.clock() == {unit_waiting_key(UNIT): START.isoformat()}


def test_an_unread_unit_holds_its_keys(run: Pass) -> None:
    run.estate.add("failed")
    assert run.run(0) == [(unit_failed_key(UNIT), "firing")]
    run.estate.unreachable = {UNIT}
    assert run.run(1) == []
    run.estate.unreachable = set()
    run.estate.add("ready")
    assert run.run(2) == [(unit_failed_key(UNIT), "resolved")]


def test_a_row_that_can_t_be_read_holds_a_wait_on_a_unit_no_longer_ready(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(48)
    run.estate.ready = []
    run.estate.add("failed")
    run.estate.unreachable = {UNIT}
    assert run.run(49) == []
    assert run.clock() == {unit_waiting_key(UNIT): START.isoformat()}


def test_a_fetch_that_fails_still_raises_the_failed_alert(run: Pass) -> None:
    run.estate.add("failed", last_failure={"reason": "the host refused"})
    run.fetch_fails = True
    assert run.run() == [(unit_failed_key(UNIT), "firing")]
    message = run.message(unit_failed_key(UNIT))["message"]
    assert "github-pat" in message
    assert "the host refused" in message
    assert "staging key's name is omitted" in message
    assert "rotation/github-pat" not in message


def test_a_failed_package_refusal_omits_the_staging_name(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_arguments: Any) -> None:
        raise worker.ContractError("the commit isn't on main")

    monkeypatch.setattr(worker, "approved_package", refused)
    run.estate.add("failed")
    run.run()
    assert "staging key's name is omitted" in run.message(unit_failed_key(UNIT))["message"]


@pytest.mark.parametrize("fetch_fails", [False, True])
def test_packages_are_fetched_once_a_pass_however_many_units_failed(
    run: Pass, fetch_fails: bool
) -> None:
    other = "99999999-2222-3333-4444-555555555555"
    run.estate.add("failed")
    run.estate.units[other] = {**run.estate.units[UNIT], "id": other}
    run.estate.rows.append({**run.estate.rows[0], "unit_id": other})
    run.fetch_fails = fetch_fails
    assert len(run.run()) == 2
    assert run.syncs == 1
    run.run(1)
    assert run.syncs == 2


def test_no_failed_unit_fetches_nothing(run: Pass) -> None:
    run.estate.add("in_progress")
    run.run()
    assert run.syncs == 0


def test_a_wait_past_its_threshold_holds_through_an_unread_pass(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the alert nor the clock moves on a pass that never reached the unit."""
    real_take = worker._take
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(48)
    monkeypatch.setattr(worker, "_take", real_take)
    run.estate.add("ready")
    run.estate.rows = []
    run.estate.unreachable = {UNIT}
    assert run.run(49) == []
    assert run.clock() == {unit_waiting_key(UNIT): START.isoformat()}


def test_a_skip_leaves_a_requeued_unit_s_failed_alert_free_to_resolve(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.estate.add("failed")
    run.run(0)
    run.estate.add("ready")
    run.estate.ready = [UNIT]
    run.estate.unreachable = {UNIT}
    run.estate.rows[0]["blockers"] = []
    assert run.run(1) == [(unit_failed_key(UNIT), "resolved")]


def test_a_ledger_outage_resolves_nothing_and_the_pass_ends_normally(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(48)
    run.estate.ledger_down = True
    run.estate.ready = []
    assert run.run(49) == []
    assert run.clock() == {unit_waiting_key(UNIT): START.isoformat()}


def test_an_alert_hub_outage_never_fails_a_unit(run: Pass, monkeypatch: pytest.MonkeyPatch) -> None:
    run.hub.__dict__["_answers"] = [503] * 10
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.add("failed")
    run.estate.ready = [UNIT]
    run.run(0)
    assert [m["status"] for m in run.hub.sent] == ["firing"]
    run.run(48)
    assert len(run.hub.sent) == 3


def test_a_clock_that_can_t_be_saved_holds_every_waiting_alert(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    run.run(48)

    (run.state / "wait-since.json.tmp").mkdir()
    run.estate.add("failed")
    assert run.run(49) == [(unit_failed_key(UNIT), "firing")]


def test_a_corrupt_clock_file_restarts_the_clock(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waits_in(monkeypatch, lambda found: found.waits.update({UNIT: worker.Wait("a window")}))
    run.estate.ready = [UNIT]
    run.run(0)
    (run.state / "wait-since.json").write_text("{not json")
    assert run.run(48) == []
    assert run.run(96) == [(unit_waiting_key(UNIT), "firing")]


def test_act_keys_keep_their_bytes() -> None:
    assert alert_key("rotation-act", UNIT) == f"rotation-act:{UNIT}"
    assert unit_failed_key(UNIT) == f"rotation-unit-failed:{UNIT}"
    assert unit_review_key(UNIT) == f"rotation-unit-review:{UNIT}"
    assert unit_stuck_key(UNIT) == f"rotation-unit-stuck:{UNIT}"
    assert unit_waiting_key(UNIT) == f"rotation-unit-waiting:{UNIT}"


def test_a_long_package_refusal_leaves_the_next_step_in_the_message(
    run: Pass, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*_arguments: Any) -> None:
        raise worker.ContractError("x" * 5000)

    monkeypatch.setattr(worker, "approved_package", refused)
    run.estate.add("failed")
    run.run()
    message = run.message(unit_failed_key(UNIT))["message"]
    assert "requeue" in message
    assert len(message) < 1200
