"""Which execution conditions raise an alert, and which a sibling lane already owns."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from bump_driver.cli import run_pass
from bump_driver.client import CallError, GitHub, Orchestrator
from execution_watch.decide import (
    Condition,
    Thresholds,
    UnitRow,
    conditions,
    firing,
    owned_elsewhere,
    raised_keys,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
REPOSITORY = "AlobarQuest/orchestrator"
BUMP = "dependency-update"
THRESHOLDS = Thresholds()


def row(**changes: Any) -> UnitRow:
    base = UnitRow(
        unit_id=str(uuid.UUID(int=1)),
        state="ready",
        change_class=BUMP,
        is_rotation=False,
        standing=True,
        target_repository=REPOSITORY,
        granted=True,
        latest_dispatch_status=None,
        latest_dispatch_at=None,
        has_claim=False,
        lease_lapsed=False,
        breaker_open=False,
    )
    return replace(base, **changes)


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def unowned(**changes: Any) -> UnitRow:
    """A row of a class no sibling lane covers, so every condition it has is this lane's."""
    return row(change_class="app-change", standing=False, **changes)


def test_unit_failed_is_a_failed_state() -> None:
    assert conditions(unowned(state="failed"), NOW) == {Condition.UNIT_FAILED}


@pytest.mark.parametrize("state", ["blocked", "revision_required"])
def test_unit_held_is_blocked_or_revision_required(state: str) -> None:
    assert conditions(unowned(state=state), NOW) == {Condition.UNIT_HELD}


def test_stalled_is_a_lapsed_lease_and_nothing_owns_it() -> None:
    stalled = row(state="claimed", lease_lapsed=True)
    assert conditions(stalled, NOW) == {Condition.STALLED}
    assert not owned_elsewhere(stalled, Condition.STALLED)


def test_an_open_breaker_alone_raises_nothing() -> None:
    assert conditions(unowned(breaker_open=True), NOW) == set()


def test_a_breaker_alone_raises_nothing_after_a_later_success() -> None:
    # Three failures then a success: the breaker counts all time, the latest dispatch is fine.
    succeeded = unowned(
        breaker_open=True, latest_dispatch_status="dispatched", latest_dispatch_at=hours_ago(0.5)
    )
    assert conditions(succeeded, NOW) == set()
    for state in ("claimed", "executing"):
        assert conditions(replace(succeeded, state=state, has_claim=True), NOW) == set()


@pytest.mark.parametrize("state", ["claimed", "executing"])
def test_an_executing_unit_with_a_failed_dispatch_raises_nothing(state: str) -> None:
    started = unowned(
        state=state,
        has_claim=True,
        breaker_open=True,
        latest_dispatch_status="failed",
        latest_dispatch_at=hours_ago(9),
    )
    assert conditions(started, NOW) == set()


@pytest.mark.parametrize(
    "change_class,standing", [("app-change", False), (BUMP, False), (BUMP, True)]
)
def test_a_failed_unit_raises_only_unit_failed(change_class: str, standing: bool) -> None:
    failed = row(
        change_class=change_class,
        standing=standing,
        state="failed",
        latest_dispatch_status="dispatched",
        latest_dispatch_at=hours_ago(9),
        breaker_open=True,
    )
    assert conditions(failed, NOW) == {Condition.UNIT_FAILED}


def test_a_unit_walking_its_whole_life_fires_nothing() -> None:
    steps = [
        ("ready", None, None, False),
        ("ready", "dispatched", 0.1, False),
        ("claimed", "dispatched", 3, True),
        ("executing", "dispatched", 3, True),
        ("submitted", "dispatched", 3, True),
        ("verifying", "dispatched", 3, True),
        ("awaiting_review", "dispatched", 3, True),
        ("completed", "dispatched", 3, True),
    ]
    for state, status, age, claimed in steps:
        walking = unowned(
            state=state,
            latest_dispatch_status=status,
            latest_dispatch_at=hours_ago(age) if age is not None else None,
            has_claim=claimed,
            breaker_open=True,
        )
        assert firing([walking], {}, set(), NOW, THRESHOLDS) == {}, state


def test_a_bump_that_is_not_standing_failed_alerts() -> None:
    failed = row(state="failed", standing=False)
    assert not owned_elsewhere(failed, Condition.UNIT_FAILED)
    assert owned_elsewhere(row(state="failed"), Condition.UNIT_FAILED)


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_dispatch_blocked_is_a_blocked_or_failed_latest_dispatch(status: str) -> None:
    latest = unowned(latest_dispatch_status=status, latest_dispatch_at=hours_ago(5))
    assert conditions(latest, NOW) == {Condition.DISPATCH_BLOCKED}


def test_a_later_success_clears_dispatch_blocked() -> None:
    assert conditions(unowned(latest_dispatch_status="dispatched", has_claim=True), NOW) == set()
    assert conditions(unowned(latest_dispatch_status="skipped"), NOW) == set()


def test_a_unit_held_alone_is_owned_only_in_revision_required() -> None:
    covered = row(state="blocked")
    assert not owned_elsewhere(covered, Condition.UNIT_HELD)
    assert owned_elsewhere(row(state="revision_required"), Condition.UNIT_HELD)


def test_dispatch_blocked_by_status_waits_an_hour_from_first_sighting() -> None:
    blocked = unowned(latest_dispatch_status="failed", latest_dispatch_at=hours_ago(9))
    key = f"execution-dispatch-blocked:{blocked.unit_id}"
    assert firing([blocked], {key: hours_ago(0.5)}, set(), NOW, THRESHOLDS) == {}
    assert key in firing([blocked], {key: hours_ago(1)}, set(), NOW, THRESHOLDS)
    assert firing([blocked], {}, set(), NOW, THRESHOLDS) == {}


def test_an_open_breaker_fires_without_waiting() -> None:
    broken = unowned(
        breaker_open=True, latest_dispatch_status="failed", latest_dispatch_at=hours_ago(0.1)
    )
    assert list(firing([broken], {}, set(), NOW, THRESHOLDS)) == [
        f"execution-dispatch-blocked:{broken.unit_id}"
    ]


def test_run_unclaimed_is_a_dispatch_nobody_claimed_for_two_hours() -> None:
    waiting = unowned(latest_dispatch_status="dispatched", latest_dispatch_at=hours_ago(1.9))
    assert conditions(waiting, NOW) == set()
    overdue = replace(waiting, latest_dispatch_at=hours_ago(2))
    assert conditions(overdue, NOW) == {Condition.RUN_UNCLAIMED}
    assert conditions(replace(overdue, has_claim=True), NOW) == set()


def test_a_rotation_unit_raises_nothing() -> None:
    rotation = row(
        is_rotation=True,
        change_class="credential-rotation",
        state="failed",
        lease_lapsed=True,
        breaker_open=True,
        latest_dispatch_status="dispatched",
        latest_dispatch_at=hours_ago(9),
    )
    assert conditions(rotation, NOW) == set()
    assert raised_keys([rotation], NOW, THRESHOLDS) == set()
    assert firing([rotation], {}, set(), NOW, THRESHOLDS) == {}


def test_a_bump_that_lost_its_grant_alerts() -> None:
    held = row(state="revision_required", granted=False)
    assert owned_elsewhere(row(state="revision_required"), Condition.UNIT_HELD)
    assert not owned_elsewhere(held, Condition.UNIT_HELD)
    assert list(firing([held], {}, set(), NOW, THRESHOLDS)) == [
        f"execution-unit-held:{held.unit_id}"
    ]


def test_a_bump_the_sibling_lanes_cover_stays_silent() -> None:
    covered = [
        row(state="failed"),
        row(state="revision_required"),
        row(latest_dispatch_status="failed", latest_dispatch_at=hours_ago(3)),
        row(latest_dispatch_status="dispatched", latest_dispatch_at=hours_ago(3)),
    ]
    assert firing(covered, {}, set(), NOW, THRESHOLDS) == {}


def test_a_known_subject_stays_silent() -> None:
    failed = unowned(state="failed")
    key = f"execution-unit-failed:{failed.unit_id}"
    assert list(firing([failed], {}, set(), NOW, THRESHOLDS)) == [key]
    assert firing([failed], {}, {key}, NOW, THRESHOLDS) == {}


def test_a_known_key_does_not_silence_another_condition_on_the_unit() -> None:
    failed = unowned(state="failed")
    stalled = replace(failed, state="claimed", lease_lapsed=True)
    known = {f"execution-unit-failed:{failed.unit_id}"}
    assert list(firing([stalled], {}, known, NOW, THRESHOLDS)) == [
        f"execution-stalled:{stalled.unit_id}"
    ]


def test_the_unclaimed_message_names_failing_then_cancelling() -> None:
    unclaimed = unowned(latest_dispatch_status="dispatched", latest_dispatch_at=hours_ago(3))
    [alert] = firing([unclaimed], {}, set(), NOW, THRESHOLDS).values()
    message = alert.message.lower()
    assert "fail the unit" in message
    assert message.index("fail the unit") < message.index("cancel it")
    assert "3 hours" in message


@pytest.mark.parametrize(
    "shape",
    [
        {"state": "failed"},
        {"state": "blocked"},
        {"state": "claimed", "lease_lapsed": True},
        {"state": "ready", "breaker_open": True, "latest_dispatch_status": "failed"},
        {"latest_dispatch_status": "dispatched", "latest_dispatch_at": hours_ago(3)},
    ],
)
def test_every_alert_says_what_happened_and_the_next_step(shape: dict[str, Any]) -> None:
    [alert] = firing([unowned(**shape)], {}, set(), NOW, THRESHOLDS).values()
    unit = str(uuid.UUID(int=1))
    assert unit in alert.title or unit in alert.message
    assert len(alert.message.split(". ")) >= 2
    for hedge in ("may ", "might", "possibly", "probably", "perhaps"):
        assert hedge not in alert.message.lower()


def test_no_reported_kind_is_a_substring_of_another() -> None:
    kinds = [str(condition) for condition in Condition]
    for kind in kinds:
        assert not any(kind != other and kind in other for other in kinds)


def test_the_keys_are_the_kind_and_the_unit() -> None:
    failed = unowned(state="failed")
    assert raised_keys([failed], NOW, THRESHOLDS) == {f"execution-unit-failed:{failed.unit_id}"}


# The ownership table, held to what the bump lane really does.

BUMP_UNIT = "0c7a484c-1111-4111-8111-111111111111"
REVISION = "f962475a-1964-46df-81ac-2b3029dec2a7"


class _Orchestrator:
    def __init__(self, shape: UnitRow, history: list[dict[str, Any]]) -> None:
        self._shape = shape
        self._history = history

    def work(self, change_record_id: int) -> dict[str, Any]:
        return {
            "revision_ids": [REVISION],
            "units": [{"unit_id": BUMP_UNIT, "revision_id": REVISION, "state": self._shape.state}],
        }

    def intake(self, revision_id: str) -> dict[str, Any]:
        return {
            "id": REVISION,
            "profile": BUMP,
            "revision": 1,
            "enforcement_snapshot": {
                "profile_fields": {
                    "standing": self._shape.standing,
                    "target_repo": self._shape.target_repository,
                }
            },
        }

    def unit(self, unit_id: str) -> dict[str, Any]:
        return {
            "id": BUMP_UNIT,
            "state": self._shape.state,
            "version": 3,
            "authority": {
                "change_class": self._shape.change_class,
                "constraints": {"target_repository": self._shape.target_repository},
            },
        }

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return self._history

    def dispatch(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        raise CallError("dispatch refused", status=409, code="refused")


def _bump_raises(shape: UnitRow) -> bool:
    """Whether the bump lane's own pass raises a keyed alert for this unit."""
    record = {
        "id": 170,
        "status": "approved",
        "change_class": shape.change_class,
        "target_repository": shape.target_repository,
        "package_id": "orchestrator-uv-httpx2",
        "package_revision": 1,
    }
    history = []
    if shape.latest_dispatch_status == "dispatched":
        history = [
            {
                "action": "dispatch.dispatched",
                "occurred_at": cast(datetime, shape.latest_dispatch_at).isoformat(),
            }
        ]
    granted = {REPOSITORY.lower(): REPOSITORY} if shape.granted else {}

    def refuse(*_: object) -> Any:
        raise AssertionError("a unit in this table is never decomposed")

    outcomes = run_pass(
        [record],
        cast(Orchestrator, _Orchestrator(shape, history)),
        cast(GitHub, object()),
        granted,
        refuse,
        refuse,
        now=NOW,
    )
    return any(outcome.key is not None for outcome in outcomes)


# Each bump-covered condition, as the unit shape that raises it.
_SHAPES: dict[Condition, dict[str, Any]] = {
    Condition.UNIT_HELD: {"state": "revision_required"},
    Condition.DISPATCH_BLOCKED: {
        "state": "ready",
        "latest_dispatch_status": "failed",
        "latest_dispatch_at": hours_ago(3),
    },
    Condition.RUN_UNCLAIMED: {
        "state": "ready",
        "latest_dispatch_status": "dispatched",
        "latest_dispatch_at": hours_ago(3),
    },
}


@pytest.mark.parametrize("condition", list(_SHAPES))
@pytest.mark.parametrize("standing", [True, False])
@pytest.mark.parametrize("granted", [True, False])
@pytest.mark.parametrize("change_class", [BUMP, "app-change"])
def test_the_ownership_table_matches_the_bump_lane(
    condition: Condition, standing: bool, granted: bool, change_class: str
) -> None:
    shape = row(change_class=change_class, standing=standing, granted=granted, **_SHAPES[condition])
    assert condition in conditions(shape, NOW)
    assert owned_elsewhere(shape, condition) == _bump_raises(shape)


def test_a_failed_bump_unit_is_the_work_watchers_and_never_the_bump_lanes() -> None:
    failed = row(state="failed")
    assert not _bump_raises(failed)
    assert owned_elsewhere(failed, Condition.UNIT_FAILED)
    assert not owned_elsewhere(
        row(state="failed", change_class="app-change"), Condition.UNIT_FAILED
    )


def test_the_bump_lane_alert_for_a_dispatch_refusal_comes_from_a_ready_unit_only() -> None:
    # A dispatch the orchestrator refused: the bump lane keys it, so this lane stays silent.
    ready = row(state="ready")
    assert _bump_raises(ready)
    assert not _bump_raises(row(state="claimed"))
    assert not conditions(row(state="claimed", breaker_open=True), NOW)
