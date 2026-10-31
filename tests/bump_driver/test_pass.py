"""One pass over fake services: what is decomposed, dispatched, merged and alerted, and never what
isn't a standing bump in a granted repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from bump_driver import cli
from bump_driver.cli import (
    FOUND,
    INCOMPLETE,
    UNCLAIMED_HOURS,
    act_refused_key,
    bump_failed_key,
    decompose_refused_key,
    exit_code,
    run_pass,
    run_unclaimed_key,
)
from bump_driver.client import CallError, GitHub, Orchestrator
from bump_driver.decompose import DecomposeRefused
from bump_driver.mirror import MirrorFailed, MirrorRefused

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
REPOSITORY = "AlobarQuest/orchestrator"
GRANTED = {REPOSITORY.lower(): REPOSITORY}
REVISION = "f962475a-1964-46df-81ac-2b3029dec2a7"
UNIT = "0c7a484c-1111-4111-8111-111111111111"
OTHER_UNIT = "0c7a484c-2222-4222-8222-222222222222"
RECORD: dict[str, Any] = {
    "id": 170,
    "status": "approved",
    "change_class": "dependency-update",
    "target_repository": REPOSITORY,
    "package_id": "orchestrator-uv-httpx2",
    "package_revision": 1,
}
FIELDS = {
    "package": "httpx2",
    "standing": True,
    "to_version": "2.16.0",
    "target_repo": REPOSITORY,
    "from_version": "2.13.1",
}
INTAKE: dict[str, Any] = {
    "id": REVISION,
    "package_id": "orchestrator-uv-httpx2",
    "profile": "dependency-update",
    "revision": 1,
    "enforcement_snapshot": {"profile_fields": FIELDS},
}
BUMP_AUTHORITY = {
    "change_class": "dependency-update",
    "constraints": {"target_repository": REPOSITORY},
}


def _dispatched(hours_ago: float = 0.5) -> dict[str, Any]:
    moment = NOW - timedelta(hours=hours_ago)
    return {"action": "dispatch.dispatched", "occurred_at": moment.isoformat()}


def _unit(unit: str, state: str, version: int = 3, **extra: Any) -> dict[str, Any]:
    return {
        "id": unit,
        "unit_key": "orchestrator-ac-001",
        "state": state,
        "version": version,
        "authority": BUMP_AUTHORITY,
        "pr_number": 506,
        **extra,
    }


class FakeOrchestrator:
    def __init__(self) -> None:
        self.intakes: dict[str, dict[str, Any]] = {REVISION: INTAKE}
        self.units: dict[str, dict[str, Any]] = {}
        self.proposal_rows: list[dict[str, Any]] = []
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.admissions: dict[str, dict[str, Any]] = {}
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.dispatched: list[tuple[str, dict[str, Any]]] = []
        self.merged: list[tuple[str, dict[str, Any]]] = []
        self.proposal_answer: dict[str, Any] = {"id": "p-1", "state": "approved"}
        self.dispatch_answer: dict[str, Any] = {"status": "dispatched", "reason_code": None}
        self.merge_answer: dict[str, Any] = {"status": "merged", "reason_code": None}
        self.refuse: dict[str, CallError] = {}
        # What the record's work lists for a unit, where it differs from the unit's own read.
        self.listed: dict[str, str] = {}
        self.unit_reads: list[str] = []

    def _refused(self, name: str) -> None:
        if name in self.refuse:
            raise self.refuse[name]

    def work(self, change_record_id: int) -> dict[str, Any]:
        self._refused("work")
        return {
            "revision_ids": [REVISION],
            "units": [
                {
                    "unit_id": u["id"],
                    "revision_id": REVISION,
                    "state": self.listed.get(u["id"], u["state"]),
                }
                for u in self.units.values()
            ],
        }

    def intake(self, revision_id: str) -> dict[str, Any]:
        self._refused("intake")
        return self.intakes[revision_id]

    def proposals(self, revision_id: str) -> list[dict[str, Any]]:
        return self.proposal_rows

    def propose(self, revision_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._refused("propose")
        self.posted.append((revision_id, body))
        return self.proposal_answer

    def unit(self, unit_id: str) -> dict[str, Any]:
        self.unit_reads.append(unit_id)
        return self.units[unit_id]

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return self.histories.get(unit_id, [])

    def dispatch(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._refused("dispatch")
        self.dispatched.append((unit_id, body))
        return self.dispatch_answer

    def merge_admission(self, unit_id: str) -> dict[str, Any]:
        return self.admissions.get(unit_id, {"satisfied": True, "refusals": []})

    def land(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._refused("merge")
        self.merged.append((unit_id, body))
        return self.merge_answer


class FakeGitHub:
    def __init__(self) -> None:
        self.state = "open"
        self.fail = False

    def pull_state(self, repository: str, number: int) -> str:
        if self.fail:
            raise CallError("GitHub unreachable")
        return self.state


class Generator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], Path]] = []
        self.mirrors: list[str] = []
        self.refusal: Exception | None = None
        self.mirror_error: Exception | None = None

    def mirror(self, repository: str) -> Path:
        if self.mirror_error is not None:
            raise self.mirror_error
        self.mirrors.append(repository)
        return Path("/mirror") / repository

    def generate(self, intake: dict[str, Any], repository: str, checkout: Path) -> dict[str, Any]:
        self.calls.append((str(intake["id"]), repository, intake, checkout))
        if self.refusal is not None:
            raise self.refusal
        return {"rationale": "generated", "proposed_units": [{"unit_key": "k"}]}


@pytest.fixture
def orch() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def generator() -> Generator:
    return Generator()


def _run(
    orch: FakeOrchestrator,
    github: FakeGitHub,
    generator: Generator,
    records: list[dict[str, Any]] | None = None,
    *,
    granted: dict[str, str] = GRANTED,
    dry_run: bool = False,
) -> list[cli.Outcome]:
    return run_pass(
        [RECORD] if records is None else records,
        cast(Orchestrator, orch),
        cast(GitHub, github),
        granted,
        generator.mirror,
        generator.generate,
        dry_run=dry_run,
        now=NOW,
    )


def _results(outcomes: list[cli.Outcome]) -> list[str]:
    return [o.result for o in outcomes]


# Decompose


def test_a_registered_intake_is_decomposed_and_posted_once(orch, github, generator) -> None:
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["approved"]
    [(revision, body)] = orch.posted
    assert revision == REVISION
    assert body["idempotency_key"] == f"bump-driver:{REVISION}"
    assert body["expected_version"] == 0
    assert body["rationale"] == "generated"
    assert generator.mirrors == [REPOSITORY]
    assert generator.calls[0][1] == REPOSITORY
    assert generator.calls[0][3] == Path("/mirror") / REPOSITORY
    assert exit_code(outcomes) == 0


def test_a_revision_with_units_is_not_decomposed(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "executing")
    _run(orch, github, generator)
    assert orch.posted == []
    assert generator.mirrors == []


@pytest.mark.parametrize(
    ("state", "result"),
    [("rejected", "declined"), ("revision_required", "declined"), ("approved", "superseded")],
)
def test_a_decided_proposal_is_never_re_proposed(orch, github, generator, state, result) -> None:
    orch.proposal_rows = [{"state": state, "proposed_by": "orchestrator-system"}]
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == [result]
    assert orch.posted == []
    assert generator.mirrors == []
    assert exit_code(outcomes) == 0
    assert outcomes[0].key is None


def test_a_rejected_proposal_beside_a_later_state_is_still_never_re_proposed(
    orch, github, generator
) -> None:
    orch.proposal_rows = [
        {"state": "rejected", "proposed_by": "orchestrator-system"},
        {"state": "revision_required", "proposed_by": "orchestrator-system"},
    ]
    assert _results(_run(orch, github, generator)) == ["declined"]
    assert orch.posted == []


def test_a_persons_waiting_proposal_is_awaited_and_never_alerted(orch, github, generator) -> None:
    orch.proposal_rows = [
        {
            "state": "proposed",
            "proposed_by": "devon",
            "policy_not_applied": {"reason": "binding_violated", "detail": []},
        }
    ]
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "awaiting-approval"
    assert outcome.key is None
    assert exit_code([outcome]) == 0
    assert orch.posted == []


@pytest.mark.parametrize("reason", ["binding_violated", "approval_failed"])
def test_the_lanes_own_unapproved_proposal_keeps_its_alert(orch, github, generator, reason) -> None:
    orch.proposal_rows = [
        {
            "state": "proposed",
            "proposed_by": "orchestrator-system",
            "policy_not_applied": {"reason": reason, "detail": ["x"]},
        }
    ]
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == decompose_refused_key(REPOSITORY, REVISION)
    assert reason in outcome.detail
    assert orch.posted == []


def test_a_proposal_policy_didnt_approve_is_refused_and_alerted(orch, github, generator) -> None:
    orch.proposal_answer = {
        "id": "p-1",
        "state": "proposed",
        "policy_not_applied": {"reason": "envelope_not_recognised", "detail": []},
    }
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == decompose_refused_key(REPOSITORY, REVISION)
    assert "envelope_not_recognised" in outcome.detail
    assert exit_code([outcome]) == FOUND


def test_a_generator_refusal_is_refused_and_alerted(orch, github, generator) -> None:
    generator.refusal = DecomposeRefused("decompose failed: scanner_unavailable")
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == decompose_refused_key(REPOSITORY, REVISION)
    assert "scanner_unavailable" in outcome.detail
    assert orch.posted == []


def test_a_refused_mirror_is_refused_and_alerted(orch, github, generator) -> None:
    generator.mirror_error = MirrorRefused("not a member")
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == decompose_refused_key(REPOSITORY, REVISION)


def test_a_mirror_that_could_not_be_fetched_is_unread(orch, github, generator) -> None:
    generator.mirror_error = MirrorFailed("git fetch exited 128")
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["unread"]
    assert exit_code(outcomes) == INCOMPLETE
    assert outcomes[0].repository == REPOSITORY


@pytest.mark.parametrize(("status", "result"), [(409, "refused"), (422, "refused")])
def test_a_proposal_the_orchestrator_refused_is_alerted(
    orch, github, generator, status, result
) -> None:
    orch.refuse["propose"] = CallError("refused", status)
    [outcome] = _run(orch, github, generator)
    assert outcome.result == result
    assert outcome.key == decompose_refused_key(REPOSITORY, REVISION)


@pytest.mark.parametrize("status", [500, 503, None])
def test_a_proposal_nothing_answered_is_unread(orch, github, generator, status) -> None:
    orch.refuse["propose"] = CallError("down", status)
    assert _results(_run(orch, github, generator)) == ["unread"]


def test_a_dry_run_proposes_nothing_and_touches_no_mirror(orch, github, generator) -> None:
    assert _results(_run(orch, github, generator, dry_run=True)) == ["would-propose"]
    assert orch.posted == []
    assert generator.mirrors == []


# Selection


@pytest.mark.parametrize(
    "change",
    [
        {"status": "pending"},
        {"status": "wontfix"},
        {"change_class": "credential-rotation"},
        {"change_class": None},
        {"target_repository": "AlobarQuest/brain"},
        {"target_repository": None},
        {"package_id": "rotation-example"},
        {"package_id": "brain-uv-httpx2"},
    ],
)
def test_a_record_outside_the_selection_is_never_read(orch, github, generator, change) -> None:
    orch.refuse["work"] = CallError("must not be asked")
    assert _run(orch, github, generator, [{**RECORD, **change}]) == []
    assert generator.mirrors == []


def test_a_repository_the_member_record_doesnt_grant_is_never_read(orch, github, generator) -> None:
    orch.refuse["work"] = CallError("must not be asked")
    assert _run(orch, github, generator, granted={}) == []


def test_the_grant_is_matched_without_case_and_acted_on_in_its_spelling(
    orch, github, generator
) -> None:
    record = {**RECORD, "target_repository": REPOSITORY.upper()}
    orch.intakes[REVISION] = {
        **INTAKE,
        "enforcement_snapshot": {"profile_fields": {**FIELDS, "target_repo": REPOSITORY.upper()}},
    }
    # The package id reads the repository's name, so it is matched case-folded too.
    _run(orch, github, generator, [record])
    assert generator.mirrors == [REPOSITORY]


@pytest.mark.parametrize(
    "change",
    [
        {"profile": "software-delivery"},
        {"profile": None},
        {"revision": 2},
        {"enforcement_snapshot": {"profile_fields": {**FIELDS, "standing": False}}},
        {"enforcement_snapshot": {"profile_fields": {**FIELDS, "standing": "true"}}},
        {"enforcement_snapshot": {"profile_fields": {**FIELDS, "target_repo": "Other/repo"}}},
        {"enforcement_snapshot": {"profile_fields": None}},
        {"enforcement_snapshot": {}},
    ],
)
def test_a_revision_that_is_no_standing_bump_is_left_alone(orch, github, generator, change) -> None:
    orch.intakes[REVISION] = {**INTAKE, **change}
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "completed")
    assert _run(orch, github, generator) == []
    assert orch.posted == []
    assert orch.dispatched == []
    assert orch.merged == []


# Dispatch


def test_a_ready_bump_unit_is_dispatched(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready", version=4)
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["dispatched"]
    [(unit, body)] = orch.dispatched
    assert unit == UNIT
    assert body == {"idempotency_key": f"bump-driver:dispatch:{UNIT}:4", "expected_version": 4}
    assert "runner_attempt" not in body


def test_a_unit_dispatched_and_unclaimed_is_running_and_not_dispatched_again(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.histories[UNIT] = [_dispatched()]
    assert _results(_run(orch, github, generator)) == ["running"]
    assert orch.dispatched == []


def test_a_ready_unit_whose_last_dispatch_was_claimed_is_dispatched_again(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready", version=9)
    orch.histories[UNIT] = [
        _dispatched(),
        {"action": "work_unit.transition", "to_state": "claimed"},
    ]
    _run(orch, github, generator)
    assert orch.dispatched[0][1]["idempotency_key"] == f"bump-driver:dispatch:{UNIT}:9"


def test_a_skipped_dispatch_takes_a_new_key_or_it_would_replay_the_skip_for_ever(
    orch, github, generator
) -> None:
    """The record a skip writes holds its key, and a skip moves no version."""
    orch.units[UNIT] = _unit(UNIT, "ready", version=4)
    orch.histories[UNIT] = [{"action": "dispatch.skipped"}, {"action": "dispatch.blocked"}]
    _run(orch, github, generator)
    assert orch.dispatched[0][1]["idempotency_key"] == f"bump-driver:dispatch:{UNIT}:4:2"


@pytest.mark.parametrize("reason", ["dispatch_disabled", "outside_change_window"])
def test_a_switched_off_dispatch_waits(orch, github, generator, reason) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.dispatch_answer = {"status": "skipped", "reason_code": reason}
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["waiting"]
    assert exit_code(outcomes) == 0


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "blocked", "reason_code": "authority_approval_missing"},
        {"status": "skipped", "reason_code": "authority_verify_commands_invalid"},
        {"status": "failed", "reason_code": "github_api"},
    ],
)
def test_any_other_dispatch_answer_is_reported(orch, github, generator, answer) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.dispatch_answer = answer
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["refused"]
    assert answer["reason_code"] in outcomes[0].detail
    assert outcomes[0].key == act_refused_key(REPOSITORY, UNIT)
    assert exit_code(outcomes) == FOUND


def test_a_version_that_moved_waits_for_the_next_pass(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.refuse["dispatch"] = CallError("moved", 409, "version_conflict")
    assert _results(_run(orch, github, generator)) == ["waiting"]


def test_a_refused_dispatch_is_reported_and_an_unanswered_one_unread(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.refuse["dispatch"] = CallError("no", 403, "role_forbidden")
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == act_refused_key(REPOSITORY, UNIT)
    orch.refuse["dispatch"] = CallError("down", None)
    assert _results(_run(orch, github, generator)) == ["unread"]


@pytest.mark.parametrize("state", ["claimed", "executing"])
def test_one_run_per_repository_a_live_unit_holds_the_rest(orch, github, generator, state) -> None:
    orch.units[UNIT] = _unit(UNIT, state)
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    assert _results(_run(orch, github, generator)) == ["busy"]
    assert orch.dispatched == []


def test_one_run_per_repository_a_pass_dispatches_one_unit(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    assert sorted(_results(_run(orch, github, generator))) == ["busy", "dispatched"]
    assert len(orch.dispatched) == 1


@pytest.mark.parametrize("pending", [UNIT, OTHER_UNIT], ids=["sorts-first", "sorts-last"])
def test_one_run_per_repository_an_unclaimed_dispatch_holds_the_rest(
    orch, github, generator, pending
) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    orch.histories[pending] = [_dispatched()]
    assert sorted(_results(_run(orch, github, generator))) == ["busy", "running"]
    assert orch.dispatched == []


def test_a_run_unclaimed_past_the_limit_alerts_and_keeps_holding(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    orch.histories[UNIT] = [_dispatched(UNCLAIMED_HOURS)]
    outcomes = _run(orch, github, generator)
    assert sorted(_results(outcomes)) == ["busy", "unclaimed"]
    assert orch.dispatched == []
    alert = next(o for o in outcomes if o.result == "unclaimed")
    assert alert.key == run_unclaimed_key(REPOSITORY, UNIT)
    assert "orchestrator deploys" in alert.detail
    assert "again or retire it" in alert.detail
    assert exit_code(outcomes) == FOUND


def test_a_run_just_inside_the_limit_is_still_running(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.histories[UNIT] = [_dispatched(UNCLAIMED_HOURS - 0.01)]
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "running"
    assert outcome.key is None


def test_the_limit_is_two_hours() -> None:
    assert UNCLAIMED_HOURS == 2


@pytest.mark.parametrize("failing", ["unit", "history"])
@pytest.mark.parametrize("broken", [UNIT, OTHER_UNIT], ids=["sorts-first", "sorts-last"])
def test_a_ready_unit_that_cant_be_read_holds_its_repository(
    orch, github, generator, failing, broken
) -> None:
    """It may be the one with a run starting, so nothing else there may start."""
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    original = getattr(orch, failing)

    def read(unit_id: str) -> Any:
        if unit_id == broken:
            raise CallError("503", 503)
        return original(unit_id)

    setattr(orch, failing, read)
    outcomes = _run(orch, github, generator)
    assert orch.dispatched == []
    assert sorted(_results(outcomes)) == ["busy", "unread"]
    assert exit_code(outcomes) == INCOMPLETE


def test_an_unreadable_history_entry_holds_its_repository(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    orch.histories[UNIT] = [{"action": "dispatch.dispatched", "occurred_at": "not a time"}]
    _run(orch, github, generator)
    assert orch.dispatched == []


def test_a_record_whose_work_cant_be_read_holds_its_repository(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    original = orch.work

    def work(change_record_id: int) -> dict[str, Any]:
        if change_record_id == 999:
            raise CallError("503", 503)
        return original(change_record_id)

    orch.work = work
    outcomes = _run(orch, github, generator, [{**RECORD, "id": 999}, RECORD])
    assert orch.dispatched == []
    assert sorted(_results(outcomes)) == ["busy", "unread"]


def test_a_record_that_goes_unreadable_after_decomposing_holds_its_repository(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    original = orch.work
    calls: list[int] = []

    def work(change_record_id: int) -> dict[str, Any]:
        calls.append(change_record_id)
        if change_record_id == 999 and calls.count(999) > 2:
            raise CallError("503", 503)
        return original(change_record_id)

    orch.work = work
    _run(orch, github, generator, [{**RECORD, "id": 999}, RECORD])
    assert orch.dispatched == []


def test_an_unreadable_repository_holds_only_itself(orch, github, generator) -> None:
    other = "AlobarQuest/brain"
    orch.units[UNIT] = _unit(UNIT, "ready")
    original = orch.work

    def work(change_record_id: int) -> dict[str, Any]:
        if change_record_id == 999:
            raise CallError("503", 503)
        return original(change_record_id)

    orch.work = work
    record = {**RECORD, "id": 999, "target_repository": other, "package_id": "brain-uv-httpx2"}
    granted = {**GRANTED, other.lower(): other}
    assert sorted(_results(_run(orch, github, generator, [record, RECORD], granted=granted))) == [
        "dispatched",
        "unread",
    ]


def test_a_blocked_dispatch_doesnt_hold_the_repository(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    orch.units[OTHER_UNIT] = _unit(OTHER_UNIT, "ready")
    orch.dispatch_answer = {"status": "blocked", "reason_code": "x"}
    _run(orch, github, generator)
    assert len(orch.dispatched) == 2


@pytest.mark.parametrize("state", ["ready", "completed", "failed", "revision_required"])
@pytest.mark.parametrize(
    "authority",
    [
        {"change_class": "app-change", "constraints": {"target_repository": REPOSITORY}},
        {"change_class": None, "constraints": {"rotation_unit": "mint"}},
        {
            "change_class": "dependency-update",
            "constraints": {"target_repository": REPOSITORY, "rotation_unit": "mint"},
        },
        {
            "change_class": "dependency-update",
            "constraints": {"target_repository": "AlobarQuest/brain"},
        },
        {"change_class": "dependency-update", "constraints": {}},
        {"change_class": "dependency-update"},
        "not-an-object",
    ],
)
def test_a_non_bump_unit_is_never_touched(orch, github, generator, state, authority) -> None:
    orch.units[UNIT] = _unit(UNIT, state, authority=authority)
    assert _run(orch, github, generator) == []
    assert orch.dispatched == []
    assert orch.merged == []


def test_a_dry_run_dispatches_nothing(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    assert _results(_run(orch, github, generator, dry_run=True)) == ["would-dispatch"]
    assert orch.dispatched == []


# Merge


def test_a_completed_unit_with_an_open_pull_request_is_merged(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed", version=7)
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["merged"]
    [(unit, body)] = orch.merged
    assert unit == UNIT
    assert body == {"idempotency_key": f"bump-driver:merge:{UNIT}", "expected_version": 7}
    assert exit_code(outcomes) == 0


def test_a_retired_records_completed_unit_is_still_merged(orch, github, generator) -> None:
    """`work_watcher` retires a record the moment its units complete, usually before this lane."""
    orch.units[UNIT] = _unit(UNIT, "completed")
    _run(orch, github, generator, [{**RECORD, "status": "resolved"}])
    assert len(orch.merged) == 1


def test_a_retired_record_is_never_decomposed(orch, github, generator) -> None:
    assert _run(orch, github, generator, [{**RECORD, "status": "resolved"}]) == []
    assert orch.posted == []


def test_the_unit_is_re_read_for_the_version_it_acts_on(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed", version=7)
    original = orch.merge_admission

    def moved(unit_id: str) -> dict[str, Any]:
        orch.units[UNIT] = _unit(UNIT, "completed", version=8)
        return original(unit_id)

    orch.merge_admission = moved
    _run(orch, github, generator)
    assert orch.merged[0][1]["expected_version"] == 8


def test_a_closed_pull_request_is_nothing_to_do(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    github.state = "closed"
    assert _run(orch, github, generator) == []
    assert orch.merged == []


def test_a_merged_pull_request_is_asked_so_already_merged_is_recorded(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    github.state = "merged"
    orch.merge_answer = {"status": "already_merged"}
    assert _run(orch, github, generator) == []
    assert len(orch.merged) == 1


def test_a_merged_pull_request_whose_unit_doesnt_qualify_is_nothing_to_do(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    github.state = "merged"
    orch.admissions[UNIT] = {"satisfied": False, "refusals": ["criteria_not_verifier_decided"]}
    assert _run(orch, github, generator) == []
    assert orch.merged == []


def test_a_dry_run_says_nothing_of_a_merged_pull_request(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    github.state = "merged"
    assert _run(orch, github, generator, dry_run=True) == []


def test_a_unit_with_no_bound_pull_request_is_nothing_to_do(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed", pr_number=None)
    assert _run(orch, github, generator) == []
    assert orch.merged == []


def test_a_pull_request_that_cant_be_read_is_unread(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    github.fail = True
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["unread"]
    assert exit_code(outcomes) == INCOMPLETE
    assert orch.merged == []


def test_the_off_switch_alone_waits(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.admissions[UNIT] = {"satisfied": False, "refusals": ["merge_policy_path_off"]}
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["waiting"]
    assert orch.merged == []
    assert exit_code(outcomes) == 0


def test_a_refusal_beside_the_off_switch_is_reported(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.admissions[UNIT] = {
        "satisfied": False,
        "refusals": ["merge_policy_path_off", "pr_binding_missing"],
    }
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["refused"]
    assert outcomes[0].key == act_refused_key(REPOSITORY, UNIT)
    assert "pr_binding_missing" in outcomes[0].detail
    assert orch.merged == []
    assert exit_code(outcomes) == FOUND


def test_any_other_refusal_is_reported(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.admissions[UNIT] = {"satisfied": False, "refusals": ["authority_approval_not_bound"]}
    assert _results(_run(orch, github, generator)) == ["refused"]


def test_an_unsatisfied_answer_with_no_refusal_is_reported(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.admissions[UNIT] = {"satisfied": False, "refusals": []}
    assert _results(_run(orch, github, generator)) == ["refused"]
    assert orch.merged == []


def test_a_pass_that_lost_the_race_reports_the_refusal(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.refuse["merge"] = CallError("refused", 409, "pr_merge_not_admissible")
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "refused"
    assert outcome.key == act_refused_key(REPOSITORY, UNIT)


def test_a_version_that_moved_before_the_merge_waits(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.refuse["merge"] = CallError("moved", 409, "version_conflict")
    assert _results(_run(orch, github, generator)) == ["waiting"]


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        ({"status": "merged"}, "merged"),
        ({"status": "already_merged"}, "merged"),
        ({"status": "refused", "reason_code": "merge_refused_by_remote:x"}, "refused"),
    ],
)
def test_the_merge_answer_is_classified(orch, github, generator, answer, result) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    orch.merge_answer = answer
    [outcome] = _run(orch, github, generator)
    assert outcome.result == result
    assert outcome.key == (act_refused_key(REPOSITORY, UNIT) if result == "refused" else None)


def test_a_dry_run_merges_nothing(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "completed")
    assert _results(_run(orch, github, generator, dry_run=True)) == ["would-merge"]
    assert orch.merged == []


# Failed units


def test_a_failed_unit_is_left_to_the_work_watchers_alert(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "failed")
    assert _run(orch, github, generator) == []


def test_a_unit_listed_failed_raises_nothing_whatever_its_own_read_says(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "revision_required")
    orch.listed[UNIT] = "failed"
    assert _run(orch, github, generator) == []


def test_a_unit_that_moved_to_failed_after_its_listing_raises_nothing(
    orch, github, generator
) -> None:
    orch.units[UNIT] = _unit(UNIT, "failed")
    orch.listed[UNIT] = "revision_required"
    assert _run(orch, github, generator) == []


def test_a_revision_required_unit_raises_its_key(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "revision_required")
    outcomes = _run(orch, github, generator)
    assert _results(outcomes) == ["failed"]
    assert outcomes[0].key == bump_failed_key(REPOSITORY, UNIT)
    assert exit_code(outcomes) == FOUND


@pytest.mark.parametrize(
    "state", ["cancelled", "submitted", "verifying", "awaiting_review", "blocked"]
)
def test_other_states_raise_nothing(orch, github, generator, state) -> None:
    orch.units[UNIT] = _unit(UNIT, state)
    assert _run(orch, github, generator) == []


# Isolation


def test_one_unreadable_record_doesnt_stop_the_pass(orch, github, generator) -> None:
    orch.units[UNIT] = _unit(UNIT, "ready")
    original = orch.work

    def work(change_record_id: int) -> dict[str, Any]:
        if change_record_id == 999:
            raise CallError("503", 503)
        return original(change_record_id)

    orch.work = work
    outcomes = _run(orch, github, generator, [{**RECORD, "id": 999}, RECORD])
    assert _results(outcomes) == ["unread", "busy"]
    assert exit_code(outcomes) == INCOMPLETE
    assert outcomes[0].repository == REPOSITORY


def test_an_unreadable_intake_is_unread_and_names_its_repository(orch, github, generator) -> None:
    orch.refuse["intake"] = CallError("503", 503)
    [outcome] = _run(orch, github, generator)
    assert outcome.result == "unread"
    assert outcome.repository == REPOSITORY


def test_unread_outranks_found() -> None:
    assert exit_code([cli.Outcome("a", "refused"), cli.Outcome("b", "unread")]) == INCOMPLETE
