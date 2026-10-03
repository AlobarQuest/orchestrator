"""Superseding an unworked decomposition approval (ADR-0052)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    Dependency,
    DispatchRecord,
    Event,
    WorkUnit,
)
from orchestrator.services.intake.change_record_work import work_for_change_record
from orchestrator.services.intake.decomposition import (
    approve_decomposition_proposal,
    can_supersede,
    submit_decomposition_proposal,
    supersede_approved_decomposition,
)
from orchestrator.services.intake.graduation_ledger import graduation_ledger
from orchestrator.services.intake.package_intake import register_package_intake
from orchestrator.services.intake.packages import record_approval
from orchestrator.services.lifecycle.lifecycle import TransitionCommand, transition_unit
from tests.services.test_decomposition import (
    package_ac_ids,
    proposal_command,
    register_intaken_revision,
    worker_actor,
)
from tests.services.test_package_intake import (
    AUTHORITY,
    NOW,
    acceptance_criterion,
    human_actor,
    intake_command,
)


def _approved(session: Session, *, key: str = "proposal-1") -> DecompositionProposal:
    revision = register_intaken_revision(session)
    proposal = submit_decomposition_proposal(
        session,
        proposal_command(revision.id, package_ac_ids(session, revision.id), idempotency_key=key),
        worker_actor(),
    )
    approve_decomposition_proposal(
        session, proposal.id, actor=human_actor(), reason="Approve.", idempotency_key=f"{key}-ok"
    )
    return proposal


def _units(session: Session, proposal: DecompositionProposal) -> list[WorkUnit]:
    created = proposal.created_work_unit_ids
    assert isinstance(created, dict)
    ids = [uuid.UUID(unit_id) for unit_id in created.values()]
    return list(
        session.scalars(select(WorkUnit).where(WorkUnit.id.in_(ids)).order_by(WorkUnit.unit_key))
    )


def _supersede(
    session: Session,
    proposal: DecompositionProposal,
    *,
    actor: ActorContext | None = None,
    reason: str = "Wrong split.",
    idempotency_key: str = "supersede-1",
) -> DecompositionProposal:
    return supersede_approved_decomposition(
        session,
        proposal.id,
        actor=actor or human_actor(),
        reason=reason,
        idempotency_key=idempotency_key,
    )


def _refusal(
    session: Session,
    proposal: DecompositionProposal,
    *,
    reason: str = "Wrong split.",
    idempotency_key: str = "supersede-1",
) -> str:
    with pytest.raises(DomainError) as error:
        _supersede(session, proposal, reason=reason, idempotency_key=idempotency_key)
    return error.value.code


def _dispatch(
    session: Session, unit: WorkUnit, status: str, *, trigger_raised: bool = False
) -> None:
    """A dispatch record shaped as the dispatch service writes it: only a trigger call that raised
    leaves a `failure_signature` (status `failed`, or `blocked` once the circuit opens)."""
    session.add(
        DispatchRecord(
            work_unit_id=unit.id,
            work_package_revision_id=unit.work_package_revision_id,
            runner_attempt=1,
            status=status,
            idempotency_key=f"dispatch-{unit.id}-{status}-{trigger_raised}",
            target_repository="owner/repo",
            workflow_id="factory-runner.yml",
            workflow_ref="main",
            failure_signature="workflow_dispatch:http_error:abc" if trigger_raised else None,
            payload={},
        )
    )
    session.flush()


def test_superseding_retires_draft_and_ready_units_and_records_who_and_why(
    migrated_session: Session,
) -> None:
    proposal = _approved(migrated_session)
    first, second = _units(migrated_session, proposal)
    second.state = WorkUnitState.READY
    envelopes = {unit.id: dict(unit.authority) for unit in (first, second)}

    _supersede(migrated_session, proposal)

    approval = migrated_session.scalar(
        select(ApprovedDecomposition).where(ApprovedDecomposition.proposal_id == proposal.id)
    )
    assert approval is not None
    assert (approval.superseded_by, approval.supersession_reason) == ("human-1", "Wrong split.")
    assert approval.superseded_at is not None
    retired = _units(migrated_session, proposal)
    assert {unit.state for unit in retired} == {WorkUnitState.CANCELLED}
    assert {unit.id: unit.authority for unit in retired} == envelopes
    assert proposal.state == "approved"
    event = migrated_session.scalar(select(Event).where(Event.idempotency_key == "supersede-1"))
    assert event is not None and event.action == "decomposition.superseded"


def test_a_re_approval_may_reuse_the_superseded_units_keys(migrated_session: Session) -> None:
    proposal = _approved(migrated_session)
    _supersede(migrated_session, proposal)
    revision_id = proposal.work_package_revision_id
    second = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision_id,
            package_ac_ids(migrated_session, revision_id),
            rationale="Corrected split.",
            idempotency_key="proposal-2",
        ),
        worker_actor(),
    )

    approve_decomposition_proposal(
        migrated_session,
        second.id,
        actor=human_actor(),
        reason="Approve the corrected split.",
        idempotency_key="proposal-2-ok",
    )

    old = {unit.unit_key: unit for unit in _units(migrated_session, proposal)}
    new = {unit.unit_key: unit for unit in _units(migrated_session, second)}
    assert set(new) == set(old) == {"unit-1", "unit-2"}
    assert {unit.state for unit in new.values()} == {WorkUnitState.DRAFT}
    assert all(new[key].id != old[key].id for key in new)


@pytest.mark.parametrize(
    "work",
    ["claimed", "attempted", "completed_sibling", "dispatched", "trigger_failed", "circuit_open"],
)
def test_an_approval_with_worked_units_is_not_superseded(
    migrated_session: Session, work: str
) -> None:
    proposal = _approved(migrated_session)
    unit = _units(migrated_session, proposal)[0]
    if work == "claimed":
        unit.state = WorkUnitState.CLAIMED
    elif work == "attempted":
        unit.state = WorkUnitState.READY
        unit.attempt_count = 1
    elif work == "completed_sibling":
        unit.state = WorkUnitState.COMPLETED
    else:
        unit.state = WorkUnitState.READY
        status = {"dispatched": "dispatched", "trigger_failed": "failed", "circuit_open": "blocked"}
        _dispatch(migrated_session, unit, status[work], trigger_raised=work != "dispatched")
    migrated_session.flush()

    assert not can_supersede(migrated_session, proposal)
    assert _refusal(migrated_session, proposal) == "decomposition_work_started"
    assert {u.state for u in _units(migrated_session, proposal)} != {WorkUnitState.CANCELLED}


def test_a_dispatch_refused_at_admission_does_not_count_as_work(migrated_session: Session) -> None:
    proposal = _approved(migrated_session)
    first, second = _units(migrated_session, proposal)
    for unit, status in ((first, "skipped"), (second, "blocked")):
        unit.state = WorkUnitState.READY
        _dispatch(migrated_session, unit, status)

    assert can_supersede(migrated_session, proposal)
    _supersede(migrated_session, proposal)


def test_a_superseded_approval_is_not_superseded_again(migrated_session: Session) -> None:
    proposal = _approved(migrated_session)
    _supersede(migrated_session, proposal)

    assert not can_supersede(migrated_session, proposal)
    assert _refusal(migrated_session, proposal, idempotency_key="supersede-2") == (
        "decomposition_not_active"
    )


def test_a_replay_returns_the_same_result_and_a_changed_reason_conflicts(
    migrated_session: Session,
) -> None:
    proposal = _approved(migrated_session)
    _supersede(migrated_session, proposal)

    assert _supersede(migrated_session, proposal).id == proposal.id
    assert _refusal(migrated_session, proposal, reason="A different reason.") == (
        "idempotency_conflict"
    )


def test_only_a_human_supersedes(migrated_session: Session) -> None:
    proposal = _approved(migrated_session)
    system = ActorContext("system", ActorRole.SYSTEM)

    with pytest.raises(DomainError):
        _supersede(migrated_session, proposal, actor=system)
    assert {u.state for u in _units(migrated_session, proposal)} == {WorkUnitState.DRAFT}


@pytest.mark.parametrize("state", [WorkUnitState.DRAFT, WorkUnitState.READY])
def test_the_ordinary_cancel_still_cannot_retire_a_draft_or_ready_unit(
    migrated_session: Session, state: WorkUnitState
) -> None:
    proposal = _approved(migrated_session)
    unit = _units(migrated_session, proposal)[0]
    unit.state = state
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.CANCELLED,
                actor=human_actor(),
                expected_version=unit.version,
                idempotency_key="plain-cancel",
            ),
        )

    assert error.value.code == "decomposition_supersession_required"


def test_a_unit_outside_the_approval_depending_on_it_blocks_supersession(
    migrated_session: Session,
) -> None:
    proposal = _approved(migrated_session)
    target = _units(migrated_session, proposal)[0]
    # Planted as a row: an intake revision takes units only through a decomposition approval, and
    # this one stands for any unit outside the approval that comes to depend on it.
    outsider = WorkUnit(
        work_package_revision_id=target.work_package_revision_id,
        unit_key="outsider",
        title="Outsider",
        outcome="Waits on the approval's unit.",
        state=WorkUnitState.DRAFT,
        decomposition_approved_by="human-1",
        decomposition_approved_at=NOW,
        required_capability="repo.edit",
        authority=AUTHORITY.normalized(),
        authority_fingerprint=authority_fingerprint(AUTHORITY),
        max_attempts=3,
    )
    migrated_session.add(outsider)
    migrated_session.flush()
    migrated_session.add(
        Dependency(
            work_unit_id=outsider.id,
            kind="work_unit",
            required_state_or_condition="completed",
            depends_on_work_unit_id=target.id,
            status="pending",
        )
    )
    migrated_session.flush()

    assert not can_supersede(migrated_session, proposal)
    assert _refusal(migrated_session, proposal) == "decomposition_has_dependents"


def test_the_ordinary_cancel_cannot_retire_the_re_approvals_units(
    migrated_session: Session,
) -> None:
    """The guard keys on the unit's OWN approval: an older, superseded approval of the same
    revision must not unlock the replacement units."""
    proposal = _approved(migrated_session)
    _supersede(migrated_session, proposal)
    revision_id = proposal.work_package_revision_id
    second = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision_id,
            package_ac_ids(migrated_session, revision_id),
            rationale="Corrected split.",
            idempotency_key="proposal-2",
        ),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session, second.id, actor=human_actor(), reason="Ok.", idempotency_key="p2-ok"
    )
    unit = _units(migrated_session, second)[0]
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.CANCELLED,
                actor=human_actor(),
                expected_version=unit.version,
                idempotency_key="plain-cancel-new",
            ),
        )

    assert error.value.code == "decomposition_supersession_required"


def test_a_change_record_completes_on_the_re_approvals_units(migrated_session: Session) -> None:
    """Units retired by a supersession were replaced, not given up, so they don't hold the change
    record open (the work watcher retires a record only when its work is complete)."""
    revision = register_package_intake(
        migrated_session,
        intake_command(
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            change_record_id=4242,
        ),
        human_actor(),
    )
    ac_ids = package_ac_ids(migrated_session, revision.id)
    first = submit_decomposition_proposal(
        migrated_session, proposal_command(revision.id, ac_ids), worker_actor()
    )
    approve_decomposition_proposal(
        migrated_session, first.id, actor=human_actor(), reason="Ok.", idempotency_key="p1-ok"
    )
    _supersede(migrated_session, first)
    second = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids, rationale="Again.", idempotency_key="proposal-2"),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session, second.id, actor=human_actor(), reason="Ok.", idempotency_key="p2-ok"
    )
    for unit in _units(migrated_session, second):
        unit.state = WorkUnitState.COMPLETED
    migrated_session.flush()

    work = work_for_change_record(migrated_session, 4242)

    assert work.all_units_completed
    assert {unit.unit_id for unit in work.units} == {u.id for u in _units(migrated_session, second)}


def test_a_superseded_unit_is_not_an_abandoned_clearing_in_the_graduation_ledger(
    migrated_session: Session,
) -> None:
    """A unit retired by supersession never ran, so it has no outcome for a human weighing whether
    to graduate its envelope's pattern."""
    proposal = _approved(migrated_session)
    old = _units(migrated_session, proposal)[0]
    record_approval(
        migrated_session,
        unit_id=old.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="Envelope reviewed.",
        idempotency_key="old-authority",
        expected_version=old.version,
    )
    old.state = WorkUnitState.READY
    migrated_session.flush()
    _supersede(migrated_session, proposal)
    revision_id = proposal.work_package_revision_id
    second = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision_id,
            package_ac_ids(migrated_session, revision_id),
            rationale="Corrected split.",
            idempotency_key="proposal-2",
        ),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session, second.id, actor=human_actor(), reason="Ok.", idempotency_key="p2-ok"
    )
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, _units(migrated_session, second)[0])

    assert old.unit_key not in {entry.unit_key for entry in ledger.recent}
    assert ledger.abandoned == 0
