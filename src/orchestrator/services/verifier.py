import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.evidence import (
    append_verifier_evidence,
    current_adjudication,
    current_evidence,
    record_adjudication,
)
from orchestrator.services.lifecycle import (
    TransitionCommand,
    transition_unit,
)
from orchestrator.services.verifier_criteria import load_required_criteria
from orchestrator.services.verifier_evaluators import EvaluationStatus, evaluate_criterion
from orchestrator.services.verifier_named_check import (
    lock_named_check_subject,
    validate_named_check_bindings,
)
from orchestrator.services.verifier_types import (
    VerificationResult,
    VerifierEvaluation,
    VerifierResult,
    VerifyCommand,
)


def verify_work_unit(session: Session, command: VerifyCommand) -> VerifierResult:
    if command.actor.role is not ActorRole.VERIFIER:
        raise DomainError("role_forbidden", "only verifiers may verify work units", None)

    unit = _load_unit(session, command.unit_id)
    revision = _load_revision(session, unit)
    state = WorkUnitState(unit.state)
    if state in {
        WorkUnitState.COMPLETED,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
    }:
        return _replay_terminal_result(session, command, unit, revision)
    if state not in {WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING}:
        raise DomainError(
            "invalid_transition",
            f"{state} is not verifiable",
            "submit",
            current_state=unit.state,
            current_version=unit.version,
        )

    if state is WorkUnitState.SUBMITTED:
        transition_unit(
            session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.VERIFYING,
                actor=command.actor,
                expected_version=command.expected_version,
                idempotency_key=_child_key(command, "transition", "verifying"),
            ),
        )
        unit = _load_unit(session, command.unit_id)
        revision = _load_revision(session, unit)
    elif unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )

    criteria = load_required_criteria(session, unit, revision)
    evaluations = tuple(
        _verify_criterion(session, command, unit, criterion) for criterion in criteria
    )
    evaluations = _stabilize_named_check_evaluations(
        session,
        command,
        unit,
        criteria,
        evaluations,
    )
    target = _target_state(evaluations)
    unit = _load_unit(session, command.unit_id)
    transition = transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=target,
            actor=command.actor,
            expected_version=unit.version,
            idempotency_key=_child_key(command, "transition", target.value),
        ),
    )
    return VerifierResult(
        unit_id=unit.id,
        state=transition.state,
        version=transition.version,
        result=_result_for_state(transition.state),
        evaluations=evaluations,
    )


def _verify_criterion(
    session: Session,
    command: VerifyCommand,
    unit: WorkUnit,
    criterion: PackageAcceptanceCriterion,
) -> VerifierEvaluation:
    evidence = current_evidence(
        session,
        unit.work_package_revision_id,
        unit.id,
        criterion.ac_id,
    )
    status, outcome, reason = evaluate_criterion(criterion, evidence)
    if _is_trusted_named_check(criterion, evidence):
        assert evidence is not None
        binding_failure = validate_named_check_bindings(session, unit, criterion, evidence)
        if binding_failure is not None:
            status, outcome, reason = binding_failure
    return _record_evaluation(
        session,
        command,
        unit,
        criterion,
        evidence,
        status,
        outcome,
        reason,
    )


def _record_evaluation(
    session: Session,
    command: VerifyCommand,
    unit: WorkUnit,
    criterion: PackageAcceptanceCriterion,
    evidence: Evidence | None,
    status: EvaluationStatus,
    outcome: str | None,
    reason: str,
    *,
    key_scope: str | None = None,
) -> VerifierEvaluation:
    if status == "judgment_required":
        return VerifierEvaluation(
            ac_id=criterion.ac_id,
            evidence_type=criterion.evidence_type,
            status=status,
            outcome=None,
            evidence_id=evidence.id if evidence is not None else None,
            finding_evidence_id=None,
            adjudication_id=None,
            reason=reason,
        )

    finding: Evidence | None = None
    adjudication_evidence_id = evidence.id if evidence is not None and outcome == "passed" else None
    failed_evidence_id = evidence.id if evidence is not None and outcome == "failed" else None
    if outcome == "failed":
        finding = _record_verifier_finding(
            session,
            command,
            unit,
            criterion,
            status,
            reason,
            key_scope=key_scope,
        )
        failed_evidence_id = finding.id

    adjudication = record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=criterion.ac_id,
        outcome=outcome or "failed",
        actor=command.actor,
        rationale=reason,
        idempotency_key=_child_key(
            command,
            "adjudication",
            criterion.ac_id,
            *((key_scope,) if key_scope is not None else ()),
        ),
        evidence_id=adjudication_evidence_id,
        failed_evidence_id=failed_evidence_id,
        allow_generated_post_deploy=True,
    )
    if isinstance(adjudication, DomainError):
        raise adjudication
    return VerifierEvaluation(
        ac_id=criterion.ac_id,
        evidence_type=criterion.evidence_type,
        status=status,
        outcome=adjudication.outcome,
        evidence_id=evidence.id if evidence is not None else None,
        finding_evidence_id=finding.id if finding is not None else None,
        adjudication_id=adjudication.id,
        reason=reason,
    )


def _record_verifier_finding(
    session: Session,
    command: VerifyCommand,
    unit: WorkUnit,
    criterion: PackageAcceptanceCriterion,
    status: EvaluationStatus,
    reason: str,
    *,
    key_scope: str | None = None,
) -> Evidence:
    finding = append_verifier_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=criterion.ac_id,
        actor=command.actor,
        evidence_type="verifier.finding",
        stable_ref=f"orchestrator://work-units/{unit.id}/verify/{criterion.ac_id}",
        payload={
            "status": status,
            "reason": reason,
            "criterion": {
                "ac_id": criterion.ac_id,
                "evidence_type": criterion.evidence_type,
            },
        },
        source_revision="ws-5.1",
        idempotency_key=_child_key(
            command,
            "evidence",
            criterion.ac_id,
            *((key_scope,) if key_scope is not None else ()),
        ),
    )
    if isinstance(finding, DomainError):
        raise finding
    return finding


def _stabilize_named_check_evaluations(
    session: Session,
    command: VerifyCommand,
    unit: WorkUnit,
    criteria: tuple[PackageAcceptanceCriterion, ...],
    evaluations: tuple[VerifierEvaluation, ...],
) -> tuple[VerifierEvaluation, ...]:
    stabilized = list(evaluations)
    if not any(evaluation.outcome == "failed" for evaluation in stabilized):
        for index, (criterion, evaluation) in enumerate(zip(criteria, stabilized, strict=True)):
            evidence = _evaluation_evidence(session, evaluation)
            if evaluation.outcome != "passed" or not _is_trusted_named_check(criterion, evidence):
                continue
            assert evidence is not None
            lock_named_check_subject(session, unit.id)
            current = current_evidence(
                session,
                unit.work_package_revision_id,
                unit.id,
                criterion.ac_id,
            )
            if current is None or current.id != evidence.id:
                stabilized[index] = _record_evaluation(
                    session,
                    command,
                    unit,
                    criterion,
                    current or evidence,
                    "failed_closed",
                    "failed",
                    "named-check evidence chain head changed before transition",
                    key_scope="evidence-chain-head",
                )
                break
            binding_failure = validate_named_check_bindings(session, unit, criterion, current)
            if binding_failure is not None:
                stabilized[index] = _record_evaluation(
                    session,
                    command,
                    unit,
                    criterion,
                    current,
                    *binding_failure,
                    key_scope="canonical-binding",
                )
                break

    for criterion, evaluation in zip(criteria, stabilized, strict=True):
        evidence = _evaluation_evidence(session, evaluation)
        if _is_trusted_named_check(criterion, evidence):
            lock_named_check_subject(session, unit.id)
    return tuple(stabilized)


def _evaluation_evidence(
    session: Session,
    evaluation: VerifierEvaluation,
) -> Evidence | None:
    if evaluation.evidence_id is None:
        return None
    return session.get(Evidence, evaluation.evidence_id)


def _is_trusted_named_check(
    criterion: PackageAcceptanceCriterion,
    evidence: Evidence | None,
) -> bool:
    return (
        criterion.evidence_type.strip().lower() == "automated_check"
        and evidence is not None
        and evidence.evidence_type == "verifier.github.named_check"
    )


def _load_unit(session: Session, unit_id: uuid.UUID) -> WorkUnit:
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def _load_revision(session: Session, unit: WorkUnit) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    return revision


def _target_state(evaluations: tuple[VerifierEvaluation, ...]) -> WorkUnitState:
    if any(evaluation.outcome == "failed" for evaluation in evaluations):
        return WorkUnitState.REVISION_REQUIRED
    if any(evaluation.status == "judgment_required" for evaluation in evaluations):
        return WorkUnitState.AWAITING_REVIEW
    return WorkUnitState.COMPLETED


def _result_for_state(state: WorkUnitState) -> VerificationResult:
    if state is WorkUnitState.COMPLETED:
        return "completed"
    if state is WorkUnitState.REVISION_REQUIRED:
        return "revision_required"
    if state is WorkUnitState.AWAITING_REVIEW:
        return "awaiting_review"
    return "failed"


def _replay_terminal_result(
    session: Session,
    command: VerifyCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> VerifierResult:
    state = WorkUnitState(unit.state)
    event = session.scalar(
        select(Event).where(Event.idempotency_key == _child_key(command, "transition", state.value))
    )
    if event is None:
        raise DomainError(
            "invalid_transition",
            f"{state} is not verifiable",
            "submit",
            current_state=unit.state,
            current_version=unit.version,
        )
    criteria = load_required_criteria(session, unit, revision)
    evaluations = tuple(_replay_evaluation(session, unit, criterion) for criterion in criteria)
    return VerifierResult(
        unit_id=unit.id,
        state=state,
        version=unit.version,
        result=_result_for_state(state),
        evaluations=evaluations,
    )


def _replay_evaluation(
    session: Session,
    unit: WorkUnit,
    criterion: PackageAcceptanceCriterion,
) -> VerifierEvaluation:
    evidence = current_evidence(session, unit.work_package_revision_id, unit.id, criterion.ac_id)
    adjudication = current_adjudication(
        session, unit.work_package_revision_id, unit.id, criterion.ac_id
    )
    if adjudication is None:
        return VerifierEvaluation(
            ac_id=criterion.ac_id,
            evidence_type=criterion.evidence_type,
            status="judgment_required",
            outcome=None,
            evidence_id=evidence.id if evidence is not None else None,
            finding_evidence_id=None,
            adjudication_id=None,
            reason=f"{criterion.evidence_type} requires review",
        )
    status: EvaluationStatus = "passed"
    evidence_id = adjudication.evidence_id
    if adjudication.outcome != "passed":
        status, evidence_id = _replay_failure(
            session,
            adjudication.failed_evidence_id,
        )
    return VerifierEvaluation(
        ac_id=criterion.ac_id,
        evidence_type=criterion.evidence_type,
        status=status,
        outcome=adjudication.outcome,
        evidence_id=evidence_id,
        finding_evidence_id=adjudication.failed_evidence_id,
        adjudication_id=adjudication.id,
        reason=adjudication.rationale,
    )


def _replay_failure(
    session: Session,
    failed_evidence_id: uuid.UUID | None,
) -> tuple[EvaluationStatus, uuid.UUID | None]:
    finding = session.get(Evidence, failed_evidence_id) if failed_evidence_id is not None else None
    payload = finding.payload if finding is not None else None
    evidence_id = finding.supersedes_evidence_id if finding is not None else None
    status = payload.get("status") if isinstance(payload, dict) else None
    if status == "failed_closed":
        return ("failed_closed", evidence_id)
    return ("failed", evidence_id)


def _child_key(command: VerifyCommand, *parts: str) -> str:
    return ":".join((command.idempotency_key, *parts))
