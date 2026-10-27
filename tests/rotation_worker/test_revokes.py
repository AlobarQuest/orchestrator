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
from rotation_worker.contract import Provider, Rotation
from rotation_worker.notify import Act
from rotation_worker.revokes import (
    STILL_LIVE,
    ObservedRevokes,
    person_revokes_before,
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
) -> str | None:
    provider = cast(Provider, probe)
    return revoke_wait(provider, old, seen, "c/2026-10-27", history or [], SETTLES, now)


def _recorded(at: datetime) -> dict[str, Any]:
    payload = {"command": {"status": "satisfied", "detail": {}}}
    return {"action": "dependency.resolved", "occurred_at": at.isoformat(), "payload": payload}


def test_only_a_unit_straight_after_a_person_s_revoke_looks_for_it() -> None:
    assert person_revokes_before(bws.PLAN, bws.PLAN.unit("confirm"))
    assert person_revokes_before(bitbucket.PLAN, bitbucket.PLAN.unit("confirm"))
    assert not person_revokes_before(bws.PLAN, bws.PLAN.unit("verify"))
    assert not person_revokes_before(bws.PLAN, bws.PLAN.unit("store"))
    assert not person_revokes_before(github_pat.PLAN, github_pat.PLAN.unit("retire"))


def test_an_old_value_still_live_waits_for_the_revoke(tmp_path: Path) -> None:
    probe = Probe(old=200)
    assert _wait(probe, ObservedRevokes(tmp_path)) == STILL_LIVE
    # Beside the control, and nothing is recorded as seen.
    assert probe.asked == ["control", "old"]
    assert not (tmp_path / "revokes-observed.json").exists()


@pytest.mark.parametrize("control", [200, None, 503])
def test_a_probe_that_doesn_t_refuse_its_control_proves_nothing(
    tmp_path: Path, control: int | None
) -> None:
    reason = _wait(Probe(old=400, control=control), ObservedRevokes(tmp_path))
    assert reason == f"the probe didn't refuse a known-bad value ({control})"


@pytest.mark.parametrize("old", [None, 503, 401])
def test_an_answer_neither_live_nor_dead_waits(tmp_path: Path, old: int | None) -> None:
    reason = _wait(Probe(old=old), ObservedRevokes(tmp_path))
    assert reason == f"the old value gave no answer to tell live from dead ({old})"


def test_a_refused_old_value_waits_out_the_window_from_when_it_was_first_seen(
    tmp_path: Path,
) -> None:
    probe = Probe(old=400)
    assert _wait(probe, ObservedRevokes(tmp_path)) is not None
    later = NOW + SETTLES - timedelta(seconds=1)
    # A new instance reads the first sighting back: the window doesn't restart each pass.
    assert _wait(probe, ObservedRevokes(tmp_path), later) is not None
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES) is None


def test_a_later_recorded_revoke_lengthens_the_wait(tmp_path: Path) -> None:
    probe = Probe(old=400)
    recorded = NOW + timedelta(minutes=30)
    history = [_recorded(recorded)]
    assert _wait(probe, ObservedRevokes(tmp_path), NOW, history) is not None
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES, history) is not None
    assert _wait(probe, ObservedRevokes(tmp_path), recorded + SETTLES, history) is None


def test_an_earlier_recorded_revoke_never_shortens_it(tmp_path: Path) -> None:
    probe = Probe(old=400)
    history = [_recorded(NOW - timedelta(hours=3))]
    assert _wait(probe, ObservedRevokes(tmp_path), NOW, history) is not None


def test_a_lost_sighting_restarts_the_window(tmp_path: Path) -> None:
    probe = Probe(old=400)
    _wait(probe, ObservedRevokes(tmp_path))
    (tmp_path / "revokes-observed.json").write_text("{not json")
    assert _wait(probe, ObservedRevokes(tmp_path), NOW + SETTLES) is not None


def test_no_window_means_claimed_as_soon_as_the_refusal_is_seen(tmp_path: Path) -> None:
    provider = cast(Provider, Probe(old=400))
    seen = ObservedRevokes(tmp_path)
    assert revoke_wait(provider, OLD, seen, "c/o", [], timedelta(0), NOW) is None


def test_no_quarantine_leaves_it_to_the_contract(tmp_path: Path) -> None:
    probe = Probe(old=200)
    assert _wait(probe, ObservedRevokes(tmp_path), old=None) is None
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


def _approved(store: MemoryStore) -> Approved:
    rotation = Rotation("bws-tok-x", "2026-10-27-requested", "keeper", "work", ())
    store.put("work", rotation.quarantine_key, OLD)
    return cast(
        Approved,
        SimpleNamespace(
            adapter=ADAPTERS[bws.NAME],
            plan=bws.PLAN,
            unit=bws.PLAN.unit("confirm"),
            rotation=rotation,
            reads={},
        ),
    )


def _setup(store: MemoryStore, tmp_path: Path) -> worker.Setup:
    client = SimpleNamespace(history=lambda _unit: [])
    return cast(worker.Setup, SimpleNamespace(store=store, state_dir=tmp_path, client=client))


def test_the_worker_announces_a_revoke_not_yet_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    approved = _approved(store)
    monkeypatch.setattr(worker, "_provider", lambda _setup, _approved: Probe(old=200))
    due: dict[str, Act] = {}
    assert worker._revoke_waits(_setup(store, tmp_path), approved, "u1", due) is True
    assert list(due) == ["rotation-revoke:u1"]
    assert due["rotation-revoke:u1"].title == "Revoke the old bws-tok-x value"
    assert due["rotation-revoke:u1"].link == "https://sds.alobar.net/review/units/u1"


def test_the_worker_waits_silently_once_the_revoke_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    approved = _approved(store)
    monkeypatch.setattr(worker, "_provider", lambda _setup, _approved: Probe(old=400))
    due: dict[str, Act] = {}
    assert worker._revoke_waits(_setup(store, tmp_path), approved, "u1", due) is True
    assert due == {}


def test_the_worker_doesn_t_look_for_a_revoke_before_a_unit_that_follows_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    approved = cast(
        Approved, SimpleNamespace(**{**vars(_approved(store)), "unit": bws.PLAN.unit("verify")})
    )
    monkeypatch.setattr(worker, "_provider", lambda _setup, _approved: Probe(old=200))
    assert worker._revoke_waits(_setup(store, tmp_path), approved, "u1", {}) is False


def test_a_provider_that_can_t_be_built_waits_rather_than_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rotation_worker.config import ConfigError

    def unbuildable(_setup: object, _approved: object) -> Provider:
        raise ConfigError("no admin secret")

    store = MemoryStore()
    monkeypatch.setattr(worker, "_provider", unbuildable)
    assert worker._revoke_waits(_setup(store, tmp_path), _approved(store), "u1", {}) is True


def test_only_the_unit_straight_after_the_revoke_looks_for_it() -> None:
    from rotation_worker.contract import Actor, HumanAct, Plan, Step, StepKind, UnitPlan

    confirm = UnitPlan("confirm", (Step(StepKind.CONFIRM_DEAD, Actor.MACHINE, "AC-001"),))
    after = UnitPlan("after", (Step(StepKind.VERIFY_CONSUMERS, Actor.MACHINE, "AC-002"),))
    revoke = HumanAct("revoke", (Step(StepKind.RETIRE_OLD, Actor.HUMAN),))
    plan = Plan((revoke, confirm, after))
    assert person_revokes_before(plan, confirm)
    assert not person_revokes_before(plan, after)


class KeychainMemory(MemoryStore):
    read_back_kind = "keychain"


def test_a_confirm_unit_whose_revoke_isn_t_seen_is_left_unclaimed_and_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KeychainMemory()
    approved = _approved(store)

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

    monkeypatch.setattr(worker, "check_unit", lambda *_arguments: approved)
    monkeypatch.setattr(worker, "_held", lambda _setup, _approved: None)
    monkeypatch.setattr(worker, "_provider", lambda _setup, _approved: Probe(old=200))
    setup = cast(
        worker.Setup,
        SimpleNamespace(
            store=store, state_dir=tmp_path, client=Client(), mirror=None, work_project_id="work"
        ),
    )
    due: dict[str, Act] = {}
    assert worker._take(setup, "u1", due) == 0
    assert list(due) == ["rotation-revoke:u1"]


def test_a_pass_announces_the_revokes_its_waits_found_due(monkeypatch: pytest.MonkeyPatch) -> None:
    act = Act("rotation-revoke:u1", "Revoke the old x value", "m")

    def take(_setup: object, _unit_id: str, due: dict[str, Act]) -> int:
        due[act.key] = act
        return 0

    announced: list[tuple[dict[str, Act], set[str]]] = []
    notifier = SimpleNamespace(announce=lambda acts, unread: announced.append((acts, unread)))
    client = SimpleNamespace(ready_units=lambda: ["u1"], active_units=lambda: [])
    monkeypatch.setattr(worker, "_take", take)
    setup = cast(worker.Setup, SimpleNamespace(client=client, notifier=notifier))
    worker.run_pass(setup)
    assert announced == [({act.key: act}, set())]
