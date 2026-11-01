"""Which execution conditions raise an alert, and which a sibling lane already owns."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from bump_driver.cli import SYSTEM_ACTOR as BUMP_SYSTEM_ACTOR
from bump_driver.cli import run_pass
from bump_driver.client import CallError, GitHub, Orchestrator
from execution_watch.decide import (
    SYSTEM_ACTOR,
    Condition,
    StatusRow,
    Thresholds,
    conditions,
    firing,
    owned_elsewhere,
    raised_keys,
)
from rotation_decomposer.alerts import AWAITING
from rotation_decomposer.cli import _existing as rotation_existing

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
REPOSITORY = "AlobarQuest/orchestrator"
BUMP = "dependency-update"
THRESHOLDS = Thresholds()


def row(**changes: Any) -> StatusRow:
    base = StatusRow(
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


def unowned(**changes: Any) -> StatusRow:
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


def test_run_unclaimed_names_both_exits_and_that_failing_is_a_system_call() -> None:
    overdue = unowned(latest_dispatch_status="dispatched", latest_dispatch_at=hours_ago(3))
    (alert,) = firing([overdue], {}, set(), NOW, THRESHOLDS).values()

    assert "restore it and dispatch the unit again" in alert.message
    assert "SYSTEM commands/fail route, then cancel it" in alert.message


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
    def __init__(self, shape: StatusRow, history: list[dict[str, Any]]) -> None:
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


def _bump_raises(shape: StatusRow) -> bool:
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


# Admission: what waits on a person before a unit runs.

SUBJECT = "0c7a484c-3333-4333-8333-333333333333"
WAITING_KEY = f"admission-waiting:proposal:{SUBJECT}"
NOT_APPLIED_KEY = f"admission-policy-not-applied:{SUBJECT}"


def waiting(kind: str = "proposal", **changes: Any) -> StatusRow:
    """A row that isn't a unit, as the server serves it: the unit flags are empty."""
    base = dict(
        unit_id=None,
        state="proposed",
        change_class=None,
        standing=False,
        target_repository=None,
        granted=False,
        subject_kind=kind,
        subject_id=SUBJECT,
        waiting=True,
    )
    return row(**(base | changes))


def gate(**changes: Any) -> StatusRow:
    """A unit at an admission gate, which the server marks `waiting`."""
    return unowned(
        state="awaiting_review", waiting=True, subject_id=str(uuid.UUID(int=1)), **changes
    )


def covered_bump_proposal(**changes: Any) -> StatusRow:
    return replace(
        waiting(
            change_class=BUMP,
            standing=True,
            granted=True,
            target_repository=REPOSITORY,
            proposed_by=SYSTEM_ACTOR,
        ),
        **changes,
    )


@pytest.mark.parametrize(
    ("kind", "state"),
    [
        ("proposal", "proposed"),
        ("proposal", "revision_required"),
        ("package", "registered"),
        ("staged-intake", "staged"),
    ],
)
def test_a_row_that_is_not_a_unit_raises_only_its_wait(kind: str, state: str) -> None:
    """A sent-back proposal's state is a unit's held state too, and it isn't a held unit."""
    subject = waiting(kind, state=state, overdue=True, lease_lapsed=True)
    assert conditions(subject, NOW) == {Condition.ADMISSION_WAITING}
    assert raised_keys([subject], NOW, THRESHOLDS) == {f"admission-waiting:{kind}:{SUBJECT}"}


def test_a_wait_is_raised_from_the_first_pass_and_fires_only_overdue() -> None:
    """Raised on presence: `known & raised` would otherwise prune a seeded key before overdue."""
    young = waiting()
    assert raised_keys([young], NOW, THRESHOLDS) == {WAITING_KEY}
    assert firing([young], {}, set(), NOW, THRESHOLDS) == {}
    assert list(firing([replace(young, overdue=True)], {}, set(), NOW, THRESHOLDS)) == [WAITING_KEY]


def test_a_known_wait_stays_silent_past_overdue() -> None:
    assert firing([waiting(overdue=True)], {}, {WAITING_KEY}, NOW, THRESHOLDS) == {}


def test_a_unit_at_a_gate_is_keyed_by_its_kind_and_stays_silent_until_overdue() -> None:
    key = f"admission-waiting:unit:{uuid.UUID(int=1)}"
    assert raised_keys([gate()], NOW, THRESHOLDS) == {key}
    assert firing([gate()], {}, set(), NOW, THRESHOLDS) == {}
    assert list(firing([gate(overdue=True)], {}, set(), NOW, THRESHOLDS)) == [key]


def test_a_unit_the_server_says_is_not_waiting_raises_no_wait() -> None:
    assert conditions(unowned(state="draft", overdue=True), NOW) == set()


def test_a_rotation_unit_at_a_gate_raises_nothing() -> None:
    assert conditions(row(is_rotation=True, state="awaiting_review", waiting=True), NOW) == set()


def test_policy_not_applied_needs_no_wait() -> None:
    refused = waiting(policy_not_applied="envelope_not_recognised")
    assert conditions(refused, NOW) == {
        Condition.ADMISSION_WAITING,
        Condition.ADMISSION_POLICY_NOT_APPLIED,
    }
    assert list(firing([refused], {}, set(), NOW, THRESHOLDS)) == [NOT_APPLIED_KEY]


def test_a_person_s_own_proposal_raises_no_policy_alert() -> None:
    """A bump records this for every proposal a person makes; policy was never going to act."""
    theirs = waiting(policy_not_applied="proposer_not_system")
    assert conditions(theirs, NOW) == {Condition.ADMISSION_WAITING}


def test_a_rotation_proposal_refused_by_policy_passes_the_rotation_early_return() -> None:
    refused = waiting(rotation_package=True, policy_not_applied="attempts_over_budget")
    assert refused.is_rotation is False
    assert NOT_APPLIED_KEY in raised_keys([refused], NOW, THRESHOLDS)


def test_the_policy_alert_says_why_and_what_next() -> None:
    refused = waiting(policy_not_applied="attempts_over_budget")
    [alert] = firing([refused], {}, set(), NOW, THRESHOLDS).values()
    assert "attempts_over_budget" in alert.message
    assert f"/review/decomposition-proposals/{SUBJECT}" in alert.message


@pytest.mark.parametrize(
    ("subject", "href"),
    [
        (waiting(overdue=True), f"/review/decomposition-proposals/{SUBJECT}"),
        (waiting("package", overdue=True), f"/review/intakes/{SUBJECT}"),
        (waiting("staged-intake", overdue=True), f"/review/staged-intakes/{SUBJECT}"),
        (gate(overdue=True), f"/review/units/{uuid.UUID(int=1)}"),
    ],
    ids=["proposal", "package", "staged-intake", "unit"],
)
def test_each_wait_says_what_waits_and_the_next_step(subject: StatusRow, href: str) -> None:
    [alert] = firing([subject], {}, set(), NOW, THRESHOLDS).values()
    assert href in alert.message
    assert len(alert.message.split(". ")) >= 2
    for hedge in ("may ", "might", "possibly", "probably", "perhaps"):
        assert hedge not in alert.message.lower()


def test_a_superseded_or_sent_back_wait_says_what_a_person_does_next() -> None:
    [alert] = firing(
        [waiting(state="revision_required", overdue=True)], {}, set(), NOW, THRESHOLDS
    ).values()
    assert "sent back" in alert.message


# Who else tells a person about a proposal, held to what each lane really does.


class _ProposalOrchestrator(_Orchestrator):
    def __init__(self, shape: StatusRow) -> None:
        super().__init__(shape, [])

    def work(self, change_record_id: int) -> dict[str, Any]:
        return {"revision_ids": [REVISION], "units": []}

    def proposals(self, revision_id: str) -> list[dict[str, Any]]:
        return [{"id": SUBJECT, "state": self._shape.state, "proposed_by": self._shape.proposed_by}]


def _bump_raises_on_proposal(shape: StatusRow) -> bool:
    record = {
        "id": 170,
        "status": "approved",
        "change_class": BUMP,
        "target_repository": shape.target_repository,
        "package_id": "orchestrator-uv-httpx2",
        "package_revision": 1,
    }

    def refuse(*_: object) -> Any:
        raise AssertionError("a revision with a proposal is never proposed again")

    outcomes = run_pass(
        [record],
        cast(Orchestrator, _ProposalOrchestrator(shape)),
        cast(GitHub, object()),
        {REPOSITORY.lower(): REPOSITORY} if shape.granted else {},
        refuse,
        refuse,
        now=NOW,
    )
    return any(outcome.key is not None for outcome in outcomes)


def test_this_lane_names_the_bump_lane_s_identity() -> None:
    assert SYSTEM_ACTOR == BUMP_SYSTEM_ACTOR


@pytest.mark.parametrize("state", ["proposed", "revision_required"])
@pytest.mark.parametrize("proposer", [SYSTEM_ACTOR, "devon"])
@pytest.mark.parametrize("standing", [True, False])
@pytest.mark.parametrize("granted", [True, False])
@pytest.mark.parametrize(
    "condition", [Condition.ADMISSION_WAITING, Condition.ADMISSION_POLICY_NOT_APPLIED]
)
def test_the_proposal_ownership_table_matches_the_bump_lane(
    state: str, proposer: str, standing: bool, granted: bool, condition: Condition
) -> None:
    shape = covered_bump_proposal(
        state=state, proposed_by=proposer, standing=standing, granted=granted
    )
    assert owned_elsewhere(shape, condition) == _bump_raises_on_proposal(shape)


@pytest.mark.parametrize("state", ["proposed", "revision_required"])
def test_a_rotation_proposal_s_wait_ownership_matches_its_lane(state: str) -> None:
    shape = waiting(state=state, rotation_package=True)
    outcome = rotation_existing("subject", [state])
    assert outcome is not None
    assert owned_elsewhere(shape, Condition.ADMISSION_WAITING) == (outcome.result == AWAITING)


def test_a_rotation_s_policy_refusal_is_this_lanes() -> None:
    refused = waiting(rotation_package=True, policy_not_applied="attempts_over_budget")
    assert not owned_elsewhere(refused, Condition.ADMISSION_POLICY_NOT_APPLIED)


def test_a_covered_bump_proposal_raises_nothing_here() -> None:
    covered = covered_bump_proposal(overdue=True, policy_not_applied="binding_violated")
    assert raised_keys([covered], NOW, THRESHOLDS) == set()


def test_a_units_wait_and_other_kinds_are_never_owned_elsewhere() -> None:
    assert not owned_elsewhere(
        replace(gate(), change_class=BUMP, standing=True), Condition.ADMISSION_WAITING
    )
    assert not owned_elsewhere(waiting("package"), Condition.ADMISSION_WAITING)


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
