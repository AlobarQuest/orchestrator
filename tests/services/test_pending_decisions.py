"""The queue's subject: what needs a human, across every gate kind (AC-017, AC-018, AC-019)."""

import uuid

from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.claims import authorize_retry
from orchestrator.services.decomposition import (
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.pending_decisions import pending_decisions
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    record_reconciliation_condition,
)
from tests.services.test_decomposition import package_ac_ids, proposal_command, worker_actor
from tests.services.test_dependencies import register_unit
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command
from tests.web.conftest import _review_unit_with_criteria

SYSTEM_ACTOR = ActorContext("system", ActorRole.SYSTEM)
# The stalled-execution kind has its own file. Nothing here holds a claim, so the shipped
# default reports nothing and these assertions stay about the kinds they are named for.
GRACE = 900


def _kinds(session: Session) -> set[str]:
    return {
        entry["kind"] for entry in pending_decisions(session, execution_stall_grace_seconds=GRACE)
    }


def _seed_registered_package(session: Session, suffix: str):
    return register_package_intake(
        session,
        intake_command(
            package_id=f"pkg-pending-{suffix}",
            content_hash=f"sha256:{suffix}",
            idempotency_key=f"package-intake-pending-{suffix}",
            acceptance_criteria=(
                acceptance_criterion("AC-001"),
                acceptance_criterion("AC-002"),
            ),
        ),
        human_actor(),
    )


def _seed_open_condition(session: Session, unit: WorkUnit) -> None:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=SYSTEM_ACTOR,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={"conclusion": "success"},
            observed_state={"conclusion": "failure"},
            detail="Quality flipped after verification read it",
        ),
    )
    assert isinstance(outcome, ConditionOutcome)


def test_every_kind_of_pending_decision_appears(migrated_session: Session) -> None:
    """AC-017. `DESIGNED_HUMAN_GATES` names three transition edges and is NOT the whole set --
    four of the six kinds below are invisible to it."""
    _seed_registered_package(migrated_session, "all-kinds-registered")

    proposed = _seed_registered_package(migrated_session, "all-kinds-proposed")
    submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            proposed.id,
            package_ac_ids(migrated_session, proposed.id),
            idempotency_key="proposal-pending-all-kinds",
        ),
        worker_actor(),
    )

    draft = register_unit(migrated_session, "pending-draft")
    assert draft.state == WorkUnitState.DRAFT
    assert draft.authority_approval_id is None

    flagged = register_unit(migrated_session, "pending-flagged")
    migrated_session.commit()
    _seed_open_condition(migrated_session, flagged)

    at_gate = register_unit(migrated_session, "pending-at-gate")
    at_gate.state = WorkUnitState.AWAITING_REVIEW
    _approve_authority(migrated_session, at_gate)
    migrated_session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=at_gate.work_package_revision_id,
            ac_id="ac-1",
            condition="A person reads the diff.",
            evidence_type="human.review",
            evidence="e",
            approver="human-1",
        )
    )
    migrated_session.commit()

    entries = pending_decisions(migrated_session, execution_stall_grace_seconds=GRACE)
    kinds = {entry["kind"] for entry in entries}

    assert kinds == {
        "package_breakdown",
        "decomposition_proposal",
        "authority_approval",
        "unit_transition",
        "criterion_adjudication",
        "reconciliation_condition",
    }
    # AC-017: each entry names the DECISION required, not the state it is in.
    for entry in entries:
        assert entry["decision"]
        assert entry["href"].startswith("/review")


def _approve_authority(session: Session, unit: WorkUnit) -> None:
    approval = Approval(
        subject_type="authority",
        subject_id=unit.id,
        subject_revision_or_fingerprint=unit.authority_fingerprint,
        decision="approved",
        approved_by="devon",
        reason="approved authority",
        event_id=uuid.uuid4(),
        idempotency_key=f"authority-{unit.id}",
    )
    session.add(approval)
    session.flush()
    unit.authority_approval_id = approval.id


def test_an_item_with_nothing_to_decide_does_not_appear(migrated_session: Session) -> None:
    """AC-018. A completed unit whose authority was approved is finished; a package that already
    has an approved breakdown has been decided."""
    revision = _seed_registered_package(migrated_session, "nothing-to-decide")
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            package_ac_ids(migrated_session, revision.id),
            idempotency_key="proposal-nothing-to-decide",
        ),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session,
        proposal_id=proposal.id,
        actor=human_actor(),
        reason="approved",
        idempotency_key="approve-nothing-to-decide",
    )
    for unit in migrated_session.query(WorkUnit).all():
        _approve_authority(migrated_session, unit)
        unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    assert _kinds(migrated_session) == set()


def _failed_unit(session: Session, key: str, *, exhausted: bool = True) -> WorkUnit:
    unit = register_unit(session, key)
    _approve_authority(session, unit)
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = unit.max_attempts if exhausted else unit.max_attempts - 1
    session.commit()
    return unit


def _entries_of_kind(session: Session, kind: str) -> list[dict]:
    return [
        entry
        for entry in pending_decisions(session, execution_stall_grace_seconds=GRACE)
        if entry["kind"] == kind
    ]


def test_a_failed_unit_names_the_disposition_it_needs(migrated_session: Session) -> None:
    """AC-017. A failed unit is stopped and nothing automatic will move it, so it is a pending
    human decision -- but Increment 4's queue produced no entry for one at all: FAILED is neither
    a settled state nor a designed gate, and no kind claimed it."""
    unit = _failed_unit(migrated_session, "pending-failed")

    entries = _entries_of_kind(migrated_session, "failed_disposition")

    assert [entry["subject"] for entry in entries] == [unit.title]
    assert "retry" in entries[0]["decision"] and "cancel" in entries[0]["decision"]
    assert entries[0]["href"] == f"/review/units/{unit.id}"


def test_a_failed_unit_with_budget_left_is_not_offered_a_retry_it_cannot_have(
    migrated_session: Session,
) -> None:
    # `authorize_retry` refuses `attempts_not_exhausted`, and requeueing is SYSTEM-only -- so for
    # this unit the only decision a person can act on is cancellation. Naming the other one would
    # send them to a form that refuses them, which is the divergence Increment 2 pinned against.
    _failed_unit(migrated_session, "pending-failed-with-budget", exhausted=False)

    decision = _entries_of_kind(migrated_session, "failed_disposition")[0]["decision"].lower()

    assert "cancel" in decision
    assert "retry" not in decision


def test_a_failed_unit_leaves_the_queue_once_cancelled(migrated_session: Session) -> None:
    unit = _failed_unit(migrated_session, "pending-failed-cancelled")
    assert "failed_disposition" in _kinds(migrated_session)

    unit.state = WorkUnitState.CANCELLED
    migrated_session.commit()

    assert "failed_disposition" not in _kinds(migrated_session)


def test_a_failed_unit_leaves_the_queue_once_a_retry_is_authorized(
    migrated_session: Session,
) -> None:
    # Driven through the real service rather than by writing the state, so the entry's
    # disappearance is a consequence of the decision being recorded, not of the test staging it.
    unit = _failed_unit(migrated_session, "pending-failed-retried")
    assert "failed_disposition" in _kinds(migrated_session)

    approval = authorize_retry(
        migrated_session,
        unit.id,
        human_actor(),
        new_max_attempts=unit.max_attempts + 1,
        reason="the runner environment was at fault",
        idempotency_key="retry-pending-failed",
    )

    assert isinstance(approval, Approval)
    assert "failed_disposition" not in _kinds(migrated_session)


def test_an_item_disappears_once_its_decision_is_recorded(migrated_session: Session) -> None:
    """AC-019. The queue is derived, not a worklist someone has to tick off."""
    unit = register_unit(migrated_session, "disappears")
    migrated_session.commit()
    assert "authority_approval" in _kinds(migrated_session)

    _approve_authority(migrated_session, unit)
    migrated_session.commit()

    assert "authority_approval" not in _kinds(migrated_session)


def test_an_adjudicated_criterion_leaves_the_queue(migrated_engine) -> None:
    unit = _review_unit_with_criteria(
        migrated_engine, unit_key="pending-adjudicated", criteria=(("ac-1", "human.review"),)
    )
    with Session(migrated_engine) as session:
        assert "criterion_adjudication" in _kinds(session)
        session.add(
            Adjudication(
                work_package_revision_id=unit.work_package_revision_id,
                work_unit_id=unit.id,
                ac_id="ac-1",
                outcome="passed",
                decided_by="devon",
                rationale="the evidence says so",
                event_id=uuid.uuid4(),
            )
        )
        session.commit()

        assert "criterion_adjudication" not in _kinds(session)


def test_a_terminal_unit_contributes_nothing(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "terminal")
    _approve_authority(migrated_session, unit)
    unit.state = WorkUnitState.CANCELLED
    migrated_session.commit()

    assert _kinds(migrated_session) == set()
