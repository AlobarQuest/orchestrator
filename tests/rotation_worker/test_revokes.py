"""Seeing a person's revoke rather than being told of it (ADR-0055 amendment 15, increment 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rotation_worker import worker
from rotation_worker.adapters import ADAPTERS, bitbucket, github_pat
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.checks import Approved
from rotation_worker.contract import (
    Actor,
    HumanAct,
    Plan,
    Provider,
    Rotation,
    Step,
    StepKind,
    UnitPlan,
)
from rotation_worker.notify import Act
from rotation_worker.orchestrator_client import OrchestratorError
from rotation_worker.revokes import (
    STILL_LIVE,
    ObservedRevokes,
    Revoke,
    person_revokes_beside,
    recorded_revoke,
    revoke_wait,
)
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

OLD = Value("the-old-value-for-tests")
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
SETTLES = timedelta(minutes=75)


class Probe:
    """A provider's probe: the known-bad control answers `control`, the old value `old`."""

    dead_statuses = frozenset({400})

    def __init__(self, old: int | None, control: int | None = 400) -> None:
        self.answers = {"control": control, OLD.reveal(): old}
        self.asked: list[str] = []

    def known_bad(self) -> Value:
        return Value("control")

    def probe(self, value: Value) -> int | None:
        self.asked.append("control" if value.reveal() == "control" else "old")
        return self.answers[value.reveal()]


def _wait(
    probe: Probe,
    seen: ObservedRevokes,
    now: datetime = NOW,
    history: list[dict[str, Any]] | None = None,
    old: Value | None = OLD,
) -> tuple[Revoke, str | None]:
    provider = cast(Provider, probe)
    return revoke_wait(provider, old, seen, "c/2026-10-27", history or [], SETTLES, now)


def _recorded(at: datetime) -> dict[str, Any]:
    payload = {"command": {"status": "satisfied", "detail": {}}}
    return {"action": "dependency.resolved", "occurred_at": at.isoformat(), "payload": payload}


def test_only_a_unit_straight_after_a_person_s_revoke_looks_for_it() -> None:
    assert person_revokes_beside(bws.PLAN, bws.PLAN.unit("confirm"), -1)
    assert person_revokes_beside(bitbucket.PLAN, bitbucket.PLAN.unit("confirm"), -1)
    assert not person_revokes_beside(bws.PLAN, bws.PLAN.unit("verify"), -1)
    assert not person_revokes_beside(bws.PLAN, bws.PLAN.unit("store"), -1)
    assert not person_revokes_beside(github_pat.PLAN, github_pat.PLAN.unit("retire"), -1)
    # And the unit straight before the revoke is the one that gates it.
    assert person_revokes_beside(bws.PLAN, bws.PLAN.unit("verify"), 1)
    assert not person_revokes_beside(bws.PLAN, bws.PLAN.unit("confirm"), 1)


def test_only_the_neighbouring_segment_counts() -> None:
    confirm = UnitPlan("confirm", (Step(StepKind.CONFIRM_DEAD, Actor.MACHINE, "AC-001"),))
    after = UnitPlan("after", (Step(StepKind.VERIFY_CONSUMERS, Actor.MACHINE, "AC-002"),))
    revoke = HumanAct("revoke", (Step(StepKind.RETIRE_OLD, Actor.HUMAN),))
    plan = Plan((revoke, confirm, after))
    assert person_revokes_beside(plan, confirm, -1)
    assert not person_revokes_beside(plan, after, -1)
    # Off either end of the plan is no segment at all.
    assert not person_revokes_beside(plan, after, 1)
    assert not person_revokes_beside(Plan((confirm, revoke)), confirm, -1)


def test_an_old_value_still_live_waits_for_the_revoke(tmp_path: Path) -> None:
    probe = Probe(old=200)
    assert _wait(probe, ObservedRevokes(tmp_path)) == (Revoke.LIVE, STILL_LIVE)
    # Beside the control, and nothing is recorded as seen.
    assert probe.asked == ["control", "old"]
    assert ObservedRevokes(tmp_path).seen("c/2026-10-27") is None


@pytest.mark.parametrize("control", [200, None, 503])
def test_a_probe_that_doesn_t_refuse_its_control_proves_nothing(
    tmp_path: Path, control: int | None
) -> None:
    state, reason = _wait(Probe(old=400, control=control), ObservedRevokes(tmp_path))
    assert state is Revoke.UNKNOWN
    assert reason == f"the probe didn't refuse a known-bad value ({control})"


@pytest.mark.parametrize("old", [None, 503, 401])
def test_an_answer_neither_live_nor_dead_tells_nothing(tmp_path: Path, old: int | None) -> None:
    state, reason = _wait(Probe(old=old), ObservedRevokes(tmp_path))
    assert state is Revoke.UNKNOWN
    assert reason == f"the old value gave no answer to tell live from dead ({old})"


def test_a_refused_old_value_waits_out_the_window_from_when_it_was_first_seen(
    tmp_path: Path,
) -> None:
    probe = Probe(old=400)
    assert _wait(probe, ObservedRevokes(tmp_path))[0] is Revoke.SEEN
    later = NOW + SETTLES - timedelta(seconds=1)
    # A new instance reads the first sighting back: the window doesn't restart each pass, and
    # nothing is probed inside it.
    asked = len(probe.asked)
    assert _wait(probe, ObservedRevokes(tmp_path), later) == (
        Revoke.SEEN,
        f"a session from before the revoke may read until {NOW + SETTLES}",
    )
    assert len(probe.asked) == asked
    # The window's end is proved by a fresh probe, beside its control.
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES) == (Revoke.SEEN, None)
    assert probe.asked[asked:] == ["control", "old"]


def test_a_refusal_the_old_value_later_contradicts_is_forgotten(tmp_path: Path) -> None:
    seen = ObservedRevokes(tmp_path)
    _wait(Probe(old=400), seen)
    # Live again once the window ends: the earlier refusal never counts.
    assert _wait(Probe(old=200), seen, NOW + SETTLES)[0] is Revoke.LIVE
    assert seen.seen("c/2026-10-27") is None
    revoked = NOW + timedelta(hours=5)
    assert _wait(Probe(old=400), seen, revoked)[1] is not None
    assert _wait(Probe(old=400), seen, revoked + SETTLES) == (Revoke.SEEN, None)


def test_a_later_recorded_revoke_lengthens_the_wait(tmp_path: Path) -> None:
    probe = Probe(old=400)
    recorded = NOW + timedelta(minutes=30)
    history = [_recorded(recorded)]
    assert _wait(probe, ObservedRevokes(tmp_path), NOW, history)[1] is not None
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES, history)[1] is not None
    assert _wait(probe, ObservedRevokes(tmp_path), recorded + SETTLES, history)[1] is None


def test_an_earlier_recorded_revoke_never_shortens_it(tmp_path: Path) -> None:
    history = [_recorded(NOW - timedelta(hours=3))]
    assert _wait(Probe(old=400), ObservedRevokes(tmp_path), NOW, history)[1] is not None


def test_a_lost_sighting_restarts_the_window(tmp_path: Path) -> None:
    probe = Probe(old=400)
    _wait(probe, ObservedRevokes(tmp_path))
    (tmp_path / "revokes-observed.json").write_text("{not json")
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES)[1] is not None


def test_sightings_of_long_finished_rotations_are_dropped(tmp_path: Path) -> None:
    seen = ObservedRevokes(tmp_path)
    seen.record("old/one", NOW)
    seen.record("new/one", NOW + timedelta(days=31))
    assert seen.seen("old/one") is None
    assert seen.seen("new/one") == NOW + timedelta(days=31)


def test_no_window_means_claimed_as_soon_as_the_refusal_is_seen(tmp_path: Path) -> None:
    provider = cast(Provider, Probe(old=400))
    seen = ObservedRevokes(tmp_path)
    assert revoke_wait(provider, OLD, seen, "c/o", [], timedelta(0), NOW) == (Revoke.SEEN, None)


def test_no_quarantine_leaves_it_to_the_contract(tmp_path: Path) -> None:
    probe = Probe(old=200)
    assert _wait(probe, ObservedRevokes(tmp_path), old=None) == (Revoke.SEEN, None)
    assert probe.asked == []


def test_only_a_person_s_recorded_act_counts_as_a_recorded_revoke() -> None:
    completed = {
        "action": "dependency.resolved",
        "occurred_at": NOW.isoformat(),
        "payload": {"status": "satisfied", "reason": "dependency_completed"},
    }
    assert recorded_revoke([completed]) is None
    assert recorded_revoke([completed, _recorded(NOW)]) == NOW


# ---- the worker's wait ------------------------------------------------------------------------


def _approved(store: MemoryStore, unit: str = "confirm") -> Approved:
    rotation = Rotation("bws-tok-x", "2026-10-27-requested", "keeper", "work", ())
    store.put("work", rotation.quarantine_key, OLD)
    return cast(
        Approved,
        SimpleNamespace(
            adapter=ADAPTERS[bws.NAME],
            plan=bws.PLAN,
            unit=bws.PLAN.unit(unit),
            rotation=rotation,
            reads={},
        ),
    )


def _setup(store: MemoryStore, tmp_path: Path, client: object = None, **fields: Any) -> Any:
    return SimpleNamespace(
        store=store,
        state_dir=tmp_path,
        client=client or SimpleNamespace(history=lambda _unit: []),
        **fields,
    )


def _revoke_waits(
    tmp_path: Path, probe: Probe, unit: str = "confirm"
) -> tuple[bool, worker.PassActs]:
    store = MemoryStore()
    found = worker.PassActs()
    setup = cast(worker.Setup, _setup(store, tmp_path))
    waits = worker._revoke_waits(setup, _approved(store, unit), cast(Provider, probe), "u1", found)
    return waits, found


def test_the_worker_announces_a_revoke_not_yet_seen(tmp_path: Path) -> None:
    waits, found = _revoke_waits(tmp_path, Probe(old=200))
    assert waits is True
    assert list(found.due) == ["rotation-revoke:u1"]
    assert found.due["rotation-revoke:u1"].title == "Revoke the old bws-tok-x value"
    assert found.due["rotation-revoke:u1"].link == "https://sds.alobar.net/review/units/u1"
    assert found.keep == set()


def test_the_worker_waits_without_an_alert_once_the_revoke_is_seen(tmp_path: Path) -> None:
    waits, found = _revoke_waits(tmp_path, Probe(old=400))
    assert waits is True
    assert (found.due, found.keep) == ({}, set())


def test_a_pass_that_can_t_tell_keeps_the_revoke_alert_as_it_was(tmp_path: Path) -> None:
    waits, found = _revoke_waits(tmp_path, Probe(old=None))
    assert waits is True
    assert (found.due, found.keep) == ({}, {"rotation-revoke:u1"})


def test_the_worker_doesn_t_look_for_a_revoke_before_a_unit_that_follows_none(
    tmp_path: Path,
) -> None:
    probe = Probe(old=200)
    assert _revoke_waits(tmp_path, probe, unit="verify")[0] is False
    assert probe.asked == []


class KeychainMemory(MemoryStore):
    read_back_kind = "keychain"


class Client:
    def unit(self, unit_id: str) -> dict[str, Any]:
        return {
            "id": unit_id,
            "work_package_revision_id": "r",
            "authority": {"constraints": {"rotation_adapter": bws.NAME}},
        }

    def package_intake(self, revision: str) -> dict[str, Any]:
        return {}

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return []

    def claim(self, *arguments: Any) -> None:
        raise AssertionError("a unit whose revoke isn't seen was claimed")


def test_a_confirm_unit_whose_revoke_isn_t_seen_is_left_unclaimed_and_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KeychainMemory()
    approved = _approved(store)
    monkeypatch.setattr(worker, "check_unit", lambda *_arguments: approved)
    monkeypatch.setattr(worker, "_held", lambda _setup, _approved: None)
    monkeypatch.setattr(worker, "_provider", lambda _setup, _approved: Probe(old=200))
    setup = _setup(store, tmp_path, Client(), mirror=None, work_project_id="work")
    found = worker.PassActs()
    assert worker._take(cast(worker.Setup, setup), "u1", found) == 0
    assert list(found.due) == ["rotation-revoke:u1"]


def test_a_pass_announces_its_waits_findings_and_keeps_what_it_couldn_t_tell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    act = Act("rotation-revoke:u1", "Revoke the old x value", "m")

    def take(_setup: object, unit_id: str, found: worker.PassActs) -> int:
        if unit_id == "u3":
            raise OrchestratorError("the unit didn't answer")
        found.due[act.key] = act
        found.keep.add("rotation-revoke:u2")
        return 0

    announced: list[tuple[dict[str, Act], set[str]]] = []
    notifier = SimpleNamespace(announce=lambda acts, unread: announced.append((acts, unread)))
    client = SimpleNamespace(ready_units=lambda: ["u1", "u3"], active_units=lambda: [])
    monkeypatch.setattr(worker, "_take", take)
    setup = SimpleNamespace(client=client, notifier=notifier, state_dir=tmp_path)
    worker.run_pass(cast(worker.Setup, setup))
    kept = {
        "rotation-revoke:u2",
        "rotation-revoke:u3",
        "rotation-mint:u3",
        "rotation-inbox:u3",
        "rotation-unit-stuck:u3",
        "rotation-unit-waiting:u3",
    }
    assert announced == [({act.key: act}, kept)]
