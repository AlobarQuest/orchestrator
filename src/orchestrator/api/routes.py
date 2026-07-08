from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas import (
    AdjudicationCommand,
    AdjudicationResponse,
    ApprovalCommand,
    ApprovalResponse,
    ClaimCommand,
    ContextSnapshotResponse,
    DecompositionDecisionCommand,
    DecompositionProposalAcMappingResponse,
    DecompositionProposalDependencyResponse,
    DecompositionProposalRegistration,
    DecompositionProposalResponse,
    DecompositionProposalRetainedAcResponse,
    DecompositionProposalUnitResponse,
    DependencyCommand,
    DependencyResolutionCommand,
    DependencyResponse,
    DispatchCommandModel,
    DispatchResponse,
    ErrorResponse,
    EventPublicationExportCommand,
    EventPublicationQueueCommand,
    EventPublicationResponse,
    EventPublicationRetryCommand,
    EventResponse,
    EvidenceCommand,
    EvidenceResponse,
    InfraLaneLinkCommandModel,
    InfraLaneLinkResponse,
    LeaseResponse,
    LifecycleCommand,
    PackageAcceptanceCriterionResponse,
    PackageIntakeRegistration,
    PackageIntakeResponse,
    PreflightCommandModel,
    ProposedUnitCommand,
    ReadinessResponse,
    ReclaimCommand,
    RenewCommand,
    RetryCommand,
    RevisionRegistration,
    RevisionResponse,
    RunnerBriefResponse,
    StatusLedgerRowResponse,
    TransitionResponse,
    UnitRegistration,
    UnitResponse,
)
from orchestrator.config import Settings, get_settings
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    ContextSnapshot,
    DecompositionProposal,
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    DecompositionProposalRetainedAc,
    DecompositionProposalUnit,
    Event,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claims import (
    authorize_retry,
    claim_unit,
    reclaim_expired_claim,
    renew_claim,
)
from orchestrator.services.context import PreflightCommand, record_preflight
from orchestrator.services.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    RetainedAc,
    approve_decomposition_proposal,
    reject_decomposition_proposal,
    require_decomposition_revision,
    submit_decomposition_proposal,
)
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubActionsDispatcher,
    dispatch_work_unit,
)
from orchestrator.services.event_publications import (
    EventPublicationFilters,
    export_event_publications,
    list_event_publications,
    queue_event_publications,
    retry_event_publication,
)
from orchestrator.services.evidence import append_evidence, list_evidence, record_adjudication
from orchestrator.services.infra_links import (
    InfraLaneLinkCommand,
    list_infra_lane_links,
    record_infra_lane_link,
)
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_unit,
    unit_history,
)
from orchestrator.services.package_intake import (
    AcceptanceCriterionProjection,
    PackageIntakeCommand,
    register_package_intake,
)
from orchestrator.services.packages import (
    DependencySpec,
    evaluate_readiness,
    record_approval,
    register_approved_unit,
    register_dependency_command,
    register_revision,
    resolve_dependency_command,
)
from orchestrator.services.runner_brief import runner_brief
from orchestrator.services.status_ledger import StatusLedgerFilters, status_ledger

SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required or rejected"},
    403: {"model": ErrorResponse, "description": "Authenticated actor is forbidden"},
    409: {"model": ErrorResponse, "description": "Domain conflict"},
}

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)

COMMAND_TARGETS = {
    "ready": WorkUnitState.READY,
    "start": WorkUnitState.EXECUTING,
    "block": WorkUnitState.BLOCKED,
    "request-approval": WorkUnitState.AWAITING_APPROVAL,
    "approve": WorkUnitState.READY,
    "submit": WorkUnitState.SUBMITTED,
    "verify": WorkUnitState.VERIFYING,
    "review": WorkUnitState.AWAITING_REVIEW,
    "revision-required": WorkUnitState.REVISION_REQUIRED,
    "complete": WorkUnitState.COMPLETED,
    "fail": WorkUnitState.FAILED,
    "retry": WorkUnitState.READY,
    "cancel": WorkUnitState.CANCELLED,
}


def _raise_error(value: object) -> object:
    if isinstance(value, DomainError):
        raise value
    return value


def _require_zero_expected_version(value: int, operation: str) -> None:
    if value != 0:
        raise DomainError(
            "version_conflict",
            f"{operation} requires expected version 0",
            "reload",
            current_version=0,
        )


@router.post("/revisions", response_model=RevisionResponse, status_code=201)
def create_revision(
    body: RevisionRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = register_revision(
        session,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": revision.id, "revision": revision.revision}


@router.post("/package-intakes", response_model=PackageIntakeResponse, status_code=201)
def create_package_intake(
    body: PackageIntakeRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = register_package_intake(
        session,
        PackageIntakeCommand(
            package_id=body.package_id,
            source_repository=body.source_repository,
            revision=body.revision,
            content_hash=body.content_hash,
            source_path=body.source_path,
            source_commit=body.source_commit,
            approved_by=body.approved_by,
            approved_at=body.approved_at,
            approval_event_id=body.approval_event_id,
            approval_ledger_commit=body.approval_ledger_commit,
            profile=body.profile,
            status_at_intake=body.status_at_intake,
            verification_mode=body.verification_mode,
            verification_limitations=body.verification_limitations,
            enforcement_snapshot=body.enforcement_snapshot,
            authority=normalize_authority(body.authority),
            registry_version=body.registry_version,
            acceptance_criteria=tuple(
                AcceptanceCriterionProjection(**criterion.model_dump())
                for criterion in body.acceptance_criteria
            ),
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
            intake_purpose=body.intake_purpose,
        ),
        actor,
    )
    session.commit()
    return _package_intake_payload(session, revision)


@router.get("/package-intakes/{revision_id}", response_model=PackageIntakeResponse)
def package_intake(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = _package_intake_revision_or_raise(session, revision_id)
    return _package_intake_payload(session, revision)


@router.post(
    "/revisions/{revision_id}/work-units",
    response_model=UnitResponse,
    status_code=201,
)
def create_unit(
    revision_id: UUID,
    body: UnitRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    unit = register_approved_unit(
        session,
        revision_id=revision_id,
        **body.model_dump(exclude={"authority"}),
        authority=normalize_authority(body.authority),
        authority_payload=body.authority,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    session.commit()
    return {"id": unit.id, "state": unit.state, "version": unit.version}


@router.post(
    "/package-intakes/{revision_id}/decomposition-proposals",
    response_model=DecompositionProposalResponse,
    status_code=201,
)
def create_decomposition_proposal(
    revision_id: UUID,
    body: DecompositionProposalRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    _require_zero_expected_version(body.expected_version, "decomposition proposal submission")
    proposal = submit_decomposition_proposal(
        session,
        DecompositionProposalCommand(
            work_package_revision_id=revision_id,
            rationale=body.rationale,
            proposed_units=tuple(_proposed_unit(command) for command in body.proposed_units),
            dependencies=tuple(
                ProposedDependency(**command.model_dump()) for command in body.dependencies
            ),
            ac_mappings=tuple(AcMapping(**command.model_dump()) for command in body.ac_mappings),
            retained_acs=tuple(RetainedAc(**command.model_dump()) for command in body.retained_acs),
            idempotency_key=body.idempotency_key,
        ),
        actor,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.get(
    "/package-intakes/{revision_id}/decomposition-proposals",
    response_model=list[DecompositionProposalResponse],
)
def decomposition_proposals(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> list[dict[str, object]]:
    _package_intake_revision_or_raise(session, revision_id)
    proposals = tuple(
        session.scalars(
            select(DecompositionProposal)
            .where(DecompositionProposal.work_package_revision_id == revision_id)
            .order_by(DecompositionProposal.proposal_number)
        )
    )
    payloads = _proposal_payloads(session, proposals)
    return [payloads[proposal.id] for proposal in proposals]


@router.get(
    "/decomposition-proposals/{proposal_id}",
    response_model=DecompositionProposalResponse,
)
def decomposition_proposal(
    proposal_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    proposal = _proposal_or_raise(session, proposal_id)
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.post(
    "/decomposition-proposals/{proposal_id}/approve",
    response_model=DecompositionProposalResponse,
)
def approve_decomposition(
    proposal_id: UUID,
    body: DecompositionDecisionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    _require_zero_expected_version(body.expected_version, "decomposition approval")
    proposal = approve_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.post(
    "/decomposition-proposals/{proposal_id}/reject",
    response_model=DecompositionProposalResponse,
)
def reject_decomposition(
    proposal_id: UUID,
    body: DecompositionDecisionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    _require_zero_expected_version(body.expected_version, "decomposition rejection")
    proposal = reject_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.post(
    "/decomposition-proposals/{proposal_id}/require-revision",
    response_model=DecompositionProposalResponse,
)
def require_decomposition_revision_route(
    proposal_id: UUID,
    body: DecompositionDecisionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    _require_zero_expected_version(body.expected_version, "decomposition revision request")
    proposal = require_decomposition_revision(
        session,
        proposal_id,
        actor=actor,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.get("/work-units/{unit_id}/readiness", response_model=ReadinessResponse)
def readiness(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    result = evaluate_readiness(session, unit_id)
    return {
        "status": result.status,
        "reasons": [
            {"code": reason.code, "subject_id": reason.subject_id, "detail": reason.detail}
            for reason in result.reasons
        ],
    }


@router.get("/work-units/{unit_id}/runner-brief", response_model=RunnerBriefResponse)
def runner_brief_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return runner_brief(session, unit_id)


@router.post("/work-units/{unit_id}/dispatch", response_model=DispatchResponse)
def dispatch_route(
    unit_id: UUID,
    body: DispatchCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    dispatch_settings = DispatchSettings(
        enabled=settings.dispatch_enabled,
        allowed_change_classes=settings.dispatch_allowed_change_classes,
        enabled_capabilities=settings.dispatch_enabled_capabilities,
        target_repository=settings.dispatch_target_repository,
        workflow_id=settings.dispatch_workflow_id,
        workflow_ref=settings.dispatch_workflow_ref,
        github_token=settings.github_dispatch_token,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
        orchestrator_url=settings.dispatch_orchestrator_url,
        human_gate_age_out_seconds=settings.dispatch_human_gate_age_out_seconds,
    )
    dispatcher = GitHubActionsDispatcher(settings.github_dispatch_token or "")
    return dispatch_work_unit(
        session,
        DispatchCommand(
            unit_id=unit_id,
            runner_attempt=body.runner_attempt,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
        dispatch_settings,
        dispatcher,
    )


@router.post(
    "/work-units/{unit_id}/infra-lane-links",
    response_model=InfraLaneLinkResponse,
    status_code=201,
)
def create_infra_lane_link(
    unit_id: UUID,
    body: InfraLaneLinkCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        record_infra_lane_link(
            session,
            InfraLaneLinkCommand(
                work_unit_id=unit_id,
                attempt=body.attempt,
                actor=actor,
                lease_token=body.lease_token,
                status=body.status,
                change_manager_ref=body.change_manager_ref,
                change_manager_url=body.change_manager_url,
                infraops_ref=body.infraops_ref,
                approval_ref=body.approval_ref,
                rollback_ref=body.rollback_ref,
                verify_ref=body.verify_ref,
                final_evidence_ref=body.final_evidence_ref,
                payload=body.payload,
                idempotency_key=body.idempotency_key,
                expected_version=body.expected_version,
            ),
        )
    )


@router.get(
    "/work-units/{unit_id}/infra-lane-links",
    response_model=list[InfraLaneLinkResponse],
)
def infra_lane_links(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(list_infra_lane_links(session, unit_id))


@router.get("/status-ledger", response_model=list[StatusLedgerRowResponse])
def status_ledger_route(
    _actor: ActorDep,
    session: SessionDep,
    actor_id: str | None = None,
    work_unit_id: UUID | None = None,
    state: str | None = None,
    include_inactive: bool = False,
) -> object:
    return status_ledger(
        session,
        StatusLedgerFilters(
            actor_id=actor_id,
            work_unit_id=work_unit_id,
            state=state,
            include_inactive=include_inactive,
        ),
    )


@router.get("/event-publications", response_model=list[EventPublicationResponse])
def event_publications(
    _actor: ActorDep,
    session: SessionDep,
    source_kind: str | None = None,
    source_id: UUID | None = None,
    status: str | None = None,
) -> object:
    return list_event_publications(
        session,
        EventPublicationFilters(source_kind=source_kind, source_id=source_id, status=status),
    )


@router.post("/event-publications/queue", response_model=list[EventPublicationResponse])
def event_publications_queue(
    body: EventPublicationQueueCommand,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return queue_event_publications(
        session,
        source_kind=body.source_kind,
        source_id=body.source_id,
    )


@router.post("/event-publications/export", response_model=list[EventPublicationResponse])
def event_publications_export(
    body: EventPublicationExportCommand,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return export_event_publications(session, Path(body.output_path))


@router.post(
    "/event-publications/{publication_id}/retry",
    response_model=EventPublicationResponse,
)
def event_publications_retry(
    publication_id: UUID,
    _body: EventPublicationRetryCommand,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return retry_event_publication(session, publication_id)


@router.post("/work-units/{unit_id}/preflight", response_model=ContextSnapshotResponse)
def preflight(
    unit_id: UUID,
    body: PreflightCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_preflight(
        session,
        PreflightCommand(
            work_unit_id=unit_id,
            standing_context=body.standing_context,
            previous_context_snapshot_id=body.previous_context_snapshot_id,
            approval_id=body.approval_id,
            purpose=body.purpose,
            idempotency_key=body.idempotency_key,
            attempt=body.attempt,
            lease_token=body.lease_token,
            expected_version=body.expected_version,
        ),
        actor,
    )
    if isinstance(result, DomainError):
        raise result
    session.commit()
    return result


@router.get(
    "/work-units/{unit_id}/context-snapshots",
    response_model=list[ContextSnapshotResponse],
)
def context_snapshots(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    if session.get(WorkUnit, unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(ContextSnapshot)
            .where(ContextSnapshot.work_unit_id == unit_id)
            .order_by(ContextSnapshot.created_at, ContextSnapshot.id)
        )
    )


@router.post("/work-units/{unit_id}/claim", response_model=LeaseResponse)
def claim(
    unit_id: UUID,
    body: ClaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        claim_unit(
            session,
            unit_id,
            actor,
            body.idempotency_key,
            expected_version=body.expected_version,
            standing_context=body.standing_context,
        )
    )


@router.post("/work-units/{unit_id}/renew", response_model=LeaseResponse)
def renew(
    unit_id: UUID,
    body: RenewCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        renew_claim(
            session,
            unit_id,
            actor,
            body.attempt,
            body.lease_token,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        )
    )


@router.post("/work-units/{unit_id}/reclaim-expired-claim", response_model=LeaseResponse)
def reclaim_expired(
    unit_id: UUID,
    body: ReclaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        reclaim_expired_claim(
            session,
            unit_id,
            actor,
            ActorContext(body.next_owner_id, ActorRole.WORKER),
            body.idempotency_key,
            expected_version=body.expected_version,
            standing_context=body.standing_context,
        )
    )


@router.post(
    "/work-units/{unit_id}/commands/{command}",
    response_model=TransitionResponse,
)
def command(
    unit_id: UUID,
    command: str,
    body: LifecycleCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    target = COMMAND_TARGETS.get(command)
    if target is None:
        raise DomainError("command_not_found", "unknown lifecycle command", None)
    return transition_unit(
        session,
        TransitionCommand(
            unit_id=unit_id,
            target=target,
            actor=actor,
            expected_version=body.expected_version,
            idempotency_key=body.idempotency_key,
            attempt=body.attempt,
            lease_token=body.lease_token,
            standing_context=body.standing_context,
            context_snapshot_id=body.context_snapshot_id,
        ),
    )


@router.post("/work-units/{unit_id}/approvals", response_model=ApprovalResponse)
def approval(
    unit_id: UUID,
    body: ApprovalCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_approval(
        session,
        unit_id=unit_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.post("/work-units/{unit_id}/adjudications", response_model=AdjudicationResponse)
def adjudication(
    unit_id: UUID,
    body: AdjudicationCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        record_adjudication(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.post("/work-units/{unit_id}/retry-authorization", response_model=ApprovalResponse)
def retry_authorization(
    unit_id: UUID,
    body: RetryCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(authorize_retry(session, unit_id, actor, **body.model_dump()))


@router.post("/work-units/{unit_id}/dependencies", response_model=DependencyResponse)
def dependency(
    unit_id: UUID,
    body: DependencyCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    values = body.model_dump(exclude={"idempotency_key", "expected_version"})
    result = register_dependency_command(
        session,
        work_unit_id=unit_id,
        spec=DependencySpec(**values),
        actor_id=actor.actor_id,
        actor_role=actor.role,
        expected_version=body.expected_version,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return result


@router.post("/dependencies/{dependency_id}/resolve", response_model=DependencyResponse)
def dependency_resolution(
    dependency_id: UUID,
    body: DependencyResolutionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = resolve_dependency_command(
        session,
        dependency_id=dependency_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.post("/work-units/{unit_id}/evidence", response_model=EvidenceResponse)
def evidence(
    unit_id: UUID,
    body: EvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        append_evidence(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.get("/work-units/{unit_id}/evidence", response_model=list[EvidenceResponse])
def evidence_list(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return list_evidence(session, unit_id)


@router.get("/work-units/{unit_id}/history", response_model=list[EventResponse])
def history(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return unit_history(session, unit_id)


def _revision_or_raise(session: Session, revision_id: UUID) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    return revision


def _package_intake_revision_or_raise(
    session: Session,
    revision_id: UUID,
) -> WorkPackageRevision:
    revision = _revision_or_raise(session, revision_id)
    if revision.intake_source != "package_cli":
        raise DomainError(
            "package_intake_not_found",
            "package intake does not exist for this revision",
            None,
        )
    return revision


def _proposal_or_raise(session: Session, proposal_id: UUID) -> DecompositionProposal:
    proposal = session.get(DecompositionProposal, proposal_id)
    if proposal is None:
        raise DomainError(
            "decomposition_proposal_not_found",
            "decomposition proposal does not exist",
            None,
        )
    return proposal


def _package_intake_payload(
    session: Session,
    revision: WorkPackageRevision,
) -> dict[str, object]:
    acceptance_criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision.id)
            .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
        )
    )
    intake_event = session.scalar(
        select(Event)
        .where(
            Event.subject_type == "work_package_revision",
            Event.subject_id == revision.id,
            Event.action == "package_revision.intake_registered",
        )
        .order_by(Event.occurred_at, Event.id)
    )
    command = intake_event.payload.get("command", {}) if intake_event is not None else {}
    return {
        "id": revision.id,
        "package_id": revision.work_package.package_id,
        "source_repository": revision.work_package.source_repository,
        "revision": revision.revision,
        "content_hash": revision.content_hash,
        "source_path": revision.source_path,
        "source_commit": revision.source_commit,
        "approved_by": revision.approved_by,
        "approved_at": revision.approved_at,
        "approval_event_id": revision.approval_event_id,
        "approval_ledger_commit": revision.approval_ledger_commit,
        "profile": revision.profile,
        "status_at_intake": revision.status_at_intake,
        "intake_source": revision.intake_source,
        "verification_mode": revision.verification_mode,
        "verification_limitations": revision.verification_limitations,
        "enforcement_snapshot": revision.enforcement_snapshot,
        "authority_fingerprint": revision.authority_fingerprint,
        "authority": command.get("authority"),
        "registry_version": revision.registry_version,
        "registered_by": revision.registered_by,
        "registered_at": revision.registered_at,
        "acceptance_criteria": [
            PackageAcceptanceCriterionResponse.model_validate(criterion).model_dump(mode="json")
            for criterion in acceptance_criteria
        ],
    }


def _proposal_payloads(
    session: Session,
    proposals: Sequence[DecompositionProposal],
) -> dict[UUID, dict[str, object]]:
    if not proposals:
        return {}
    proposal_ids = tuple(proposal.id for proposal in proposals)
    units_by_proposal: dict[UUID, list[dict[str, object]]] = defaultdict(list)
    dependencies_by_proposal: dict[UUID, list[dict[str, object]]] = defaultdict(list)
    mappings_by_proposal: dict[UUID, list[DecompositionProposalAcMapping]] = defaultdict(list)
    retained_by_proposal: dict[UUID, list[DecompositionProposalRetainedAc]] = defaultdict(list)

    for unit in session.scalars(
        select(DecompositionProposalUnit)
        .where(DecompositionProposalUnit.proposal_id.in_(proposal_ids))
        .order_by(DecompositionProposalUnit.proposal_id, DecompositionProposalUnit.unit_key)
    ):
        payload = DecompositionProposalUnitResponse.model_validate(unit).model_dump(mode="json")
        payload["authority"] = normalize_authority(unit.authority).normalized()
        units_by_proposal[unit.proposal_id].append(payload)
    for dependency in session.scalars(
        select(DecompositionProposalDependency)
        .where(DecompositionProposalDependency.proposal_id.in_(proposal_ids))
        .order_by(
            DecompositionProposalDependency.proposal_id,
            DecompositionProposalDependency.source_unit_key,
            DecompositionProposalDependency.target_unit_key,
            DecompositionProposalDependency.external_ref,
        )
    ):
        dependencies_by_proposal[dependency.proposal_id].append(
            DecompositionProposalDependencyResponse.model_validate(dependency).model_dump(
                mode="json"
            )
        )
    criterion_ids: set[UUID] = set()
    for mapping in session.scalars(
        select(DecompositionProposalAcMapping)
        .where(DecompositionProposalAcMapping.proposal_id.in_(proposal_ids))
        .order_by(
            DecompositionProposalAcMapping.proposal_id,
            DecompositionProposalAcMapping.unit_key,
            DecompositionProposalAcMapping.package_acceptance_criterion_id,
        )
    ):
        mappings_by_proposal[mapping.proposal_id].append(mapping)
        criterion_ids.add(mapping.package_acceptance_criterion_id)
    for retained in session.scalars(
        select(DecompositionProposalRetainedAc)
        .where(DecompositionProposalRetainedAc.proposal_id.in_(proposal_ids))
        .order_by(
            DecompositionProposalRetainedAc.proposal_id,
            DecompositionProposalRetainedAc.package_acceptance_criterion_id,
        )
    ):
        retained_by_proposal[retained.proposal_id].append(retained)
        criterion_ids.add(retained.package_acceptance_criterion_id)

    criteria_by_id = {
        criterion.id: PackageAcceptanceCriterionResponse.model_validate(criterion)
        for criterion in (
            session.scalars(
                select(PackageAcceptanceCriterion)
                .where(PackageAcceptanceCriterion.id.in_(criterion_ids))
                .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
            )
            if criterion_ids
            else ()
        )
    }

    payloads: dict[UUID, dict[str, object]] = {}
    for proposal in proposals:
        payloads[proposal.id] = {
            "id": proposal.id,
            "work_package_revision_id": proposal.work_package_revision_id,
            "proposal_number": proposal.proposal_number,
            "state": proposal.state,
            "rationale": proposal.rationale,
            "proposed_by": proposal.proposed_by,
            "proposed_actor_role": proposal.proposed_actor_role,
            "proposed_at": proposal.proposed_at,
            "decided_by": proposal.decided_by,
            "decided_at": proposal.decided_at,
            "decision_reason": proposal.decision_reason,
            "created_work_unit_ids": proposal.created_work_unit_ids,
            "proposed_units": units_by_proposal.get(proposal.id, []),
            "dependencies": dependencies_by_proposal.get(proposal.id, []),
            "ac_mappings": [
                DecompositionProposalAcMappingResponse(
                    unit_key=mapping.unit_key,
                    package_acceptance_criterion=criteria_by_id[
                        mapping.package_acceptance_criterion_id
                    ],
                ).model_dump(mode="json")
                for mapping in mappings_by_proposal.get(proposal.id, [])
            ],
            "retained_acs": [
                DecompositionProposalRetainedAcResponse(
                    rationale=retained.rationale,
                    package_acceptance_criterion=criteria_by_id[
                        retained.package_acceptance_criterion_id
                    ],
                ).model_dump(mode="json")
                for retained in retained_by_proposal.get(proposal.id, [])
            ],
        }
    return payloads


def _proposed_unit(command: ProposedUnitCommand) -> ProposedUnit:
    return ProposedUnit(
        unit_key=command.unit_key,
        title=command.title,
        outcome=command.outcome,
        required_capability=command.required_capability,
        authority=normalize_authority(command.authority),
        authority_payload=command.authority,
        max_attempts=command.max_attempts,
    )
