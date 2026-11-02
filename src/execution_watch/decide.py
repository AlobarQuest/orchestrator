"""Which conditions this lane tells a person about. Pure: no I/O, no clock.

A unit's row (the orchestrator's `execution-status` read) can show five conditions, each keyed
`execution-<kind>:<unit>`. Two sibling lanes already alert on part of that ground, and this lane
stays silent on exactly what they cover (`owned_elsewhere`), so a person is told once:

* `work_watcher` alerts on a failed unit of a `standing` `dependency-update` package.
* `bump_driver` alerts on a `ready` or `revision_required` unit of a standing `dependency-update`
  package in a repository the member record grants: a unit in `revision_required`, a refused
  dispatch, and a run nobody claimed. A bump whose repository lost its grant falls out of that
  lane's selection, so this lane takes it over.

A rotation unit raises nothing here, the rotation executor owning its own alerts, with one
exception. A unit of any class in `submitted` or `verifying` raises `verification-stalled:<unit>`,
and fires only once the server says `verification_overdue`: nothing else watches that wait, for a
rotation unit or any other.

Any row may also be waiting at an admission gate. A unit at a gate, a staged intake, an undecomposed
package and an undecided proposal each raise `admission-waiting:<kind>:<id>` while the server says
`waiting`, and fire only once it says `overdue`, so a key `known` holds survives being young. A
proposal policy left for a person raises `admission-policy-not-applied:<proposal>` at once. Two
sibling lanes own part of that too:

* `bump_driver` alerts on a pending SYSTEM proposal in a repository it covers, and on why policy
  refused it. A proposal a person made, or sent back, is not in that lane's selection.
* `rotation_decomposer` alerts on a pending rotation proposal's wait, and not on a sent-back one, or
  on why policy refused one.

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
_PROPOSED: Final = "proposed"
# `bump_driver.cli.SYSTEM_ACTOR`, held equal by a test: this lane can't import a sibling lane.
SYSTEM_ACTOR: Final = "orchestrator-system"
# A bump records this for every proposal a person or a worker makes; policy never considered them.
_PROPOSER_NOT_SYSTEM: Final = "proposer_not_system"
# The server's `dead_letter.VERIFICATION_STATES`, held equal by the route's test.
VERIFICATION_STATES: Final = frozenset({"submitted", "verifying"})
# The capability a post-deploy unit is minted with; `image_release` verifies it.
POST_DEPLOY_CAPABILITY: Final = "post_deploy_verification"
SUBJECT_UNIT: Final = "unit"
SUBJECT_STAGED_INTAKE: Final = "staged-intake"
SUBJECT_PACKAGE: Final = "package"
SUBJECT_PROPOSAL: Final = "proposal"
# The server's `execution_status.SUBJECT_*`, held equal by the route's test.
SUBJECT_KINDS: Final = frozenset(
    {SUBJECT_UNIT, SUBJECT_STAGED_INTAKE, SUBJECT_PACKAGE, SUBJECT_PROPOSAL}
)
_REVIEW_PATHS: Final = {
    SUBJECT_UNIT: "/review/units/",
    SUBJECT_STAGED_INTAKE: "/review/staged-intakes/",
    SUBJECT_PACKAGE: "/review/intakes/",
    SUBJECT_PROPOSAL: "/review/decomposition-proposals/",
}
_WAIT_STEPS: Final = {
    SUBJECT_UNIT: "Decide it there: approve its envelope, or adjudicate and complete it",
    SUBJECT_STAGED_INTAKE: "Confirm it there to register it, or withdraw it",
    SUBJECT_PACKAGE: "Have it broken into work units, or close this alert to leave it",
    SUBJECT_PROPOSAL: "Approve, reject, or send it back there",
}


class Condition(StrEnum):
    UNIT_FAILED = "execution-unit-failed"
    UNIT_HELD = "execution-unit-held"
    STALLED = "execution-stalled"
    DISPATCH_BLOCKED = "execution-dispatch-blocked"
    RUN_UNCLAIMED = "execution-run-unclaimed"
    WATCH_UNREAD = "execution-watch-unread"
    ADMISSION_WAITING = "admission-waiting"
    ADMISSION_POLICY_NOT_APPLIED = "admission-policy-not-applied"
    VERIFICATION_STALLED = "verification-stalled"


@dataclass(frozen=True)
class StatusRow:
    """One row of the orchestrator's execution-status read. A row that isn't a unit has no
    `unit_id`, and the client reads its empty unit flags as false."""

    unit_id: str | None
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
    subject_kind: str = SUBJECT_UNIT
    subject_id: str = ""
    entered_at: datetime | None = None
    waiting: bool = False
    overdue: bool = False
    policy_not_applied: str | None = None
    proposed_by: str | None = None
    rotation_package: bool = False
    verification_overdue: bool = False


@dataclass(frozen=True)
class Thresholds:
    dispatch_blocked_hours: int = 1
    run_unclaimed_hours: int = 2
    unread_hours: int = 2


DEFAULT_THRESHOLDS: Final = Thresholds()


def key_for(condition: Condition, unit_id: str) -> str:
    return alert_key(condition, unit_id)


def _row_key(row: StatusRow, condition: Condition) -> str:
    if condition is Condition.ADMISSION_WAITING:
        return alert_key(condition, row.subject_kind, row.subject_id)
    if condition is Condition.ADMISSION_POLICY_NOT_APPLIED:
        return alert_key(condition, row.subject_id)
    return alert_key(condition, str(row.unit_id))


def _admission_conditions(row: StatusRow) -> set[Condition]:
    found: set[Condition] = set()
    if row.waiting:
        found.add(Condition.ADMISSION_WAITING)
    if row.policy_not_applied not in (None, _PROPOSER_NOT_SYSTEM):
        found.add(Condition.ADMISSION_POLICY_NOT_APPLIED)
    return found


def conditions(
    row: StatusRow, now: datetime, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> set[Condition]:
    """What is true of the unit right now, whoever alerts on it. Dispatch conditions need a `ready`
    unit: once a run is claimed, an older refusal or a GitHub error no longer stops it. The
    one-hour wait on a blocked dispatch is a first-sighting clock and belongs to `firing`; an
    open breaker only skips that wait."""
    in_verification = row.subject_kind == SUBJECT_UNIT and row.state in VERIFICATION_STATES
    stalled = {Condition.VERIFICATION_STALLED} if in_verification else set()
    if row.is_rotation:
        return stalled
    found = _admission_conditions(row) | stalled
    if row.subject_kind != SUBJECT_UNIT:
        return found
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


def _is_covered_bump(row: StatusRow) -> bool:
    return (
        row.change_class == BUMP_CHANGE_CLASS
        and row.standing
        and row.granted
        and row.target_repository is not None
    )


def _is_bump_lanes_proposal(row: StatusRow) -> bool:
    return (
        row.subject_kind == SUBJECT_PROPOSAL
        and row.state == _PROPOSED
        and row.proposed_by == SYSTEM_ACTOR
        and _is_covered_bump(row)
    )


def owned_elsewhere(row: StatusRow, condition: Condition) -> bool:
    """Whether a sibling lane already alerts on this condition for this subject."""
    if condition is Condition.ADMISSION_WAITING:
        return _is_bump_lanes_proposal(row) or (
            row.subject_kind == SUBJECT_PROPOSAL and row.state == _PROPOSED and row.rotation_package
        )
    if condition is Condition.ADMISSION_POLICY_NOT_APPLIED:
        return _is_bump_lanes_proposal(row)
    if condition is Condition.UNIT_FAILED:
        return row.change_class == BUMP_CHANGE_CLASS and row.standing
    if condition is Condition.UNIT_HELD:
        return _is_covered_bump(row) and row.state == _REVISION_REQUIRED
    if condition in (Condition.DISPATCH_BLOCKED, Condition.RUN_UNCLAIMED):
        return _is_covered_bump(row) and row.state == _READY
    return False


def _raised(row: StatusRow, now: datetime, thresholds: Thresholds) -> list[Condition]:
    return sorted(
        condition
        for condition in conditions(row, now, thresholds)
        if not owned_elsewhere(row, condition)
    )


def raised_keys(rows: Iterable[StatusRow], now: datetime, thresholds: Thresholds) -> set[str]:
    """Every key raised now, before any wait: what the first-sighting clock tracks."""
    return {_row_key(row, condition) for row in rows for condition in _raised(row, now, thresholds)}


def _admission_alert(row: StatusRow, condition: Condition) -> Alert:
    href = f"{_REVIEW_PATHS[row.subject_kind]}{row.subject_id}"
    what = f"{row.subject_kind.replace('-', ' ')} {row.subject_id}"
    if condition is Condition.ADMISSION_POLICY_NOT_APPLIED:
        return Alert(
            f"Policy did not approve a proposal: {row.subject_id}",
            f"Policy left proposal {row.subject_id} for a person: {row.policy_not_applied}. "
            f"Read it on {href}, then approve, reject, or send it back.",
            None,
        )
    since = row.entered_at.date().isoformat() if row.entered_at else "an unread time"
    step = (
        "It was sent back and no new proposal has followed. Propose a new breakdown, or reject it"
        if row.state == "revision_required"
        else _WAIT_STEPS[row.subject_kind]
    )
    return Alert(
        f"Waiting on a person: {what}",
        f"The {what} has waited since {since}. {step}, on {href}.",
        None,
    )


def _alert(row: StatusRow, condition: Condition, now: datetime) -> Alert:
    if condition in (Condition.ADMISSION_WAITING, Condition.ADMISSION_POLICY_NOT_APPLIED):
        return _admission_alert(row, condition)
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
            f"Unit {unit}{where} was dispatched {waited} hours ago and no runner claimed it: "
            "its run failed to start or never appeared. Look for the run in the repository's "
            "Actions. If the repository's member grant was withdrawn, restore it and dispatch "
            "the unit again. Otherwise retire the unit: /review has no fail control, so fail "
            "the unit through the SYSTEM commands/fail route, then cancel it.",
            link,
        )
    if condition is Condition.VERIFICATION_STALLED:
        since = row.entered_at.date().isoformat() if row.entered_at else "an unread time"
        return Alert(
            f"Verification stalled: {unit}",
            f"Unit {unit}{where} has been in state {row.state} since {since} and no verifier "
            f"finished it. {_verification_steps(row)}",
            link,
        )
    raise ValueError(f"{condition} is not a unit condition")


def _verification_steps(row: StatusRow) -> str:
    """Who finishes a stuck unit. `/verify` refuses nothing for a post-deploy unit, so its
    alert names the lane that mints and verifies it rather than the VERIFIER route."""
    if row.change_class == POST_DEPLOY_CAPABILITY:
        return (
            "Open it on /review, then read the image-release lane's log for why "
            "`image_release` did not verify it."
        )
    return (
        "Open it on /review, then read ~/Library/Logs/unit-verifier.log for why. If that "
        "lane skips the unit, run its verification through the VERIFIER verify route."
    )


def firing(
    rows: Iterable[StatusRow],
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
            key = _row_key(row, condition)
            if key in known:
                continue
            if condition is Condition.ADMISSION_WAITING and not row.overdue:
                continue
            if condition is Condition.VERIFICATION_STALLED and not row.verification_overdue:
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
