import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    ApprovedDecomposition,
    Claim,
    ContextSnapshot,
    DecompositionProposal,
    DecompositionProposalAcMapping,
    Evidence,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import record_adjudication
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)
from tests.services.test_context_preflight import register_context_unit, valid_context
from tests.services.test_dependencies import register_unit
from tests.services.test_package_registration import AUTHORITY
from tests.services.test_package_registration import NOW as APPROVED_AT

NOW = datetime(2026, 7, 5, tzinfo=UTC)


class FixedClock:
    def now(self, session: Session) -> datetime:
        del session
        return NOW


def completion_command(unit: WorkUnit) -> TransitionCommand:
    return TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.COMPLETED,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        expected_version=unit.version,
        idempotency_key=str(uuid.uuid4()),
    )


def submitted_unit(session: Session) -> WorkUnit:
    unit = register_unit(session, "completion")
    unit.state = WorkUnitState.SUBMITTED
    session.commit()
    return unit


def add_adjudication(
    session: Session,
    unit: WorkUnit,
    *,
    ac_id: str = "ac-1",
    outcome: str = "passed",
    supersedes: Adjudication | None = None,
    expires_at: datetime | None = None,
    scope: str | None = None,
    adjudication_id: uuid.UUID | None = None,
    supersedes_id: uuid.UUID | None = None,
) -> Adjudication:
    failed_evidence_id = None
    if outcome == "waived":
        evidence = Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id=ac_id,
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://failed",
            source_revision="abc123",
            recorded_by="worker-1",
            event_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
        )
        session.add(evidence)
        session.flush()
        failed_evidence_id = evidence.id
    adjudication = Adjudication(
        id=adjudication_id,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        outcome=outcome,
        decided_by="human-1",
        rationale="verified",
        failed_evidence_id=failed_evidence_id,
        risk="high" if outcome == "waived" else None,
        follow_up="monitor" if outcome == "waived" else None,
        scope=scope,
        expires_at=expires_at,
        event_id=uuid.uuid4(),
        supersedes_adjudication_id=supersedes_id or (supersedes.id if supersedes else None),
    )
    session.add(adjudication)
    session.flush()
    return adjudication


def assert_completion_rejected(session: Session, unit: WorkUnit) -> None:
    with pytest.raises(DomainError) as error:
        transition_unit(session, completion_command(unit), clock=FixedClock())
    assert error.value.code == "completion_incomplete"


def test_completion_requires_adjudication_for_every_required_ac(
    migrated_session: Session,
) -> None:
    assert_completion_rejected(migrated_session, submitted_unit(migrated_session))


def test_completion_uses_approved_decomposition_ac_mapping(
    migrated_session: Session,
) -> None:
    revision = register_revision(
        migrated_session,
        package_id="mapped-completion",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:mapped",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=APPROVED_AT,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1", "ac-2"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="mapped-unit",
        title="mapped-unit",
        outcome="mapped unit complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    ac_one = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-1",
        condition="first",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    ac_two = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-2",
        condition="second",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=1,
        state="approved",
        rationale="mapped",
        proposed_by="worker",
        proposed_actor_role="worker",
        decided_by="human-1",
        decided_at=NOW,
        idempotency_key="mapped-completion-proposal",
    )
    migrated_session.add_all([ac_one, ac_two, proposal])
    migrated_session.flush()
    migrated_session.add(
        ApprovedDecomposition(
            work_package_revision_id=revision.id,
            proposal_id=proposal.id,
            approved_by="human-1",
            approved_at=NOW,
        )
    )
    migrated_session.add(
        DecompositionProposalAcMapping(
            proposal_id=proposal.id,
            package_acceptance_criterion_id=ac_one.id,
            unit_key=unit.unit_key,
        )
    )
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.flush()
    add_adjudication(migrated_session, unit, outcome="passed")
    migrated_session.commit()

    result = transition_unit(migrated_session, completion_command(unit), clock=FixedClock())

    assert result.state is WorkUnitState.COMPLETED


def test_completion_ignores_adjudications_outside_decomposed_unit_mapping(
    migrated_session: Session,
) -> None:
    revision = register_revision(
        migrated_session,
        package_id="mapped-completion-with-extra-adjudication",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:mapped-extra",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=APPROVED_AT,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1", "ac-2"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="mapped-unit-extra",
        title="mapped-unit-extra",
        outcome="mapped unit complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    ac_one = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-1",
        condition="first",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    ac_two = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-2",
        condition="second",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=1,
        state="approved",
        rationale="mapped",
        proposed_by="worker",
        proposed_actor_role="worker",
        decided_by="human-1",
        decided_at=NOW,
        idempotency_key="mapped-completion-extra-adjudication-proposal",
    )
    migrated_session.add_all([ac_one, ac_two, proposal])
    migrated_session.flush()
    migrated_session.add(
        ApprovedDecomposition(
            work_package_revision_id=revision.id,
            proposal_id=proposal.id,
            approved_by="human-1",
            approved_at=NOW,
        )
    )
    migrated_session.add(
        DecompositionProposalAcMapping(
            proposal_id=proposal.id,
            package_acceptance_criterion_id=ac_one.id,
            unit_key=unit.unit_key,
        )
    )
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.flush()
    add_adjudication(migrated_session, unit, ac_id="ac-1", outcome="passed")
    add_adjudication(migrated_session, unit, ac_id="ac-2", outcome="passed")
    migrated_session.commit()

    result = transition_unit(migrated_session, completion_command(unit), clock=FixedClock())

    assert result.state is WorkUnitState.COMPLETED


def test_completion_does_not_fall_back_to_package_acs_for_unmapped_decomposed_unit(
    migrated_session: Session,
) -> None:
    revision = register_revision(
        migrated_session,
        package_id="support-completion",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:support",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=APPROVED_AT,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1", "ac-2"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    mapped_unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="mapped-unit",
        title="mapped-unit",
        outcome="mapped unit complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    support_unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="support-unit",
        title="support-unit",
        outcome="support unit complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    ac_one = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-1",
        condition="first",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    ac_two = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id="ac-2",
        condition="second",
        evidence_type="test",
        evidence="evidence",
        approver="policy",
    )
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=1,
        state="approved",
        rationale="mapped",
        proposed_by="worker",
        proposed_actor_role="worker",
        decided_by="human-1",
        decided_at=NOW,
        idempotency_key="support-completion-proposal",
    )
    migrated_session.add_all([ac_one, ac_two, proposal])
    migrated_session.flush()
    migrated_session.add(
        ApprovedDecomposition(
            work_package_revision_id=revision.id,
            proposal_id=proposal.id,
            approved_by="human-1",
            approved_at=NOW,
        )
    )
    migrated_session.add_all(
        [
            DecompositionProposalAcMapping(
                proposal_id=proposal.id,
                package_acceptance_criterion_id=ac_one.id,
                unit_key=mapped_unit.unit_key,
            ),
            DecompositionProposalAcMapping(
                proposal_id=proposal.id,
                package_acceptance_criterion_id=ac_two.id,
                unit_key=mapped_unit.unit_key,
            ),
        ]
    )
    support_unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    result = transition_unit(migrated_session, completion_command(support_unit), clock=FixedClock())

    assert result.state is WorkUnitState.COMPLETED


def test_completion_rejects_empty_acceptance_criteria(migrated_session: Session) -> None:
    revision = register_revision(
        migrated_session,
        package_id="empty-acs",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:empty",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=APPROVED_AT,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": []},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="empty-acs",
        title="Empty ACs",
        outcome="complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=1,
        approved_by="human-1",
        approved_at=APPROVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


def test_completion_accepts_one_current_terminal_adjudication(
    migrated_session: Session,
) -> None:
    unit = submitted_unit(migrated_session)
    original = add_adjudication(migrated_session, unit, outcome="failed")
    add_adjudication(migrated_session, unit, supersedes=original)
    migrated_session.commit()

    result = transition_unit(migrated_session, completion_command(unit), clock=FixedClock())

    assert result.state is WorkUnitState.COMPLETED


def test_completion_rejects_duplicate_current_adjudications(
    migrated_session: Session,
) -> None:
    unit = submitted_unit(migrated_session)
    add_adjudication(migrated_session, unit)
    add_adjudication(migrated_session, unit)
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


def test_completion_rejects_cyclic_adjudication_chain(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    adjudication_id = uuid.uuid4()
    add_adjudication(
        migrated_session,
        unit,
        adjudication_id=adjudication_id,
        supersedes_id=adjudication_id,
    )
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


@pytest.mark.parametrize(
    ("expires_at", "scope"),
    [(NOW - timedelta(seconds=1), None), (NOW + timedelta(days=1), "subset")],
)
def test_completion_rejects_invalid_waiver(
    migrated_session: Session,
    expires_at: datetime,
    scope: str | None,
) -> None:
    unit = submitted_unit(migrated_session)
    add_adjudication(
        migrated_session,
        unit,
        outcome="waived",
        expires_at=expires_at,
        scope=scope,
    )
    migrated_session.commit()

    assert_completion_rejected(migrated_session, unit)


@pytest.mark.parametrize(
    ("subject_type", "binding", "allowed"),
    [("action", "1", True), ("action", "0", False), ("work_unit", "1", False)],
)
def test_approval_must_bind_current_unit_version(
    migrated_session: Session,
    subject_type: str,
    binding: str,
    allowed: bool,
) -> None:
    unit = register_unit(migrated_session, "approval")
    unit.state = WorkUnitState.AWAITING_APPROVAL
    approval = Approval(
        subject_type=subject_type,
        subject_id=unit.id,
        subject_revision_or_fingerprint=binding,
        decision="approved",
        approved_by="human-1",
        reason="approved",
        event_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    migrated_session.add(approval)
    migrated_session.commit()
    command = TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.READY,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        expected_version=unit.version,
        idempotency_key=str(uuid.uuid4()),
    )

    if allowed:
        assert transition_unit(migrated_session, command).state is WorkUnitState.READY
    else:
        with pytest.raises(DomainError) as error:
            transition_unit(migrated_session, command)
        assert error.value.code == "approval_required"


def test_worker_start_records_execution_context_snapshot(migrated_session: Session) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "start-context")
    grant = claim_unit(
        migrated_session,
        unit.id,
        ActorContext("worker-1", ActorRole.WORKER),
        "claim-1",
        standing_context=valid_context(),
    )
    assert isinstance(grant, LeaseGrant)

    result = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=ActorContext("worker-1", ActorRole.WORKER),
            expected_version=unit.version,
            idempotency_key="start-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=valid_context(),
        ),
    )

    assert result.state is WorkUnitState.EXECUTING
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.execution_context_snapshot_id is not None


def test_worker_start_defaults_execution_preflight_to_claim_context_snapshot(
    migrated_session: Session,
) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "start-default-previous")
    actor = ActorContext("worker-1", ActorRole.WORKER)
    grant = claim_unit(
        migrated_session,
        unit.id,
        actor,
        "claim-1",
        standing_context=valid_context(),
    )
    assert isinstance(grant, LeaseGrant)

    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=actor,
            expected_version=unit.version,
            idempotency_key="start-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=valid_context(),
        ),
    )

    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.execution_context_snapshot_id is not None
    execution_snapshot = migrated_session.get(ContextSnapshot, claim.execution_context_snapshot_id)
    assert execution_snapshot is not None
    assert execution_snapshot.classification == "accepted"


def test_worker_start_rejects_authority_expanding_context_until_approved(
    migrated_session: Session,
) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "start-expansion")
    actor = ActorContext("worker-1", ActorRole.WORKER)
    grant = claim_unit(
        migrated_session,
        unit.id,
        actor,
        "claim-1",
        standing_context=valid_context(),
    )
    assert isinstance(grant, LeaseGrant)
    expanded = valid_context(capabilities=["python", "deploy"])
    command = TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.EXECUTING,
        actor=actor,
        expected_version=unit.version,
        idempotency_key="start-1",
        attempt=grant.attempt,
        lease_token=grant.lease_token,
        standing_context=expanded,
    )

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, command)
    assert error.value.code == "context_authority_expanding"

    record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approve runtime authority envelope",
        idempotency_key="start-context-approval",
        expected_version=unit.version,
        standing_context=expanded,
    )
    migrated_session.commit()

    result = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=actor,
            expected_version=unit.version,
            idempotency_key="start-2",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            standing_context=expanded,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )

    assert result.state is WorkUnitState.EXECUTING


def test_worker_start_rejects_authority_approval_for_different_context_fingerprint(
    migrated_session: Session,
) -> None:
    unit = register_context_unit(migrated_session, valid_context(), "start-approval-fingerprint")
    actor = ActorContext("worker-1", ActorRole.WORKER)
    grant = claim_unit(
        migrated_session,
        unit.id,
        actor,
        "claim-1",
        standing_context=valid_context(),
    )
    assert isinstance(grant, LeaseGrant)
    approved_context = valid_context(capabilities=["python", "repository_write"])
    requested_context = valid_context(
        capabilities=["python", "repository_write"],
        runtime_version="1.1",
    )
    approval = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approve exact runtime context",
        idempotency_key="start-context-approval",
        expected_version=unit.version,
        standing_context=approved_context,
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.EXECUTING,
                actor=actor,
                expected_version=unit.version,
                idempotency_key="start-1",
                attempt=grant.attempt,
                lease_token=grant.lease_token,
                standing_context=requested_context,
                context_snapshot_id=grant.context_snapshot_id,
            ),
        )

    assert approval.subject_revision_or_fingerprint != unit.authority_fingerprint
    assert error.value.code == "context_authority_expanding"


def judgment_unit(session: Session) -> WorkUnit:
    unit = submitted_unit(session)
    session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id="ac-1",
            condition="condition",
            evidence_type="human.review",
            evidence="evidence",
            approver="human-1",
        )
    )
    session.commit()
    return unit


def human_adjudicates(session: Session, unit: WorkUnit, outcome: str, key: str) -> Adjudication:
    result = record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome=outcome,
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed",
        idempotency_key=key,
    )
    assert isinstance(result, Adjudication)
    return result


def test_human_recorded_failure_blocks_completion(migrated_session: Session) -> None:
    # Spec AC-008. `failed` only became reachable for a HUMAN in WS-P2.17 Increment 2, so this
    # path had never been exercised. It goes through `record_adjudication` rather than inserting
    # an Adjudication row, because the claim under test is that a HUMAN-recorded failure blocks
    # completion -- a hand-built row would prove only that the string "failed" does.
    unit = judgment_unit(migrated_session)
    failure = human_adjudicates(migrated_session, unit, "failed", "human-fail-gate")
    assert failure.decided_by == "human-1"

    migrated_session.refresh(unit)
    assert_completion_rejected(migrated_session, unit)


def test_human_passed_supersedes_a_human_recorded_failure(migrated_session: Session) -> None:
    # Spec AC-009. Rework, then re-adjudication: the completion guard reads the current terminal
    # of the supersession chain, and nothing in it consults WHO recorded an outcome -- which is
    # why AC-008 and AC-009 need no source change. Were the guard to treat a human `failed`
    # differently from a verifier one, this pair would red.
    unit = judgment_unit(migrated_session)
    failure = human_adjudicates(migrated_session, unit, "failed", "human-fail-then-pass")
    migrated_session.refresh(unit)
    assert_completion_rejected(migrated_session, unit)

    recovery = human_adjudicates(migrated_session, unit, "passed", "human-pass-after-fail")
    assert recovery.supersedes_adjudication_id == failure.id

    migrated_session.refresh(unit)
    transition_unit(migrated_session, completion_command(unit), clock=FixedClock())
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.COMPLETED


def test_human_passed_satisfies_completion_gate(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    migrated_session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id="ac-1",
            condition="condition",
            evidence_type="human.review",
            evidence="evidence",
            approver="human-1",
        )
    )
    migrated_session.commit()

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-gate",
    )
    assert isinstance(result, Adjudication)

    migrated_session.refresh(unit)
    transition_unit(migrated_session, completion_command(unit), clock=FixedClock())
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.COMPLETED
