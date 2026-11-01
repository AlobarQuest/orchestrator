"""The execution-status read: per-unit facts for the execution alert lane."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    Dependency,
    DispatchRecord,
    Event,
    SdsMember,
    SdsMemberGrant,
    StagedPackageIntake,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.intake.decomposition import (
    approve_decomposition_proposal,
    reject_decomposition_proposal,
    require_decomposition_revision,
    submit_decomposition_proposal,
    supersede_approved_decomposition,
)
from orchestrator.services.intake.intake_reads import POLICY_NOT_APPLIED
from orchestrator.services.intake.package_intake import register_package_intake
from orchestrator.services.lifecycle.claims import LeaseGrant, claim_unit
from orchestrator.services.reporting.execution_status import (
    ExecutionStatusRow,
    _is_standing,
    execution_status,
)
from tests._support.seeding import grant_repository, register_unit
from tests.services.test_claims import worker
from tests.services.test_decomposition import package_ac_ids, proposal_command, worker_actor
from tests.services.test_dispatch import PILOT_REPOSITORY, claimable_unit, ready_unit
from tests.services.test_package_intake import (
    acceptance_criterion,
    human_actor,
    intake_command,
)
from tests.services.test_reclaim import expire
from tests.services.test_rotation_claims import seed_unit

WEEK = 604_800
DAY = 86_400
HUMAN = ActorContext("human-1", ActorRole.HUMAN)


def _record(session: Session, unit: WorkUnit, attempt: int, status: str) -> None:
    event = Event(
        actor_id="system",
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"es-{unit.id}-{attempt}:event",
    )
    session.add(event)
    session.flush()
    session.add(
        DispatchRecord(
            work_unit_id=unit.id,
            work_package_revision_id=unit.work_package_revision_id,
            runner_attempt=attempt,
            status=status,
            reason_code="github_api",
            idempotency_key=f"es-{unit.id}-{attempt}",
            target_repository=PILOT_REPOSITORY,
            workflow_id="w.yml",
            workflow_ref="main",
            failure_signature="sig" if status == "failed" else None,
            payload={},
            event_id=event.id,
        )
    )
    session.commit()


def _row(
    session: Session,
    unit: WorkUnit,
    *,
    execution_stall_grace_seconds: int = 900,
    dispatch_failure_signature_threshold: int = 3,
    stalled_approval_seconds: int = WEEK,
    stalled_verification_seconds: int = DAY,
):
    rows = {
        row.unit_id: row
        for row in execution_status(
            session,
            stall_grace_seconds=execution_stall_grace_seconds,
            failure_signature_threshold=dispatch_failure_signature_threshold,
            stalled_approval_seconds=stalled_approval_seconds,
            stalled_verification_seconds=stalled_verification_seconds,
        )
    }
    return rows.get(unit.id)


def _subjects(session: Session, kind: str, *, threshold: int = WEEK) -> list[ExecutionStatusRow]:
    return [
        row
        for row in execution_status(
            session,
            stall_grace_seconds=900,
            failure_signature_threshold=3,
            stalled_approval_seconds=threshold,
            stalled_verification_seconds=threshold,
        )
        if row.subject_kind == kind
    ]


@pytest.mark.parametrize("state", [WorkUnitState.COMPLETED, WorkUnitState.CANCELLED])
def test_a_completed_or_cancelled_unit_has_no_row(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = ready_unit(migrated_session, key="es-done")
    unit.state = state
    migrated_session.commit()

    assert _row(migrated_session, unit) is None


def test_a_live_unit_has_a_row_with_its_class_and_repository(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-live", change_class="app-change")

    row = _row(migrated_session, unit)

    assert row is not None
    assert (row.state, row.change_class, row.target_repository) == (
        "ready",
        "app-change",
        PILOT_REPOSITORY,
    )
    assert not (row.has_claim or row.lease_lapsed or row.breaker_open or row.is_rotation)


def test_lease_lapsed_is_true_only_past_grace(migrated_session: Session) -> None:
    unit = claimable_unit(migrated_session, key="es-lapse")
    grant = claim_unit(migrated_session, unit.id, worker(), "es-lapse-claim")
    assert isinstance(grant, LeaseGrant)
    unit.state = WorkUnitState.EXECUTING
    migrated_session.commit()
    expire(migrated_session, grant.claim_id)

    inside = _row(migrated_session, unit, execution_stall_grace_seconds=3_600)
    past = _row(migrated_session, unit, execution_stall_grace_seconds=0)

    assert inside is not None and past is not None
    assert inside.has_claim and past.has_claim
    assert (inside.lease_lapsed, past.lease_lapsed) == (False, True)


def test_the_latest_dispatch_record_decides(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-latest")
    _record(migrated_session, unit, 1, "failed")
    _record(migrated_session, unit, 2, "dispatched")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.latest_dispatch_status == "dispatched"
    assert row.latest_dispatch_at is not None


def test_a_claim_after_the_dispatch_still_counts_once_the_work_moves_on(
    migrated_session: Session,
) -> None:
    unit = claimable_unit(migrated_session, key="es-gate")
    _record(migrated_session, unit, 1, "dispatched")
    migrated_session.commit()
    assert isinstance(claim_unit(migrated_session, unit.id, worker(), "es-gate"), LeaseGrant)
    unit.state = WorkUnitState.AWAITING_REVIEW
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None and row.has_claim


def test_a_claim_older_than_the_latest_dispatch_does_not_answer_it(
    migrated_session: Session,
) -> None:
    unit = claimable_unit(migrated_session, key="es-redispatch")
    assert isinstance(claim_unit(migrated_session, unit.id, worker(), "es-rd"), LeaseGrant)
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    _record(migrated_session, unit, 2, "dispatched")
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None and not row.has_claim


def test_a_unit_never_dispatched_has_no_latest_record(migrated_session: Session) -> None:
    row = _row(migrated_session, ready_unit(migrated_session, key="es-none"))

    assert row is not None
    assert (row.latest_dispatch_status, row.latest_dispatch_at) == (None, None)


def test_breaker_open_follows_the_at_rest_failure_count(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-breaker")
    for attempt in (1, 2, 3):
        _record(migrated_session, unit, attempt, "failed")

    open_row = _row(migrated_session, unit, dispatch_failure_signature_threshold=3)
    closed_row = _row(migrated_session, unit, dispatch_failure_signature_threshold=4)

    assert open_row is not None and closed_row is not None
    assert (open_row.breaker_open, closed_row.breaker_open) == (True, False)


@pytest.mark.parametrize(
    ("grant_state", "member_state", "expected"),
    [("granted", "member", True), ("withdrawn", "member", False), ("granted", "retired", False)],
)
def test_granted_reads_the_member_record(
    migrated_session: Session, grant_state: str, member_state: str, expected: bool
) -> None:
    unit = ready_unit(migrated_session, key="es-grant", change_class="app-change")
    member = SdsMember(
        repository=PILOT_REPOSITORY.upper(),
        owner="devon",
        state=member_state,
        state_changed_by="human:devon",
        state_reason="seed",
    )
    migrated_session.add(member)
    migrated_session.flush()
    migrated_session.add(
        SdsMemberGrant(
            member_id=member.id,
            service="app_changes",
            state=grant_state,
            seeded=False,
            set_by="human:devon",
            reason="seed",
        )
    )
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.granted is expected


def test_granted_is_false_with_no_member(migrated_session: Session) -> None:
    row = _row(migrated_session, ready_unit(migrated_session, key="es-nomember"))

    assert row is not None
    assert row.granted is False


def test_a_rotation_unit_says_so(migrated_session: Session) -> None:
    unit = seed_unit(migrated_session, "es-rot")
    migrated_session.commit()
    plain = ready_unit(migrated_session, key="es-plain")

    rotation_row = _row(migrated_session, unit)
    plain_row = _row(migrated_session, plain)

    assert rotation_row is not None and plain_row is not None
    assert rotation_row.is_rotation is True
    assert plain_row.is_rotation is False
    assert rotation_row.standing is True
    assert plain_row.standing is False


def test_a_later_skipped_record_does_not_hide_a_dispatched_one(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-skipped")
    _record(migrated_session, unit, 1, "dispatched")
    _record(migrated_session, unit, 2, "skipped")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.latest_dispatch_status == "dispatched"


def test_a_retried_units_second_claim_is_the_one_compared(migrated_session: Session) -> None:
    unit = claimable_unit(migrated_session, key="es-retry")
    assert isinstance(claim_unit(migrated_session, unit.id, worker(), "es-r1"), LeaseGrant)
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    _record(migrated_session, unit, 2, "dispatched")
    assert isinstance(claim_unit(migrated_session, unit.id, worker(), "es-r2"), LeaseGrant)
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None and row.has_claim


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_standing_is_the_boolean_true_only(value: object) -> None:
    revision = WorkPackageRevision()
    revision.enforcement_snapshot = {"profile_fields": {"standing": value}}
    assert _is_standing(revision) is False


# What waits on a person before a unit runs.


def _revision(session: Session, name: str, **overrides: Any) -> WorkPackageRevision:
    return register_package_intake(
        session,
        intake_command(
            package_id=f"pkg-{name}",
            content_hash=f"sha256:{name}",
            idempotency_key=f"intake-{name}",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            **overrides,
        ),
        human_actor(),
    )


def _propose(
    session: Session, revision: WorkPackageRevision, key: str, actor: ActorContext | None = None
) -> DecompositionProposal:
    return submit_decomposition_proposal(
        session,
        proposal_command(revision.id, package_ac_ids(session, revision.id), idempotency_key=key),
        actor or worker_actor(),
    )


def _decide(
    session: Session, decide: Callable[..., DecompositionProposal], proposal_id: uuid.UUID, key: str
) -> DecompositionProposal:
    return decide(session, proposal_id, actor=HUMAN, reason="decided", idempotency_key=key)


def _stage(session: Session) -> StagedPackageIntake:
    row = StagedPackageIntake(
        payload={"package_id": "pkg-staged", "revision": 1},
        idempotency_key="staged",
        staged_by="work-carrier",
        state="staged",
    )
    session.add(row)
    session.commit()
    return row


def test_no_units_does_not_hide_what_is_waiting(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "alone")

    [row] = _subjects(migrated_session, "package")

    assert row.subject_id == revision.id
    assert row.unit_id is None


def test_a_staged_intake_waits_from_when_it_was_staged(migrated_session: Session) -> None:
    staged = _stage(migrated_session)

    [young] = _subjects(migrated_session, "staged-intake")
    [late] = _subjects(migrated_session, "staged-intake", threshold=0)

    assert (young.subject_id, young.state, young.waiting) == (staged.id, "staged", True)
    assert young.entered_at == staged.staged_at
    assert (young.overdue, late.overdue) == (False, True)


def test_a_withdrawn_intake_has_no_row(migrated_session: Session) -> None:
    staged = _stage(migrated_session)
    withdrawn_at = staged.staged_at
    staged.state = "withdrawn"
    staged.withdrawn_by, staged.withdrawal_reason = "devon", "no"
    staged.withdrawn_at = withdrawn_at
    migrated_session.commit()

    assert _subjects(migrated_session, "staged-intake") == []


def test_an_undecomposed_package_waits_from_registration(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "young")

    [young] = _subjects(migrated_session, "package")
    [late] = _subjects(migrated_session, "package", threshold=0)

    assert young.entered_at == revision.registered_at
    assert (young.waiting, young.overdue, late.overdue) == (True, False, True)


def test_a_pending_proposal_waits_from_when_it_was_proposed(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "pending")
    proposal = _propose(migrated_session, revision, "pending")
    migrated_session.commit()

    assert _subjects(migrated_session, "package") == []
    [young] = _subjects(migrated_session, "proposal")
    [late] = _subjects(migrated_session, "proposal", threshold=0)

    assert (young.subject_id, young.state) == (proposal.id, "proposed")
    assert young.entered_at == proposal.proposed_at
    assert young.proposed_by == proposal.proposed_by
    assert (young.overdue, late.overdue) == (False, True)
    assert young.is_rotation is False


def test_a_rejected_proposal_stops_raising_and_its_revision_is_not_relisted(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session, "rejected")
    proposal = _propose(migrated_session, revision, "rejected")
    _decide(migrated_session, reject_decomposition_proposal, proposal.id, "reject")
    migrated_session.commit()

    assert _subjects(migrated_session, "proposal", threshold=0) == []
    assert _subjects(migrated_session, "package", threshold=0) == []


def test_an_approved_proposal_stops_raising(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "approved")
    proposal = _propose(migrated_session, revision, "approved")
    _decide(migrated_session, approve_decomposition_proposal, proposal.id, "approve")
    migrated_session.commit()

    assert _subjects(migrated_session, "proposal", threshold=0) == []
    assert _subjects(migrated_session, "package", threshold=0) == []


def test_a_sent_back_proposal_keeps_waiting_once_from_the_decision(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session, "sent-back")
    proposal = _propose(migrated_session, revision, "sent-back")
    migrated_session.commit()
    decided = _decide(migrated_session, require_decomposition_revision, proposal.id, "send-back")
    migrated_session.commit()

    assert _subjects(migrated_session, "package", threshold=0) == []
    [row] = _subjects(migrated_session, "proposal", threshold=0)

    assert (row.subject_id, row.state, row.waiting) == (proposal.id, "revision_required", True)
    assert row.entered_at == decided.decided_at
    assert row.entered_at > proposal.proposed_at


def test_a_later_proposal_answers_a_sent_back_one(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "answered")
    first = _propose(migrated_session, revision, "answered-1")
    _decide(migrated_session, require_decomposition_revision, first.id, "answered-back")
    second = _propose(migrated_session, revision, "answered-2")
    migrated_session.commit()

    assert [row.subject_id for row in _subjects(migrated_session, "proposal")] == [second.id]


def test_a_superseded_revision_waits_from_the_supersession(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "superseded")
    proposal = _propose(migrated_session, revision, "superseded")
    _decide(migrated_session, approve_decomposition_proposal, proposal.id, "s-approve")
    migrated_session.commit()
    _decide(migrated_session, supersede_approved_decomposition, proposal.id, "s-supersede")
    migrated_session.commit()
    superseded_at = migrated_session.scalar(select(ApprovedDecomposition.superseded_at))

    [row] = _subjects(migrated_session, "package")

    assert superseded_at is not None
    assert row.entered_at == superseded_at
    assert row.entered_at > revision.registered_at


def test_a_replaced_revision_does_not_ask_for_a_breakdown(migrated_session: Session) -> None:
    first = _revision(migrated_session, "replaced")
    second = register_package_intake(
        migrated_session,
        intake_command(
            package_id="pkg-replaced",
            revision=2,
            content_hash="sha256:replaced-2",
            idempotency_key="intake-replaced-2",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
        ),
        human_actor(),
    )

    subjects = _subjects(migrated_session, "package")

    assert first.work_package_id == second.work_package_id
    assert [row.subject_id for row in subjects] == [second.id]


def _not_applied(
    session: Session, proposal: DecompositionProposal, reason: str, key: str = "pna"
) -> None:
    session.add(
        Event(
            actor_id="orchestrator-system",
            action=POLICY_NOT_APPLIED,
            subject_type="decomposition_proposal",
            subject_id=proposal.id,
            from_state="proposed",
            to_state="proposed",
            payload={"reason": reason, "detail": []},
            correlation_id=uuid.uuid4(),
            idempotency_key=key,
        )
    )
    session.commit()


def test_policy_not_applied_is_served_only_while_proposed(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "refused")
    proposal = _propose(migrated_session, revision, "refused")
    _not_applied(migrated_session, proposal, "attempts_over_budget")

    [pending] = _subjects(migrated_session, "proposal")
    assert pending.policy_not_applied == "attempts_over_budget"

    _decide(migrated_session, require_decomposition_revision, proposal.id, "refused-back")
    migrated_session.commit()

    [sent_back] = _subjects(migrated_session, "proposal")
    assert sent_back.policy_not_applied is None


def test_two_refusals_on_one_proposal_serve_one_row(migrated_session: Session) -> None:
    revision = _revision(migrated_session, "twice")
    proposal = _propose(migrated_session, revision, "twice")
    _not_applied(migrated_session, proposal, "attempts_over_budget", "pna-1")
    _not_applied(migrated_session, proposal, "policy_unreadable", "pna-2")

    assert len(_subjects(migrated_session, "proposal")) == 1


def test_a_bump_proposal_carries_what_the_ownership_table_reads(migrated_session: Session) -> None:
    fields = {"standing": True, "target_repo": PILOT_REPOSITORY}
    revision = _revision(
        migrated_session,
        "bump",
        profile="dependency-update",
        enforcement_snapshot={"profile_fields": fields},
    )
    grant_repository(migrated_session, PILOT_REPOSITORY)
    _propose(migrated_session, revision, "bump")
    migrated_session.commit()

    [row] = _subjects(migrated_session, "proposal")

    assert (row.change_class, row.standing, row.target_repository, row.granted) == (
        "dependency-update",
        True,
        PILOT_REPOSITORY,
        True,
    )


def test_a_rotation_proposal_says_so_and_stays_is_rotation_false(
    migrated_session: Session,
) -> None:
    fields = {"standing": True, "credential_id": "openrouter-generic"}
    revision = _revision(
        migrated_session,
        "rotation",
        profile="non-software-operational",
        enforcement_snapshot={"profile_fields": fields},
    )
    _propose(migrated_session, revision, "rotation")
    migrated_session.commit()

    [row] = _subjects(migrated_session, "proposal")

    assert (row.rotation_package, row.is_rotation) == (True, False)


def test_any_other_proposal_is_no_rotation_package(migrated_session: Session) -> None:
    _propose(migrated_session, _revision(migrated_session, "plain"), "plain")
    migrated_session.commit()

    [row] = _subjects(migrated_session, "proposal")

    assert (row.rotation_package, row.change_class, row.standing) == (False, None, False)


# Units at an admission gate.


def _parked(session: Session, state: WorkUnitState, key: str) -> WorkUnit:
    unit = register_unit(session, key=key)
    persisted = session.get(WorkUnit, unit.id)
    assert persisted is not None
    persisted.state = state.value
    session.commit()
    return persisted


@pytest.mark.parametrize(
    "state",
    [WorkUnitState.DRAFT, WorkUnitState.AWAITING_APPROVAL, WorkUnitState.AWAITING_REVIEW],
)
def test_a_unit_at_an_admission_gate_is_waiting(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = _parked(migrated_session, state, f"gate-{state}")

    young = _row(migrated_session, unit)
    late = _row(migrated_session, unit, stalled_approval_seconds=0)

    assert young is not None and late is not None
    assert (young.subject_kind, young.subject_id) == ("unit", unit.id)
    assert (young.waiting, young.overdue, late.overdue) == (True, False, True)


@pytest.mark.parametrize(
    "state", [WorkUnitState.READY, WorkUnitState.EXECUTING, WorkUnitState.FAILED]
)
def test_a_unit_past_the_gates_is_not_waiting_at_any_threshold(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = _parked(migrated_session, state, f"past-{state}")

    row = _row(migrated_session, unit, stalled_approval_seconds=0)

    assert row is not None
    assert (row.waiting, row.overdue) == (False, False)


def test_a_draft_unit_with_a_pending_dependency_is_not_waiting_on_a_person(
    migrated_session: Session,
) -> None:
    unit = _parked(migrated_session, WorkUnitState.DRAFT, "dep-draft")
    other = _parked(migrated_session, WorkUnitState.READY, "dep-target")
    dependency = Dependency(
        work_unit_id=unit.id,
        kind="work_unit",
        required_state_or_condition="completed",
        depends_on_work_unit_id=other.id,
        status="pending",
    )
    migrated_session.add(dependency)
    migrated_session.commit()

    row = _row(migrated_session, unit, stalled_approval_seconds=0)
    assert row is not None
    assert (row.waiting, row.overdue) == (False, False)

    dependency.status = "satisfied"
    migrated_session.commit()

    row = _row(migrated_session, unit, stalled_approval_seconds=0)
    assert row is not None
    assert (row.waiting, row.overdue) == (True, True)


def test_a_pending_dependency_does_not_excuse_a_unit_past_draft(
    migrated_session: Session,
) -> None:
    unit = _parked(migrated_session, WorkUnitState.AWAITING_REVIEW, "dep-review")
    other = _parked(migrated_session, WorkUnitState.READY, "dep-other")
    migrated_session.add(
        Dependency(
            work_unit_id=unit.id,
            kind="work_unit",
            required_state_or_condition="completed",
            depends_on_work_unit_id=other.id,
            status="pending",
        )
    )
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None and row.waiting is True


@pytest.mark.parametrize("kind", ["decision", "external_system"])
def test_a_pending_dependency_on_a_person_is_a_wait(migrated_session: Session, kind: str) -> None:
    unit = _parked(migrated_session, WorkUnitState.DRAFT, f"person-{kind}")
    migrated_session.add(
        Dependency(
            work_unit_id=unit.id,
            kind=kind,
            required_state_or_condition="resolved",
            external_ref=f"ref-{kind}",
            status="pending",
        )
    )
    migrated_session.commit()

    row = _row(migrated_session, unit, stalled_approval_seconds=0)

    assert row is not None
    assert (row.waiting, row.overdue) == (True, True)


def test_a_freed_draft_unit_starts_a_new_clock(migrated_session: Session) -> None:
    unit = _parked(migrated_session, WorkUnitState.DRAFT, "freed")
    other = _parked(migrated_session, WorkUnitState.READY, "freed-target")
    unit.created_at = datetime.now(UTC) - timedelta(days=30)
    freed = datetime.now(UTC)
    migrated_session.add(
        Dependency(
            work_unit_id=unit.id,
            kind="work_unit",
            required_state_or_condition="completed",
            depends_on_work_unit_id=other.id,
            status="satisfied",
            resolved_at=freed,
        )
    )
    migrated_session.commit()

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.entered_at == freed
    assert (row.waiting, row.overdue) == (True, False)


def _state_event(
    session: Session, unit: WorkUnit, key: str, from_state: str | None, to_state: str
) -> Event:
    event = Event(
        actor_id="system",
        action="work_unit.test",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=from_state,
        to_state=to_state,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=key,
    )
    session.add(event)
    session.commit()
    return event


def test_entered_at_is_the_newest_transition_into_the_state(migrated_session: Session) -> None:
    unit = _parked(migrated_session, WorkUnitState.AWAITING_REVIEW, "entered")
    entered = _state_event(migrated_session, unit, "entered-1", "verifying", "awaiting_review")
    _state_event(migrated_session, unit, "entered-other", "verifying", "submitted")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.entered_at == entered.occurred_at


def test_a_same_state_event_does_not_move_the_clock(migrated_session: Session) -> None:
    """A claim renewal, a dependency and an approval all write `to_state` as the unit's state."""
    unit = _parked(migrated_session, WorkUnitState.AWAITING_REVIEW, "same-state")
    entered = _state_event(migrated_session, unit, "same-1", "verifying", "awaiting_review")
    _state_event(migrated_session, unit, "same-2", "awaiting_review", "awaiting_review")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.entered_at == entered.occurred_at


def test_a_verifying_unit_is_measured_from_its_entry_to_submitted(
    migrated_session: Session,
) -> None:
    """A verify that fails part-way and is retried must not restart the stalled clock."""
    unit = _parked(migrated_session, WorkUnitState.VERIFYING, "verifying-clock")
    submitted = _state_event(migrated_session, unit, "vc-1", "executing", "submitted")
    _state_event(migrated_session, unit, "vc-2", "submitted", "verifying")

    row = _row(migrated_session, unit)

    assert row is not None
    assert row.entered_at == submitted.occurred_at


def _in_state(session: Session, key: str, state: WorkUnitState) -> WorkUnit:
    unit = ready_unit(session, key=key)
    unit.state = state
    session.commit()
    return unit


def _moved(
    session: Session, unit: WorkUnit, from_state: str, to_state: str, when: datetime, name: str
) -> None:
    session.add(
        Event(
            actor_id="system",
            action="work_unit.verifying",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=from_state,
            to_state=to_state,
            payload={},
            correlation_id=uuid.uuid4(),
            idempotency_key=f"es-moved-{unit.id}-{name}",
            occurred_at=when,
        )
    )
    session.commit()


@pytest.mark.parametrize("state", [WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING])
def test_a_unit_past_the_threshold_is_verification_overdue(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = _in_state(migrated_session, f"es-over-{state}", state)

    row = _row(migrated_session, unit, stalled_verification_seconds=0)

    assert row is not None and row.verification_overdue


def test_a_unit_just_submitted_is_not_overdue(migrated_session: Session) -> None:
    unit = _in_state(migrated_session, "es-young", WorkUnitState.SUBMITTED)

    row = _row(migrated_session, unit)

    assert row is not None and not row.verification_overdue


def test_only_the_verification_states_are_overdue(migrated_session: Session) -> None:
    unit = ready_unit(migrated_session, key="es-not-verifying")

    row = _row(migrated_session, unit, stalled_verification_seconds=0)

    assert row is not None and not row.verification_overdue


def test_the_verification_clock_is_its_own_threshold(migrated_session: Session) -> None:
    unit = _in_state(migrated_session, "es-own", WorkUnitState.SUBMITTED)

    row = _row(
        migrated_session, unit, stalled_approval_seconds=0, stalled_verification_seconds=WEEK
    )

    assert row is not None and not row.verification_overdue


def test_a_same_state_write_does_not_restart_the_verification_clock(
    migrated_session: Session,
) -> None:
    """Evidence filed on a stuck unit is why this reads the move, not `updated_at`."""
    unit = _in_state(migrated_session, "es-same", WorkUnitState.SUBMITTED)
    now = datetime.now(UTC)
    _moved(migrated_session, unit, "executing", "submitted", now - timedelta(days=2), "in")
    _moved(migrated_session, unit, "submitted", "submitted", now, "same")

    row = _row(migrated_session, unit)

    assert row is not None and row.verification_overdue


def test_a_row_that_is_not_a_unit_is_never_verification_overdue(
    migrated_session: Session,
) -> None:
    _stage(migrated_session)

    [row] = _subjects(migrated_session, "staged-intake", threshold=0)

    assert not row.verification_overdue
