import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    DecompositionProposalAcMapping,
    DispatchRecord,
    Evidence,
    PackageAcceptanceCriterion,
    UnitPrBinding,
    WorkUnit,
)
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import append_evidence
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.verifier_evidence import NamedCheckAssertion, NamedCheckEvidenceCommand

NOW = datetime(2026, 7, 8, tzinfo=UTC)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)
AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
TARGET_REPOSITORY = "AlobarQuest/change-manager"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
PR_NUMBER = 26
PR_URL = f"https://github.com/{TARGET_REPOSITORY}/pull/{PR_NUMBER}"
RUN_URL = f"https://github.com/{TARGET_REPOSITORY}/actions/runs/123456789"
AUTOMATED_CHECK_AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
    constraints={"target_repository": TARGET_REPOSITORY},
)


def mapped_submitted_unit(
    session: Session,
    *,
    key: str,
    evidence_type: str = "pytest",
    ac_id: str = "ac-1",
    authority: AuthorityEnvelope = AUTHORITY,
) -> WorkUnit:
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": [ac_id]},
        authority=authority,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    criterion = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id=ac_id,
        condition=f"{ac_id} passes",
        evidence_type=evidence_type,
        evidence="recorded evidence",
        approver="verifier",
    )
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=1,
        state="approved",
        rationale="map unit ACs",
        proposed_by=HUMAN.actor_id,
        proposed_actor_role=HUMAN.role,
        decided_by=HUMAN.actor_id,
        decided_at=NOW,
        idempotency_key=f"{key}-proposal",
    )
    session.add_all([criterion, proposal])
    session.flush()
    session.add(
        ApprovedDecomposition(
            work_package_revision_id=revision.id,
            proposal_id=proposal.id,
            approved_by=HUMAN.actor_id,
            approved_at=NOW,
        )
    )
    session.add(
        DecompositionProposalAcMapping(
            proposal_id=proposal.id,
            package_acceptance_criterion_id=criterion.id,
            unit_key=unit.unit_key,
        )
    )
    unit.state = WorkUnitState.SUBMITTED
    session.commit()
    return unit


def record_worker_evidence(
    session: Session,
    unit: WorkUnit,
    *,
    ac_id: str = "ac-1",
    evidence_type: str = "pytest",
    payload: dict[str, object],
    idempotency_key: str = "worker-evidence",
) -> Evidence:
    unit.state = WorkUnitState.READY
    session.commit()
    grant = claim_unit(session, unit.id, WORKER, f"{idempotency_key}-claim")
    assert isinstance(grant, LeaseGrant)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key=f"{idempotency_key}-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    evidence = append_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=grant.attempt,
        actor=WORKER,
        lease_token=grant.lease_token,
        evidence_type=evidence_type,
        stable_ref=f"artifact://{idempotency_key}",
        payload=payload,
        source_revision="abc123",
        idempotency_key=idempotency_key,
    )
    assert isinstance(evidence, Evidence)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.SUBMITTED,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key=f"{idempotency_key}-submit",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    return evidence


def bind_dispatched_pull_request(session: Session, unit: WorkUnit) -> DispatchRecord:
    record = DispatchRecord(
        work_unit_id=unit.id,
        work_package_revision_id=unit.work_package_revision_id,
        runner_attempt=unit.attempt_count,
        status="dispatched",
        idempotency_key=f"dispatch-{unit.unit_key}",
        target_repository=TARGET_REPOSITORY,
        workflow_id="factory-runner-pilot.yml",
        workflow_ref="main",
        payload={},
    )
    session.add_all(
        [
            record,
            UnitPrBinding(
                work_unit_id=unit.id,
                pr_number=PR_NUMBER,
                head_sha=HEAD_SHA,
                verification_read_head_sha=HEAD_SHA,
                verification_read_attempt=unit.attempt_count,
            ),
        ]
    )
    session.commit()
    return record


def named_check_command(
    unit: WorkUnit,
    dispatch: DispatchRecord,
    *,
    conclusion: str = "success",
    assertions: tuple[NamedCheckAssertion, ...] | None = None,
    idempotency_key: str | None = None,
) -> NamedCheckEvidenceCommand:
    return NamedCheckEvidenceCommand(
        unit_id=unit.id,
        work_package_revision_id=unit.work_package_revision_id,
        ac_id="AC-006",
        dispatch_id=dispatch.id,
        repository=TARGET_REPOSITORY,
        pr_number=PR_NUMBER,
        pr_url=PR_URL,
        head_sha=HEAD_SHA,
        check_name="Quality",
        conclusion=conclusion,
        run_id="123456789",
        run_url=RUN_URL,
        assertions=(
            assertions
            if assertions is not None
            else (
                NamedCheckAssertion("dispatch_target", TARGET_REPOSITORY, TARGET_REPOSITORY),
                NamedCheckAssertion("head_sha", HEAD_SHA, HEAD_SHA),
                NamedCheckAssertion("check_name", "Quality", "Quality"),
                NamedCheckAssertion("ruff", "passed", "passed"),
                NamedCheckAssertion("pyright_error_count", 0, 0),
                NamedCheckAssertion("tests_passed", 105, 105),
            )
        ),
        actor=VERIFIER,
        expected_version=unit.version,
        idempotency_key=idempotency_key or f"named-check-{unit.unit_key}",
    )
