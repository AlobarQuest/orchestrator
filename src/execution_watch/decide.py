"""Which work-unit execution conditions this lane tells a person about. Pure: no I/O, no clock.

A row (the orchestrator's `execution-status` read) can show five conditions, each keyed
`execution-<kind>:<unit>`. Two sibling lanes already alert on part of that ground, and this lane
stays silent on exactly what they cover (`owned_elsewhere`), so a person is told once:

* `work_watcher` alerts on a failed unit of a `standing` `dependency-update` package.
* `bump_driver` alerts on a `ready` or `revision_required` unit of a standing `dependency-update`
  package in a repository the member record grants: a unit in `revision_required`, a refused
  dispatch, and a run nobody claimed. A bump whose repository lost its grant falls out of that
  lane's selection, so this lane takes it over.

A rotation unit raises nothing here: the rotation executor owns its own alerts.

`known` holds the keys that were already raised when the lane first ran. They stay silent for as
long as they stay raised; a key that clears leaves `known`, so a return is new and alerts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from alert_hub_client import Alert, alert_key

BUMP_CHANGE_CLASS: Final = "dependency-update"
_REVISION_REQUIRED: Final = "revision_required"
_READY: Final = "ready"
_HELD_STATES: Final = frozenset({"blocked", _REVISION_REQUIRED})
_BLOCKED_DISPATCH: Final = frozenset({"blocked", "failed"})
_DISPATCHED: Final = "dispatched"


class Condition(StrEnum):
    UNIT_FAILED = "execution-unit-failed"
    UNIT_HELD = "execution-unit-held"
    STALLED = "execution-stalled"
    DISPATCH_BLOCKED = "execution-dispatch-blocked"
    RUN_UNCLAIMED = "execution-run-unclaimed"
    WATCH_UNREAD = "execution-watch-unread"


@dataclass(frozen=True)
class UnitRow:
    """One row of the orchestrator's execution-status read."""

    unit_id: str
    state: str
    change_class: str | None
    is_rotation: bool
    standing: bool
    target_repository: str | None
    granted: bool
    latest_dispatch_status: str | None
    latest_dispatch_at: datetime | None
    has_claim: bool
    lease_lapsed: bool
    breaker_open: bool


@dataclass(frozen=True)
class Thresholds:
    dispatch_blocked_hours: int = 1
    run_unclaimed_hours: int = 2
    unread_hours: int = 2


DEFAULT_THRESHOLDS: Final = Thresholds()


def key_for(condition: Condition, unit_id: str) -> str:
    return alert_key(condition, unit_id)


def conditions(
    row: UnitRow, now: datetime, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> set[Condition]:
    """What is true of the unit right now, whoever alerts on it. Dispatch conditions need a `ready`
    unit: once a run is claimed, an older refusal or a GitHub error no longer stops it. The
    one-hour wait on a blocked dispatch is a first-sighting clock and belongs to `firing`; an
    open breaker only skips that wait."""
    if row.is_rotation:
        return set()
    found: set[Condition] = set()
    if row.state == "failed":
        found.add(Condition.UNIT_FAILED)
    if row.state in _HELD_STATES:
        found.add(Condition.UNIT_HELD)
    if row.lease_lapsed:
        found.add(Condition.STALLED)
    if row.state == _READY and row.latest_dispatch_status in _BLOCKED_DISPATCH:
        found.add(Condition.DISPATCH_BLOCKED)
    if (
        row.state == _READY
        and row.latest_dispatch_status == _DISPATCHED
        and not row.has_claim
        and row.latest_dispatch_at is not None
        and now - row.latest_dispatch_at >= timedelta(hours=thresholds.run_unclaimed_hours)
    ):
        found.add(Condition.RUN_UNCLAIMED)
    return found


def _is_covered_bump(row: UnitRow) -> bool:
    return (
        row.change_class == BUMP_CHANGE_CLASS
        and row.standing
        and row.granted
        and row.target_repository is not None
    )


def owned_elsewhere(row: UnitRow, condition: Condition) -> bool:
    """Whether a sibling lane already alerts on this condition for this unit."""
    if condition is Condition.UNIT_FAILED:
        return row.change_class == BUMP_CHANGE_CLASS and row.standing
    if condition is Condition.UNIT_HELD:
        return _is_covered_bump(row) and row.state == _REVISION_REQUIRED
    if condition in (Condition.DISPATCH_BLOCKED, Condition.RUN_UNCLAIMED):
        return _is_covered_bump(row) and row.state == _READY
    return False


def _raised(row: UnitRow, now: datetime, thresholds: Thresholds) -> list[Condition]:
    return sorted(
        condition
        for condition in conditions(row, now, thresholds)
        if not owned_elsewhere(row, condition)
    )


def raised_keys(rows: Iterable[UnitRow], now: datetime, thresholds: Thresholds) -> set[str]:
    """Every key raised now, before any wait: what the first-sighting clock tracks."""
    return {
        key_for(condition, row.unit_id)
        for row in rows
        for condition in _raised(row, now, thresholds)
    }


def _alert(row: UnitRow, condition: Condition, now: datetime) -> Alert:
    unit = row.unit_id
    where = f" in {row.target_repository}" if row.target_repository else ""
    link = f"https://github.com/{row.target_repository}" if row.target_repository else None
    if condition is Condition.UNIT_FAILED:
        return Alert(
            f"Work unit failed: {unit}",
            f"Unit {unit}{where} is in state failed and nothing retries it. Open it on /review "
            "and read why it failed, then retry it or cancel it.",
            link,
        )
    if condition is Condition.UNIT_HELD:
        return Alert(
            f"Work unit held: {unit}",
            f"Unit {unit}{where} is in state {row.state} and waits for a person. Open it on "
            "/review, resolve what holds it, and release it or cancel it.",
            link,
        )
    if condition is Condition.STALLED:
        return Alert(
            f"Work unit stalled: {unit}",
            f"Unit {unit}{where} holds a claim whose lease lapsed and no worker renewed it. "
            "Find the worker that claimed it; if it is gone, fail the unit, then cancel it.",
            link,
        )
    if condition is Condition.DISPATCH_BLOCKED:
        cause = (
            "its failure-signature breaker is open"
            if row.breaker_open
            else f"its latest dispatch is {row.latest_dispatch_status}"
        )
        return Alert(
            f"Dispatch blocked: {unit}",
            f"Unit {unit}{where} cannot start: {cause}. Read the dispatch record on /review "
            "and fix the cause, then dispatch the unit again or fail it and cancel it.",
            link,
        )
    if condition is Condition.RUN_UNCLAIMED:
        waited = (
            int((now - row.latest_dispatch_at).total_seconds() // 3600)
            if row.latest_dispatch_at
            else 0
        )
        return Alert(
            f"Run unclaimed: {unit}",
            f"Unit {unit}{where} was dispatched {waited} hours ago and no runner claimed it. "
            "Look for the run in the repository's Actions. If it is not there, fail the unit, "
            "then cancel it.",
            link,
        )
    raise ValueError(f"{condition} is not a unit condition")


def firing(
    rows: Iterable[UnitRow],
    first_seen: Mapping[str, datetime],
    known: Set[str],
    now: datetime,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Alert]:
    """The alerts to raise now, by key. `first_seen` is each raised key's first sighting; a key
    with none counts as seen this instant."""
    alerts: dict[str, Alert] = {}
    for row in rows:
        for condition in _raised(row, now, thresholds):
            key = key_for(condition, row.unit_id)
            if key in known:
                continue
            if (
                condition is Condition.DISPATCH_BLOCKED
                and not row.breaker_open
                and now - first_seen.get(key, now)
                < timedelta(hours=thresholds.dispatch_blocked_hours)
            ):
                continue
            alerts[key] = _alert(row, condition, now)
    return alerts
