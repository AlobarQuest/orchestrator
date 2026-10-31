"""A factory merge whose outcome is unknown gets settled, and a settled landing is its own (Part 5).

Mirrors `lane_act`'s settling on the unit table. The driver's pass runs here too, over the real
admission and the real act, because a settle branch nobody reaches proves nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from bump_driver import cli
from bump_driver.alerts import act_refused_key
from bump_driver.client import CallError, GitHub, Orchestrator
from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event, UnitPrMerge, WorkUnit
from orchestrator.services.landing.interfaces import (
    LANDING_OUTCOME_UNKNOWN,
    MERGE_REFUSED_BY_REMOTE,
    outcome_is_unknown,
)
from orchestrator.services.landing.pr_merge import (
    PR_MERGE_NOT_OPEN,
    GitHubGatewayError,
    MergeCommand,
    MergeOutcome,
    PullRequestState,
    _state_from_body,
    land_unit_pull_request,
)
from orchestrator.services.landing.pr_merge_admission import pr_merge_admission
from tests.services.change_record_doubles import no_record_source
from tests.services.estate_doubles import inert_source
from tests.services.test_pr_merge import FakeGateway
from tests.services.test_pr_merge_admission import IN_WINDOW, SYSTEM, FixedClock
from tests.services.test_pr_merge_bump_policy import (
    HEAD,
    REPOSITORY,
    AmbiguousGateway,
    _bump,
    _gate_events,
    _land,
)

COMMIT = "c" * 40
RECORD: dict[str, Any] = {
    "id": 9,
    "status": "resolved",
    "change_class": "dependency-update",
    "target_repository": REPOSITORY,
    "package_id": "orchestrator-uv-httpx2",
    "package_revision": 1,
}


KEY = "bump-driver:merge:settling"


def landed(head: str = HEAD) -> PullRequestState:
    return PullRequestState(
        base_ref="main",
        default_branch="main",
        open=False,
        landed=True,
        head_sha=head,
        merge_commit_sha=COMMIT,
    )


def closed() -> PullRequestState:
    return PullRequestState(base_ref="main", default_branch="main", open=False, landed=False)


def unknown_row(session: Session, name: str) -> tuple[WorkUnit, UnitPrMerge]:
    """A policy-path unit whose call was refused and whose confirming read failed."""
    unit = _bump(session, name)
    row = _land(
        session,
        unit,
        AmbiguousGateway(merge_error=GitHubGatewayError("merge_status", 405)),
        key=KEY,
    )
    assert outcome_is_unknown(row.status, row.reason_code)
    return unit, row


def stored(engine: Engine, unit: WorkUnit) -> UnitPrMerge | None:
    with Session(engine) as reader:
        return reader.scalar(select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id))


def event_actions(session: Session, unit: WorkUnit) -> list[str]:
    rows = session.execute(
        select(Event.action)
        .where(Event.subject_id == unit.id, Event.action.like("pr_merge.%"))
        .order_by(Event.occurred_at, Event.id)
    )
    return [action for (action,) in rows]


def test_a_replay_that_finds_it_landed_records_already_merged(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, row = unknown_row(migrated_session, "settles-landed")
    version = unit.version
    first_event = row.event_id
    gateway = FakeGateway(state=landed())

    record = _land(migrated_session, unit, gateway, enabled=False, key=KEY, as_served=True)

    assert gateway.merges == []
    fresh = stored(migrated_engine, unit)
    assert fresh is not None
    assert (fresh.status, fresh.reason_code) == ("already_merged", None)
    assert fresh.merge_commit_sha == COMMIT
    assert fresh.updated_at > fresh.created_at
    assert fresh.event_id != first_event
    event = migrated_session.get(Event, record.event_id)
    assert event is not None
    assert event.action == "pr_merge.already_merged"
    assert event.payload["status"] == "already_merged"
    assert event.payload["reason_code"] is None
    assert event.payload["merge_commit_sha"] == COMMIT
    assert event.payload["landing_inert"] is True
    assert event.payload["head_sha"] == HEAD
    assert event.payload["pr_merge_record_id"] == str(fresh.id)
    [gate] = _gate_events(migrated_session, unit)
    assert gate.payload["at"] == "merge"
    assert gate.payload["recognised_by"]
    assert gate.payload["policy_version"] is not None
    assert gate.payload["authority_fingerprint"] == unit.authority_fingerprint
    migrated_session.refresh(unit)
    assert (unit.state, unit.version) == ("completed", version)


@pytest.mark.parametrize("head", ["e" * 40, ""], ids=["another-head", "no-head"])
def test_a_row_settled_at_any_other_head_carries_no_stamp(
    migrated_session: Session, migrated_engine: Engine, head: str
) -> None:
    """Somebody pushed and landed after our call failed: a tree no term read."""
    unit, _ = unknown_row(migrated_session, f"settles-other-{head[:1]}")

    record = _land(migrated_session, unit, FakeGateway(state=landed(head)), key=KEY, as_served=True)

    fresh = stored(migrated_engine, unit)
    assert fresh is not None
    assert (fresh.status, fresh.merge_commit_sha) == ("already_merged", COMMIT)
    event = migrated_session.get(Event, record.event_id)
    assert event is not None
    assert event.action == "pr_merge.already_merged"
    assert event.payload["landing_inert"] is False
    assert event.payload["policy_path"] is None
    assert _gate_events(migrated_session, unit) == []


@pytest.mark.parametrize("head", ["e" * 40, ""], ids=["another-head", "no-head"])
def test_a_reconciling_read_at_any_other_head_carries_no_stamp(
    migrated_session: Session, head: str
) -> None:
    unit = _bump(migrated_session, f"reconciled-other-{head[:1]}")
    gateway = FakeGateway(
        merge_error=GitHubGatewayError("merge_status", 405), state_after_refusal=landed(head)
    )

    record = _land(migrated_session, unit, gateway)

    assert (record.status, record.merge_commit_sha) == ("already_merged", COMMIT)
    event = migrated_session.get(Event, record.event_id)
    assert event is not None
    assert event.payload["landing_inert"] is False
    assert event.payload["policy_path"] is None
    assert _gate_events(migrated_session, unit) == []


def test_a_settled_row_is_replayed_and_settles_once(migrated_session: Session) -> None:
    unit, _ = unknown_row(migrated_session, "settles-once")
    _land(migrated_session, unit, FakeGateway(state=landed()), key=KEY, as_served=True)
    gateway = FakeGateway(state=landed())

    record = _land(migrated_session, unit, gateway, key=KEY, as_served=True)

    assert record.status == "already_merged"
    assert gateway.reads == []
    assert len(_gate_events(migrated_session, unit)) == 1


def test_a_replay_that_finds_it_unlanded_removes_the_row_and_acts_normally(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, row = unknown_row(migrated_session, "settles-unlanded")
    removed = row.id
    gateway = FakeGateway(outcome=MergeOutcome(True, COMMIT, 200))

    record = _land(migrated_session, unit, gateway, key=KEY)

    assert len(gateway.merges) == 1
    fresh = stored(migrated_engine, unit)
    assert fresh is not None
    assert fresh.id != removed
    assert (fresh.status, fresh.merge_commit_sha) == ("merged", COMMIT)
    assert record.id == fresh.id
    assert event_actions(migrated_session, unit) == [
        "pr_merge.refused",
        "pr_merge.unlanded",
        "pr_merge.merged",
    ]
    assert len(_gate_events(migrated_session, unit)) == 1


def test_a_removal_stands_when_the_terms_then_refuse(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, _ = unknown_row(migrated_session, "removal-stands")
    gateway = FakeGateway()
    gateway.serve_the_runners_pull_request(unit, REPOSITORY, HEAD)

    with pytest.raises(DomainError) as refused:
        _land(migrated_session, unit, gateway, enabled=False, key=KEY, as_served=True)

    assert refused.value.code == "pr_merge_not_admissible"
    assert stored(migrated_engine, unit) is None
    assert gateway.merges == []
    with Session(migrated_engine) as reader:
        assert reader.scalar(select(Event.id).where(Event.action == "pr_merge.unlanded"))


def test_a_closed_pull_request_that_never_landed_loses_its_row(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, _ = unknown_row(migrated_session, "closed-unlanded")

    with pytest.raises(DomainError) as refused:
        _land(migrated_session, unit, FakeGateway(state=closed()), key=KEY, as_served=True)

    assert refused.value.code == PR_MERGE_NOT_OPEN
    assert stored(migrated_engine, unit) is None


def test_a_replay_whose_read_fails_leaves_the_row(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, row = unknown_row(migrated_session, "read-fails")
    reason = row.reason_code
    gateway = FakeGateway(read_error=GitHubGatewayError("read_status", 502))

    record = _land(migrated_session, unit, gateway, key=KEY, as_served=True)

    assert (record.status, record.reason_code) == ("refused", reason)
    assert gateway.merges == []
    fresh = stored(migrated_engine, unit)
    assert fresh is not None
    assert fresh.id == row.id
    assert event_actions(migrated_session, unit) == ["pr_merge.refused"]


def test_a_row_the_remote_declined_is_terminal(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "declined")
    row = _land(migrated_session, unit, FakeGateway(outcome=MergeOutcome(False, None, 200)))
    assert row.reason_code == MERGE_REFUSED_BY_REMOTE
    gateway = FakeGateway(state=landed())

    assert _land(migrated_session, unit, gateway, as_served=True).id == row.id
    assert gateway.reads == []


def test_a_key_a_removal_spent_lands_a_second_time(migrated_session: Session) -> None:
    """A second unknown row under the driver's one key, settled unlanded, then landed."""
    unit, _ = unknown_row(migrated_session, "twice")
    ambiguous = AmbiguousGateway(merge_error=GitHubGatewayError("merge_status", 405))
    gateway = FakeGateway()
    gateway.serve_the_runners_pull_request(unit, REPOSITORY, HEAD)
    ambiguous._state, ambiguous._files = gateway._state, gateway._files

    row = _land(migrated_session, unit, ambiguous, key=KEY, as_served=True)
    assert outcome_is_unknown(row.status, row.reason_code)

    record = _land(migrated_session, unit, FakeGateway(), key=KEY)
    assert record.status == "merged"


def test_the_answer_names_an_unknown_row_until_it_settles(migrated_session: Session) -> None:
    unit, _ = unknown_row(migrated_session, "answer")

    def answer():
        return pr_merge_admission(
            migrated_session,
            unit.id,
            inert_source(),
            no_record_source(),
            FixedClock(IN_WINDOW),
            bump_merge_enabled=True,
        )

    assert LANDING_OUTCOME_UNKNOWN in answer().refusals
    assert answer().satisfied is False
    _land(migrated_session, unit, FakeGateway(state=landed()), key=KEY, as_served=True)
    assert LANDING_OUTCOME_UNKNOWN not in answer().refusals


def test_only_a_landed_pull_request_names_a_merge_commit() -> None:
    body = {
        "base": {"ref": "main", "repo": {"default_branch": "main"}},
        "state": "open",
        "merged": False,
        "merge_commit_sha": "test-merge",
    }
    assert _state_from_body(body).merge_commit_sha is None
    assert _state_from_body({**body, "merged": True}).merge_commit_sha == "test-merge"
    assert (
        _state_from_body({**body, "merged": True, "merge_commit_sha": 7}).merge_commit_sha is None
    )


# The driver's own pass, over the real answer and the real act.


class ServedOrchestrator:
    """The driver's client, answered by the services a route calls."""

    def __init__(self, session: Session, unit: WorkUnit, gateway: Any) -> None:
        self.session, self.unit_row, self.gateway = session, unit, gateway
        self.enabled = True

    def work(self, change_record_id: int) -> dict[str, Any]:
        return {
            "revision_ids": ["rev"],
            "units": [
                {"unit_id": str(self.unit_row.id), "revision_id": "rev", "state": "completed"}
            ],
        }

    def intake(self, revision_id: str) -> dict[str, Any]:
        return {
            "revision": 1,
            "profile": "dependency-update",
            "enforcement_snapshot": {
                "profile_fields": {"standing": True, "target_repo": REPOSITORY}
            },
            "package_id": RECORD["package_id"],
        }

    def unit(self, unit_id: str) -> dict[str, Any]:
        unit = self.session.get(WorkUnit, self.unit_row.id)
        assert unit is not None
        return {
            "id": unit_id,
            "state": unit.state,
            "version": unit.version,
            "pr_number": 7,
            "authority": {
                "change_class": "dependency-update",
                "constraints": {"target_repository": REPOSITORY},
            },
        }

    def merge_admission(self, unit_id: str) -> dict[str, Any]:
        answer = pr_merge_admission(
            self.session,
            self.unit_row.id,
            inert_source(),
            no_record_source(),
            FixedClock(IN_WINDOW),
            bump_merge_enabled=self.enabled,
        )
        return {"satisfied": answer.satisfied, "refusals": list(answer.refusals)}

    def land(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            record = land_unit_pull_request(
                self.session,
                MergeCommand(
                    unit_id=self.unit_row.id,
                    actor=SYSTEM,
                    idempotency_key=body["idempotency_key"],
                    expected_version=body["expected_version"],
                ),
                self.gateway,
                inert_source(),
                no_record_source(),
                clock=FixedClock(IN_WINDOW),
                bump_merge_enabled=self.enabled,
            )
        except DomainError as error:
            raise CallError(str(error), 409, error.code) from error
        return {"status": record.status, "reason_code": record.reason_code}


class Pull:
    def __init__(self, state: str) -> None:
        self.state = state

    def pull_state(self, repository: str, number: int) -> str:
        return self.state


def driven(session: Session, unit: WorkUnit, gateway: Any, pull: str, *, enabled: bool = True):
    orchestrator = ServedOrchestrator(session, unit, gateway)
    orchestrator.enabled = enabled
    return cli.run_pass(
        [RECORD],
        cast(Orchestrator, orchestrator),
        cast(GitHub, Pull(pull)),
        {REPOSITORY.lower(): REPOSITORY},
        lambda repository: Path(repository),
        lambda *_: {},
    )


@pytest.mark.parametrize("enabled", [True, False], ids=["switch-on", "switch-off"])
def test_the_pass_settles_a_merged_pull_request_on_a_resolved_record(
    migrated_session: Session, migrated_engine: Engine, enabled: bool
) -> None:
    """The record is `resolved`, the pull request has merged, and with the switch off the answer
    refuses too: everything the ordinary path walks past."""
    unit, row = unknown_row(migrated_session, f"driver-merged-{enabled}")
    assert row.idempotency_key == KEY

    outcomes = driven(
        migrated_session, unit, FakeGateway(state=landed()), "merged", enabled=enabled
    )

    assert [(o.result, o.detail) for o in outcomes] == [("merged", "already_merged")]
    fresh = stored(migrated_engine, unit)
    assert fresh is not None
    assert fresh.status == "already_merged"


def test_the_pass_clears_a_closed_pull_request_that_never_landed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit, _ = unknown_row(migrated_session, "driver-closed")

    assert driven(migrated_session, unit, FakeGateway(state=closed()), "closed") == []
    assert stored(migrated_engine, unit) is None


def test_the_pass_alerts_only_when_its_replay_leaves_the_row_unknown(
    migrated_session: Session,
) -> None:
    unit, _ = unknown_row(migrated_session, "driver-blind")
    blind = FakeGateway(read_error=GitHubGatewayError("read_status", 502))

    [outcome] = driven(migrated_session, unit, blind, "open")

    assert outcome.result == "refused"
    assert outcome.key == act_refused_key(REPOSITORY, str(unit.id))
    assert "could not settle" in outcome.detail
    assert blind.merges == []
