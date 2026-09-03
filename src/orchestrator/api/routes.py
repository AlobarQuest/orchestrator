import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_actor, get_session
from orchestrator.api.schemas import (
    AdjudicationCommand,
    AdjudicationResponse,
    ApprovalCommand,
    ApprovalResponse,
    ChangeRecordWorkResponse,
    ChangeWindowOverrideModel,
    ClaimCommand,
    ConsistencyReportResponse,
    ContextSnapshotResponse,
    CostActualsCommand,
    CostActualsResponse,
    DeadLetterEntryResponse,
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
    DeploymentObservationCommandModel,
    DeploymentObservationResponse,
    DispatchCommandModel,
    DispatchResponse,
    ErrorResponse,
    EstateBranchUpdateCommandModel,
    EstateBranchUpdateResponse,
    EstateLandingAdmissionResponse,
    EstatePrMergeCommandModel,
    EstatePrMergeResponse,
    EventPublicationExportCommand,
    EventPublicationQueueCommand,
    EventPublicationResponse,
    EventPublicationRetryCommand,
    EventResponse,
    EvidenceCommand,
    EvidencePackResponse,
    EvidenceResponse,
    FactoryPolicyResponse,
    FollowUpMintCommand,
    FollowUpMintResponse,
    InertBranchUpdateCommandModel,
    InertBranchUpdateResponse,
    InertLandingAdmissionResponse,
    InertPrMergeCommandModel,
    InertPrMergeResponse,
    InFlightUnitsResponse,
    InfraLaneLinkCommandModel,
    InfraLaneLinkResponse,
    KnowledgePromotionProposalActionResponse,
    KnowledgePromotionProposalCommandModel,
    KnowledgePromotionProposalResponse,
    KnowledgePromotionSubmitCommandModel,
    LeaseResponse,
    LifecycleCommand,
    MachineActivationCandidateResponse,
    ObservationCommandModel,
    ObservationResponse,
    PackageAcceptanceCriterionResponse,
    PackageIntakeRegistration,
    PackageIntakeResponse,
    PrBindingCommand,
    PrBindingResponse,
    PreflightCommandModel,
    PrMergeAdmissionResponse,
    PrMergeCommandModel,
    PrMergeResponse,
    ProposedUnitCommand,
    ReadinessResponse,
    ReclaimCommand,
    ReconciliationDetectCommand,
    ReconciliationDetectResponse,
    RecoverEvidenceCommand,
    RecoverExpiredClaimCommand,
    ReleaseArtifactCommandModel,
    ReleaseArtifactResponse,
    ReleaseEvidencePackResponse,
    RenewCommand,
    RequeueCommand,
    RetryCommand,
    RevisionRegistration,
    RevisionResponse,
    RunnerBriefResponse,
    SloReportResponse,
    StatusLedgerRowResponse,
    TraceabilityResponse,
    TrackerBindingCommand,
    TrackerBindingResponse,
    TrackerReconciliationDetectCommand,
    TransitionResponse,
    UnitRegistration,
    UnitResponse,
    VerifierNamedCheckEvidenceCommandModel,
    VerifyCommandModel,
    VerifyResponse,
)
from orchestrator.change_window_override import ChangeWindowOverride
from orchestrator.config import Settings, get_settings
from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
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
    Observation,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.change_record import ChangeRecordSource, HttpChangeRecordSource
from orchestrator.services.change_record_work import work_for_change_record
from orchestrator.services.claims import (
    authorize_retry,
    claim_unit,
    reclaim_expired_claim,
    recover_expired_claim,
    renew_claim,
    requeue_unit,
)
from orchestrator.services.consistency import check_consistency
from orchestrator.services.context import PreflightCommand, record_preflight
from orchestrator.services.cost_actuals import record_cost_actuals
from orchestrator.services.dead_letter import dead_letter
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
from orchestrator.services.deployment_observations import (
    DeploymentObservationCommand,
    list_deployment_observations,
    record_deployment_observation,
)
from orchestrator.services.dispatch import (
    DispatchCommand,
    DispatchSettings,
    GitHubActionsDispatcher,
    dispatch_work_unit,
)
from orchestrator.services.estate_landing import EstateLandingSource, HttpEstateLandingSource
from orchestrator.services.estate_landing_admission import estate_landing_admission
from orchestrator.services.estate_pr_branch_update import (
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.estate_pr_merge import (
    EstateMergeCommand,
    GitHubEstatePullRequests,
    land_estate_pull_request,
)
from orchestrator.services.event_publications import (
    EventPublicationFilters,
    export_event_publications,
    list_event_publications,
    queue_event_publications,
    retry_event_publication,
)
from orchestrator.services.evidence import (
    append_evidence,
    list_evidence,
    record_adjudication,
    recover_evidence,
    supersede_evidence,
)
from orchestrator.services.evidence_pack import (
    evidence_pack_projection,
    evidence_pack_response,
    render_evidence_pack_markdown,
)
from orchestrator.services.follow_ups import mint_due_follow_ups
from orchestrator.services.github_app import github_app_credentials, token_provider_for
from orchestrator.services.github_checks import CheckObserver, GitHubActionsCheckObserver
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.inert_landing_admission import inert_landing_admission
from orchestrator.services.inert_landing_policy import (
    HttpInertLandingPolicySource,
    InertLandingPolicySource,
)
from orchestrator.services.inert_pr_branch_update import (
    InertBranchUpdateCommand,
    update_inert_pull_request_branch,
)
from orchestrator.services.inert_pr_merge import (
    GitHubInertPullRequests,
    InertMergeCommand,
    land_inert_pull_request,
)
from orchestrator.services.infra_links import (
    InfraLaneLinkCommand,
    list_infra_lane_links,
    record_infra_lane_link,
)
from orchestrator.services.knowledge_promotions import (
    HttpBrainProposalClient,
    KnowledgePromotionProposalCommand,
    KnowledgePromotionProposalFilters,
    KnowledgePromotionSubmitCommand,
    create_knowledge_promotion_proposal,
    list_knowledge_promotion_proposals,
    proposal_actions,
    proposal_state,
    submit_knowledge_promotion_to_brain,
)
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    require_operator_actor,
    transition_unit,
    unit_history,
)
from orchestrator.services.machine_activation import machine_activation_candidates
from orchestrator.services.observations import (
    ObservationCommand,
    ObservationFilters,
    list_observations,
    record_observation,
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
from orchestrator.services.pr_bindings import arm_verification_head, upsert_pr_binding
from orchestrator.services.pr_merge import (
    GitHubPullRequests,
    MergeCommand,
    land_unit_pull_request,
)
from orchestrator.services.pr_merge_admission import pr_merge_admission
from orchestrator.services.reconciliation_detection import (
    ObservedTrackerItem,
    detect_observation_conditions,
    detect_reconciliation_conditions,
    detect_tracker_conditions,
    record_digest_divergence,
)
from orchestrator.services.release_artifacts import (
    ReleaseArtifactCommand,
    list_release_artifacts,
    record_release_artifact,
)
from orchestrator.services.release_evidence_pack import release_evidence_pack_response
from orchestrator.services.runner_brief import runner_brief
from orchestrator.services.slo_report import SloReportFilters, slo_report
from orchestrator.services.status_ledger import StatusLedgerFilters, status_ledger
from orchestrator.services.traceability import TraceabilityAnchor, traceability_response
from orchestrator.services.tracker_bindings import list_tracker_bindings, upsert_tracker_binding
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from orchestrator.services.verifier_evidence import (
    NamedCheckEvidenceCommand,
    record_named_check_evidence,
)

SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[ActorContext, Depends(get_actor)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_check_observer(settings: SettingsDep) -> CheckObserver:
    """Build the thing that asks GitHub how a named check concluded.

    A dependency rather than a call inside the route body so a test can substitute GitHub — the
    same reason the workflow trigger is passed in rather than constructed where it is used. The
    same App installation serves both, resolved through the one definition of "App credentials
    are configured".
    """
    return GitHubActionsCheckObserver(token_provider_for(github_app_credentials(settings)))


CheckObserverDep = Annotated[CheckObserver, Depends(get_check_observer)]


def get_landing_source(settings: SettingsDep) -> EstateLandingSource:
    """Build the thing that asks the estate what landing on a repository's default branch does.

    A dependency rather than a call inside each route body so a test can substitute App Brain, and
    so an unset URL or credential arrives as the empty string the source itself refuses on — never
    as a source that silently answers. One definition, because two routes now ask the same
    question and a second copy is a second place for that empty-string property to be forgotten.
    """
    return HttpEstateLandingSource(
        base_url=settings.app_brain_url,
        read_key=(
            settings.app_brain_read_key.get_secret_value() if settings.app_brain_read_key else ""
        ),
        timeout_seconds=settings.app_brain_timeout_seconds,
    )


LandingSourceDep = Annotated[EstateLandingSource, Depends(get_landing_source)]

# change-manager's own name for the pipeline that proposed changes arrive on -- the source of
# truth is `DEPLOY_SOURCE` in AlobarQuest/change-manager `app/sources.py`, and its listing route
# returns nothing from that pipeline unless it is named. It is resolved HERE rather than in the
# service, alongside the base URL and the credential, because this is where this deployment's
# cross-boundary configuration is read. It is a constant rather than a setting: an operator who
# could change it could only ever make the question unanswerable.
CHANGE_RECORD_PIPELINE: Final = "deploy"


def get_change_record_source(settings: SettingsDep) -> ChangeRecordSource:
    """Build the thing that asks the estate whether this change was routed and approved.

    A dependency for the reason the estate answer's reader is one: a test can substitute
    change-manager, and an unset URL or credential arrives as the empty string the source itself
    refuses on -- never as a source that silently answers.
    """
    return HttpChangeRecordSource(
        base_url=settings.change_record_url,
        token=(
            settings.change_record_token.get_secret_value() if settings.change_record_token else ""
        ),
        pipeline=CHANGE_RECORD_PIPELINE,
        timeout_seconds=settings.change_record_timeout_seconds,
    )


ChangeRecordSourceDep = Annotated[ChangeRecordSource, Depends(get_change_record_source)]


def get_inert_landing_policy_source(settings: SettingsDep) -> InertLandingPolicySource:
    """Build the thing that reads which repositories a person declared landable unattended.

    **The SAME base URL and the SAME credential as the change-record reader above**, because it is
    the same service and the same bearer: every narrow scope change-manager grants is its read
    routes plus whatever that scope may write, and this route is one of those read routes -- so a
    credential that can read a change record can read this. Sharing them means activating the lane
    costs one environment variable rather than a second secret whose absence would be
    indistinguishable from the service being down.

    A dependency for the reason its two siblings are: a test can substitute the service, and an
    unset URL or credential arrives as the empty string the source itself refuses on, never as a
    source that silently answers.
    """
    return HttpInertLandingPolicySource(
        base_url=settings.change_record_url,
        token=(
            settings.change_record_token.get_secret_value() if settings.change_record_token else ""
        ),
        timeout_seconds=settings.change_record_timeout_seconds,
    )


InertLandingPolicySourceDep = Annotated[
    InertLandingPolicySource, Depends(get_inert_landing_policy_source)
]

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


def _parse_datetime_filter(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DomainError("observation_invalid", f"{field} is invalid", None) from error


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


def package_intake_command(body: PackageIntakeRegistration) -> PackageIntakeCommand:
    """Project a validated intake registration onto the service command.

    Shared with the `/review` browser form (`web.py`), which is the only human-reachable way to
    register an intake in production -- ADR-0006. Both entry points must build the SAME command
    from the SAME validated model, or the browser path becomes a second, laxer set of rules; this
    function is what makes "the form is a new way in, not a new set of rules" checkable rather
    than merely asserted.
    """
    return PackageIntakeCommand(
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
        follow_up=body.follow_up,
        change_record_id=body.change_record_id,
    )


@router.post("/package-intakes", response_model=PackageIntakeResponse, status_code=201)
def create_package_intake(
    body: PackageIntakeRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = register_package_intake(session, package_intake_command(body), actor)
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


@router.get("/work-units/{unit_id}/evidence-pack", response_model=EvidencePackResponse)
def evidence_pack_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """WS-P2.5 Increment 1: the structured JSON twin of the `/review` evidence-pack page.

    Authentication-only, deliberately no role gate -- the runner's WORKER credential must be able
    to read its own unit's evidentiary record.
    """
    return evidence_pack_response(evidence_pack_projection(session, unit_id))


@router.get(
    "/work-units/{unit_id}/evidence-pack/markdown",
    response_class=PlainTextResponse,
    include_in_schema=True,
)
def evidence_pack_markdown_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> PlainTextResponse:
    """WS-P2.5 Increment 1: a server-rendered markdown view of the same structured pack.

    Auth-only, no role gate -- identical access as the JSON twin. Rendered purely from
    `EvidencePackResponse`, never from the ORM projection or a template engine, so JSON and
    markdown are always two views of the one structured source.
    """
    pack = evidence_pack_response(evidence_pack_projection(session, unit_id))
    return PlainTextResponse(render_evidence_pack_markdown(pack), media_type="text/markdown")


@router.get(
    "/revisions/{revision_id}/evidence-pack",
    response_model=ReleaseEvidencePackResponse,
)
def release_evidence_pack_route(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """WS-P2.5 Increment 2: the per-release evidence pack -- every unit's pack in a package
    revision, plus that revision's release artifact bindings and deployment observations.

    Authentication-only, no role gate, matching the per-unit evidence-pack route. This JSON is
    FULL-FIDELITY (approver identity + rationale are present) and must never be relayed onto a
    possibly-public surface: the redaction boundary lives in the per-unit markdown relay, which
    this increment deliberately does not build.
    """
    return release_evidence_pack_response(session, revision_id)


@router.get(
    "/work-units/{unit_id}/pr-merge-admission",
    response_model=PrMergeAdmissionResponse,
)
def pr_merge_admission_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0020 Increment 4a: may the factory land this unit's pull request, and if not, why?

    **Report-only. Nothing here acts, and nothing that acts exists yet.** It is served so the
    composed answer can be read against real completed units before anything obeys it.

    Authentication-only, no role gate, matching the evidence-pack routes: it is a read over
    canonical rows plus one read-only question to the estate, and every actor that can read a
    unit's evidentiary record can read this.
    """
    return pr_merge_admission(session, unit_id, landing_source, record_source)


@router.get("/estate-pr-merge-admission", response_model=EstateLandingAdmissionResponse)
def estate_landing_admission_route(
    repository: Annotated[str, Query(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)],
    pr_number: Annotated[int, Query(gt=0)],
    _actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 5b: may this pull request be landed, and if not, why?

    **Report-only.** It is what makes a night that lands nothing legible: every held pull request
    names the condition it misses, and a first pass that reports four held for freshness is the
    condition working rather than the lane failing.

    The credentials are resolved once and fed to both this answer and the act, so the gate can
    never attest to credentials the actor does not hold.
    """
    credentials = github_app_credentials(settings)
    return estate_landing_admission(
        session,
        repository,
        pr_number,
        landing_source,
        record_source,
        GitHubEstatePullRequests(token_provider_for(credentials)),
        enabled=settings.estate_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.post("/estate-pr-merge", response_model=EstatePrMergeResponse)
def estate_pr_merge_route(
    body: EstatePrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 5b: the orchestrator lands a pull request that has no work unit.

    Its caller is a scheduled one, which is why this path has an off-switch where its unit-bound
    sibling deliberately has none. Unconfigured refuses.
    """
    credentials = github_app_credentials(settings)
    gateway = GitHubEstatePullRequests(token_provider_for(credentials))
    record = land_estate_pull_request(
        session,
        EstateMergeCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        record_source,
        enabled=settings.estate_landing_enabled,
        credentials_configured=credentials is not None,
    )
    return record


@router.post("/estate-pr-branch-update", response_model=EstateBranchUpdateResponse)
def estate_pr_branch_update_route(
    body: EstateBranchUpdateCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0019 Increment 6: the lane brings up to date a branch it has itself put behind.

    The same off-switch and the same credentials as the landing, resolved the same way and read
    off the same composed answer -- so a deployment that may not land may not touch a branch
    either, by the term that already says so rather than by a second one.
    """
    credentials = github_app_credentials(settings)
    gateway = GitHubEstatePullRequests(token_provider_for(credentials))
    return update_estate_pull_request_branch(
        session,
        EstateBranchUpdateCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        record_source,
        enabled=settings.estate_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.get("/inert-pr-merge-admission", response_model=InertLandingAdmissionResponse)
def inert_landing_admission_route(
    repository: Annotated[str, Query(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", max_length=300)],
    pr_number: Annotated[int, Query(gt=0)],
    _actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: may this update-bot pull request be landed where landing changes nothing
    already serving, and if not, why?

    **Report-only.** It is what makes a pass that lands nothing legible: every held pull request
    names the condition it misses, and a pass that reports several held for freshness is the
    condition working rather than the lane failing.

    The credentials are resolved once and fed to both this answer and the act, so the gate can
    never attest to credentials the actor does not hold.
    """
    credentials = github_app_credentials(settings)
    return inert_landing_admission(
        session,
        repository,
        pr_number,
        landing_source,
        policy_source,
        GitHubInertPullRequests(token_provider_for(credentials)),
        enabled=settings.inert_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.post("/inert-pr-merge", response_model=InertPrMergeResponse)
def inert_pr_merge_route(
    body: InertPrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: the orchestrator lands a pull request the update bot opened.

    Its caller is a scheduled one, which is why this path has an off-switch. Unconfigured refuses,
    and the switch is its own rather than the deploying lane's: the two were activated by different
    decisions and neither implies the other.
    """
    credentials = github_app_credentials(settings)
    gateway = GitHubInertPullRequests(token_provider_for(credentials))
    return land_inert_pull_request(
        session,
        InertMergeCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        policy_source,
        enabled=settings.inert_landing_enabled,
        credentials_configured=credentials is not None,
    )


@router.post("/inert-pr-branch-update", response_model=InertBranchUpdateResponse)
def inert_pr_branch_update_route(
    body: InertBranchUpdateCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    policy_source: InertLandingPolicySourceDep,
) -> object:
    """ADR-0038 part 2: the lane brings up to date a branch it has itself put behind.

    The same off-switch and the same credentials as the landing, resolved the same way and read off
    the same composed answer -- so a deployment that may not land may not touch a branch either, by
    the term that already says so rather than by a second one.
    """
    credentials = github_app_credentials(settings)
    gateway = GitHubInertPullRequests(token_provider_for(credentials))
    return update_inert_pull_request_branch(
        session,
        InertBranchUpdateCommand(
            repository=body.repository,
            pr_number=body.pr_number,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_head_sha=body.expected_head_sha,
        ),
        gateway,
        landing_source,
        policy_source,
        enabled=settings.inert_landing_enabled,
        credentials_configured=credentials is not None,
    )


def _change_window_override(
    body: ChangeWindowOverrideModel | None,
) -> ChangeWindowOverride | None:
    """Turn what a caller sent into the type the acts honour, or refuse it by name.

    Absent means no override, which is the default and stays fail-closed. Present with nothing to
    say is the shape this refuses: `ChangeWindowOverride` cannot be constructed without a reason,
    so the `DomainError` is raised by the type rather than here, and it is raised for every caller
    rather than for this one. The refusal reaches the caller as a named error because that is what
    has a registered handler; a constrained pydantic field would have answered the same request
    with a 422 naming a field location, which tells an operator less about what to do next.

    It happens HERE, before either service is entered, so a malformed request can never be
    answered by an idempotent replay of an earlier record.
    """
    if body is None:
        return None
    return ChangeWindowOverride(reason=body.reason or "")


@router.post("/work-units/{unit_id}/pr-merge", response_model=PrMergeResponse)
def pr_merge_route(
    unit_id: UUID,
    body: PrMergeCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
    record_source: ChangeRecordSourceDep,
) -> object:
    """ADR-0020 Increment 4b: the factory lands its own pull request.

    **Its caller is whoever drives verification, immediately afterwards.** Nothing in this
    repository is scheduled except the landing ledger, and this is deliberately not the exception:
    a scheduled closer is its own increment with its own decision, and the no-off-switch ruling is
    explicitly void if one is ever proposed.

    One credential resolution feeds the gateway, exactly as the workflow trigger does — so the
    thing that acts and the thing that reports what it may do can never disagree about which
    credentials are in play.
    """
    # One resolution feeds both the gate and the actor, so the gate can never attest to
    # credentials the gateway does not actually hold — the rule the workflow trigger states.
    credentials = github_app_credentials(settings)
    return land_unit_pull_request(
        session,
        MergeCommand(
            unit_id=unit_id,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
            change_window_override=_change_window_override(body.change_window_override),
        ),
        GitHubPullRequests(token_provider_for(credentials)),
        landing_source,
        record_source,
        credentials is not None,
    )


@router.post("/work-units/{unit_id}/dispatch", response_model=DispatchResponse)
def dispatch_route(
    unit_id: UUID,
    body: DispatchCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
    landing_source: LandingSourceDep,
) -> object:
    # One resolution feeds both the admission gate and the minter, so the gate can never
    # attest to credentials the dispatcher does not actually use.
    credentials = github_app_credentials(settings)
    dispatch_settings = DispatchSettings(
        enabled=settings.dispatch_enabled,
        allowed_change_classes=settings.dispatch_allowed_change_classes,
        enabled_capabilities=settings.dispatch_enabled_capabilities,
        allowed_target_repositories=settings.dispatch_allowed_target_repositories,
        workflow_id=settings.dispatch_workflow_id,
        workflow_ref=settings.dispatch_workflow_ref,
        github_app_configured=credentials is not None,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
        orchestrator_url=settings.dispatch_orchestrator_url,
    )
    dispatcher = GitHubActionsDispatcher(token_provider_for(credentials))
    return dispatch_work_unit(
        session,
        DispatchCommand(
            unit_id=unit_id,
            runner_attempt=body.runner_attempt,
            actor=actor,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
            change_window_override=_change_window_override(body.change_window_override),
        ),
        dispatch_settings,
        dispatcher,
        landing_source,
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


@router.post(
    "/work-units/{unit_id}/release-artifacts",
    response_model=ReleaseArtifactResponse,
    status_code=201,
)
def create_release_artifact(
    unit_id: UUID,
    body: ReleaseArtifactCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        record_release_artifact(
            session,
            ReleaseArtifactCommand(
                work_unit_id=unit_id,
                actor=actor,
                package_revision_id=body.package_revision_id,
                package_revision_hash=body.package_revision_hash,
                source_repository=body.source_repository,
                implementation_pr_number=body.implementation_pr_number,
                source_commit=body.source_commit,
                merge_commit=body.merge_commit,
                kind=body.kind,
                artifact_registry=body.artifact_registry,
                artifact_repository=body.artifact_repository,
                artifact_name=body.artifact_name,
                artifact_digest=body.artifact_digest,
                artifact_tag=body.artifact_tag,
                workflow_run_id=body.workflow_run_id,
                workflow_run_attempt=body.workflow_run_attempt,
                workflow_path=body.workflow_path,
                workflow_ref=body.workflow_ref,
                workflow_run_url=body.workflow_run_url,
                builder_id=body.builder_id,
                builder_class=body.builder_class,
                provenance_ref=body.provenance_ref,
                provenance_digest=body.provenance_digest,
                sbom_ref=body.sbom_ref,
                sbom_digest=body.sbom_digest,
                summary=body.summary,
                idempotency_key=body.idempotency_key,
                expected_version=body.expected_version,
            ),
        )
    )


@router.get(
    "/work-units/{unit_id}/release-artifacts",
    response_model=list[ReleaseArtifactResponse],
)
def release_artifacts(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(list_release_artifacts(session, unit_id))


@router.get(
    "/machine-activation-candidates",
    response_model=list[MachineActivationCandidateResponse],
)
def machine_activation_candidates_route(
    repository: str,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """ADR-0030: which completed units a machine-local working copy could bind an artifact for.

    Authentication-only, matching the other read surfaces. Read-only: it writes nothing and
    asserts nothing about the machine. A repository with no confirmed landings answers with an
    empty list rather than a 404 -- the ordinary state of a repository the factory has not landed
    into yet, which a 404 would make indistinguishable from a misspelled name.
    """
    return list(machine_activation_candidates(session, repository))


@router.post(
    "/release-artifacts/{binding_id}/deployment-observations",
    response_model=DeploymentObservationResponse,
    status_code=201,
)
def create_deployment_observation(
    binding_id: UUID,
    body: DeploymentObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_deployment_observation(
        session,
        DeploymentObservationCommand(
            release_artifact_binding_id=binding_id,
            actor=actor,
            environment=body.environment,
            base_url=body.base_url,
            observed_artifact_digest=body.observed_artifact_digest,
            deployment_ref=body.deployment_ref,
            deployment_url=body.deployment_url,
            deployer=body.deployer,
            observed_at=body.observed_at,
            kind=body.kind,
            activation_summary=body.activation_summary,
            probe_summary=body.probe_summary,
            route_summary=body.route_summary,
            auth_summary=body.auth_summary,
            dispatch_summary=body.dispatch_summary,
            status_summary=body.status_summary,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # The digest guard RAISES and the ingest service rolls back, so a condition written in there
    # would be erased with the rejected observation. Record it here instead, in its own
    # transaction -- and the ingest stays rejected.
    if isinstance(result, DomainError) and result.code == "deployment_observation_digest_mismatch":
        record_digest_divergence(
            session,
            actor=actor,
            release_artifact_binding_id=binding_id,
            observed_artifact_digest=body.observed_artifact_digest,
            environment=body.environment,
        )
    return _raise_error(result)


@router.get(
    "/release-artifacts/{binding_id}/deployment-observations",
    response_model=list[DeploymentObservationResponse],
)
def deployment_observations(
    binding_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(list_deployment_observations(session, binding_id))


@router.post("/observations", response_model=ObservationResponse, status_code=201)
def create_observation(
    body: ObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_observation(
        session,
        ObservationCommand(
            actor=actor,
            source_system=body.source_system,
            source_reference=body.source_reference,
            source_url=body.source_url,
            trust_classification=body.trust_classification,
            subject_type=body.subject_type,
            subject_reference=body.subject_reference,
            environment=body.environment,
            observation_type=body.observation_type,
            status=body.status,
            severity=body.severity,
            observed_at=body.observed_at,
            summary=body.summary,
            facts=body.facts,
            payload_digest=body.payload_digest,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # Post-commit, in the NEXT transaction: record_observation has already committed, so a
    # rejected ingest never reaches detection and a detection failure cannot roll the
    # observation back. Detection never raises, so a forged correlation cannot turn a valid
    # observation into a rejected ingest.
    if isinstance(result, Observation):
        detect_observation_conditions(session, result, actor)
    return _raise_error(result)


@router.post(
    "/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
    response_model=EvidenceResponse,
    status_code=201,
)
def recover_evidence_route(
    unit_id: UUID,
    attempt: int,
    body: RecoverEvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-004. SYSTEM/operator only -- never the expired worker."""
    return _raise_error(
        recover_evidence(
            session,
            work_unit_id=unit_id,
            attempt=attempt,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.post("/work-units/{unit_id}/requeue", response_model=UnitResponse)
def requeue(
    unit_id: UUID,
    body: RequeueCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-006. SYSTEM recovery for the NOT-exhausted case; `retry` owns the exhausted one."""
    return _raise_error(requeue_unit(session, unit_id, actor, **body.model_dump()))


@router.post("/work-units/{unit_id}/pr-binding", response_model=PrBindingResponse)
def pr_binding(
    unit_id: UUID,
    body: PrBindingCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """The worker reports the PR it opened, and re-reports the head after every push.

    This is the orchestrator's EXPECTATION of the pull request. It is deliberately written by our
    own side of the ledger and never derived from an observation: if observed reality wrote the
    expectation, an attacker's push would silently become the expected head and no divergence
    could ever fire.
    """
    _require_zero_expected_version(body.expected_version, "pr binding")
    return _raise_error(
        upsert_pr_binding(
            session,
            actor=actor,
            work_unit_id=unit_id,
            pr_number=body.pr_number,
            head_sha=body.head_sha,
            attempt=body.attempt,
            lease_token=body.lease_token,
        )
    )


@router.post("/work-units/{unit_id}/tracker-binding", response_model=TrackerBindingResponse)
def tracker_binding(
    unit_id: UUID,
    body: TrackerBindingCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Record the tracker item a work unit is projected onto. Projection only, SYSTEM-written.

    Deliberately written by our own side of the ledger: the tracker is never canonical, so a
    binding never derives from tracker content and never changes the unit's state.
    """
    _require_zero_expected_version(body.expected_version, "tracker binding")
    return _raise_error(
        upsert_tracker_binding(
            session,
            actor=actor,
            work_unit_id=unit_id,
            tracker_system=body.tracker_system,
            external_item_id=body.external_item_id,
            external_url=body.external_url,
            projected_state=body.projected_state,
        )
    )


@router.post("/reconciliation/detect", response_model=ReconciliationDetectResponse)
def reconciliation_detect(
    body: ReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """AC-003. Operator/runner-invoked: creates no unit and sets no lifecycle state."""
    _require_zero_expected_version(body.expected_version, "reconciliation detection")
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run reconciliation detection",
            None,
        )
    return detect_reconciliation_conditions(
        session,
        actor,
        stall_seconds=settings.reconcile_split_brain_stall_seconds,
    ).as_dict()


@router.post("/reconciliation/tracker-detect", response_model=ReconciliationDetectResponse)
def tracker_reconciliation_detect(
    body: TrackerReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Inbound tracker reconciliation. SYSTEM-only, report-only: records append-only divergence
    conditions, creates no unit and sets no lifecycle state. The tracker is never canonical."""
    _require_zero_expected_version(body.expected_version, "tracker reconciliation detection")
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run tracker reconciliation detection",
            None,
        )
    return detect_tracker_conditions(
        session,
        actor,
        observed_states=[
            ObservedTrackerItem(i.tracker_system, i.external_item_id, i.observed_completed)
            for i in body.observed_states
        ],
    ).as_dict()


@router.post("/follow-ups/mint", response_model=FollowUpMintResponse)
def mint_follow_ups(
    body: FollowUpMintCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """Mint the work units whose package-declared follow-up reviews have come due.

    SYSTEM-only, externally invoked, and a pure database read plus an append-only write: no
    outbound call, no loop, nothing scheduled. The role gate lives in the service.
    """
    _require_zero_expected_version(body.expected_version, "follow-up minting")
    result = mint_due_follow_ups(
        session,
        actor=actor,
        due_after_days=settings.follow_up_due_after_days,
    )
    return {
        "minted": [
            {
                "work_unit_id": row.work_unit_id,
                "work_package_revision_id": row.work_package_revision_id,
                "due_at": row.due_at,
            }
            for row in result.minted
        ],
        "skipped": [
            {"work_package_revision_id": row.work_package_revision_id, "reason": row.reason}
            for row in result.skipped
        ],
        "considered": result.considered,
    }


@router.get("/observations", response_model=list[ObservationResponse])
def observations(
    _actor: ActorDep,
    session: SessionDep,
    source_system: str | None = None,
    subject_type: str | None = None,
    subject_reference: str | None = None,
    observation_type: str | None = None,
    environment: str | None = None,
    observed_from: str | None = None,
    observed_to: str | None = None,
) -> object:
    return _raise_error(
        list_observations(
            session,
            ObservationFilters(
                source_system=source_system,
                subject_type=subject_type,
                subject_reference=subject_reference,
                observation_type=observation_type,
                environment=environment,
                observed_from=_parse_datetime_filter(observed_from, "observed_from"),
                observed_to=_parse_datetime_filter(observed_to, "observed_to"),
            ),
        )
    )


@router.post(
    "/knowledge-promotion-proposals",
    response_model=KnowledgePromotionProposalResponse,
    status_code=201,
)
def create_knowledge_promotion(
    body: KnowledgePromotionProposalCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = _raise_error(
        create_knowledge_promotion_proposal(
            session,
            KnowledgePromotionProposalCommand(
                actor=actor,
                correlation_identity=body.correlation_identity,
                source_observation_ids=body.source_observation_ids,
                release_artifact_binding_id=body.release_artifact_binding_id,
                deployment_observation_id=body.deployment_observation_id,
                work_unit_id=body.work_unit_id,
                package_revision_id=body.package_revision_id,
                correlation_summary=body.correlation_summary,
                target_brain=body.target_brain,
                target_type=body.target_type,
                authority=body.authority,
                applicability=body.applicability,
                proposed_payload=body.proposed_payload,
                provenance=body.provenance,
                idempotency_key=body.idempotency_key,
                expected_version=body.expected_version,
            ),
        )
    )
    return _knowledge_promotion_response(session, result)


@router.get(
    "/knowledge-promotion-proposals",
    response_model=list[KnowledgePromotionProposalResponse],
)
def knowledge_promotions(
    _actor: ActorDep,
    session: SessionDep,
    target_brain: str | None = None,
    target_type: str | None = None,
    state: str | None = None,
) -> object:
    return [
        _knowledge_promotion_response(session, row)
        for row in list_knowledge_promotion_proposals(
            session,
            KnowledgePromotionProposalFilters(
                target_brain=target_brain,
                target_type=target_type,
                state=state,
            ),
        )
    ]


@router.post(
    "/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
    response_model=KnowledgePromotionProposalActionResponse,
)
def submit_knowledge_promotion(
    proposal_id: UUID,
    body: KnowledgePromotionSubmitCommandModel,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    client = HttpBrainProposalClient(
        target_urls=settings.brain_proposal_target_urls,
        credentials=settings.brain_proposal_credentials,
        timeout_seconds=settings.brain_proposal_timeout_seconds,
    )
    return _raise_error(
        submit_knowledge_promotion_to_brain(
            session,
            KnowledgePromotionSubmitCommand(
                actor=actor,
                proposal_id=proposal_id,
                idempotency_key=body.idempotency_key,
                expected_version=body.expected_version,
            ),
            client,
        )
    )


@router.get("/in-flight-units", response_model=InFlightUnitsResponse)
def in_flight_units(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-009. The reconciliation runner's read surface. Read-only."""
    require_operator_actor(actor)
    return in_flight_snapshot(session)


@router.get("/factory-policy", response_model=FactoryPolicyResponse)
def factory_policy_route(actor: ActorDep) -> object:
    """WS-P2.18. The policy this running process is enforcing, read from the artifact per request.

    Merged is not deployed: a policy that is correct on `main` says nothing about the image serving
    traffic, and this is the surface that answers what that image actually holds. The artifact is
    re-read on every call, so an operator sees the bytes in force now rather than the ones loaded
    at start-up.
    """
    require_operator_actor(actor)
    return load_factory_policy().report()


@router.get("/consistency-check", response_model=ConsistencyReportResponse)
def consistency_check(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-008. Reports projection-vs-source divergence. Never repairs."""
    require_operator_actor(actor)
    return check_consistency(session)


@router.get("/dead-letter", response_model=list[DeadLetterEntryResponse])
def dead_letter_route(
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """Read-only: terminal failures AND stalled approval gates made visible.

    The stalled-approval threshold takes no parameter and has no off switch (WS-P2.15).
    """
    require_operator_actor(actor)
    return dead_letter(
        session,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
        stalled_approval_seconds=settings.dead_letter_stalled_approval_seconds,
        stalled_verification_seconds=settings.dead_letter_stalled_verification_seconds,
    )


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


@router.get("/tracker-bindings", response_model=list[TrackerBindingResponse])
def tracker_bindings_route(
    _actor: ActorDep,
    session: SessionDep,
    tracker_system: str | None = None,
) -> object:
    return list_tracker_bindings(session, tracker_system=tracker_system)


@router.get("/slo-report", response_model=SloReportResponse)
def slo_report_route(
    _actor: ActorDep,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> object:
    return slo_report(session, SloReportFilters(since=since, until=until))


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
    "/work-units/{unit_id}/recover-expired-claim",
    response_model=UnitResponse,
)
def recover_expired(
    unit_id: UUID,
    body: RecoverExpiredClaimCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        recover_expired_claim(
            session,
            unit_id,
            actor,
            body.idempotency_key,
            expected_version=body.expected_version,
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
            reason=body.reason,
            standing_context=body.standing_context,
            context_snapshot_id=body.context_snapshot_id,
        ),
        # Submitting is the moment the worker hands a head over to be adjudicated, so it is the
        # moment the divergence alarm is armed -- in the SAME transaction, holding the unit row
        # lock. Arming later, at verify time, would re-read the head and silently adopt any push
        # that landed in between as the new expectation, which is exactly what must never happen.
        after=(
            (lambda db, unit: arm_verification_head(db, unit, actor=actor))
            if target is WorkUnitState.SUBMITTED
            else None
        ),
    )


@router.post("/work-units/{unit_id}/verify", response_model=VerifyResponse)
def verify(
    unit_id: UUID,
    body: VerifyCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return verify_work_unit(
        session,
        VerifyCommand(
            unit_id=unit_id,
            actor=actor,
            expected_version=body.expected_version,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post(
    "/work-units/{unit_id}/verifier-evidence/named-check",
    response_model=EvidenceResponse,
)
def record_verifier_named_check_evidence(
    unit_id: UUID,
    body: VerifierNamedCheckEvidenceCommandModel,
    actor: ActorDep,
    session: SessionDep,
    observer: CheckObserverDep,
) -> object:
    return _raise_error(
        record_named_check_evidence(
            session,
            NamedCheckEvidenceCommand(
                unit_id=unit_id,
                work_package_revision_id=body.work_package_revision_id,
                ac_id=body.ac_id,
                dispatch_id=body.dispatch_id,
                repository=body.repository,
                pr_number=body.pr_number,
                pr_url=body.pr_url,
                head_sha=body.head_sha,
                check_name=body.check_name,
                expected_conclusion=body.expected_conclusion,
                actor=actor,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
            ),
            observer,
        )
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


@router.post("/work-units/{unit_id}/cost-actuals", response_model=CostActualsResponse)
def cost_actuals(
    unit_id: UUID,
    body: CostActualsCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """The runner reports the actual LLM cost of one attempt (WS-P2.4 Increment 1).

    Claim-gated exactly like evidence/pr-binding: a worker must prove it holds this unit's live
    claim. Emitted before the terminal fail/submit transition so the lease is still valid.
    """
    _require_zero_expected_version(body.expected_version, "cost actuals")
    event = record_cost_actuals(
        session,
        actor=actor,
        work_unit_id=unit_id,
        attempt=body.attempt,
        lease_token=body.lease_token,
        cost_known=body.cost_known,
        llm_calls=body.llm_calls,
        num_turns=body.num_turns,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost_usd=body.cost_usd,
        idempotency_key=body.idempotency_key,
    )
    return CostActualsResponse(
        work_unit_id=unit_id,
        attempt=body.attempt,
        event_id=event.id,
        cost_known=body.cost_known,
    )


@router.post("/work-units/{unit_id}/evidence", response_model=EvidenceResponse)
def evidence(
    unit_id: UUID,
    body: EvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    fields = body.model_dump()
    store = supersede_evidence if fields.pop("supersede") else append_evidence
    return _raise_error(
        store(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **fields,
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


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@router.get(
    "/change-records/{change_record_id}/work",
    response_model=ChangeRecordWorkResponse,
)
def change_record_work_route(
    change_record_id: int,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """ADR-0029: what work a change record caused, and whether all of it is done.

    Authentication-only, no role gate, matching the other read surfaces. Read-only: it writes
    nothing and decides nothing about the record, which lives in another service entirely.

    A record with no revision answers 200 with an empty list and a false verdict rather than 404.
    That is the ordinary state of every record a person has approved and the carry has not yet
    reached, so a 404 would make the common case indistinguishable from a broken identifier.
    """
    answer = work_for_change_record(session, change_record_id)
    return {
        "change_record_id": answer.change_record_id,
        "revision_ids": list(answer.revision_ids),
        "units": [
            {
                "unit_id": unit.unit_id,
                "unit_key": unit.unit_key,
                "revision_id": unit.revision_id,
                "state": unit.state,
            }
            for unit in answer.units
        ],
        "all_units_completed": answer.all_units_completed,
    }


@router.get("/traceability", response_model=TraceabilityResponse)
def traceability_route(
    _actor: ActorDep,
    session: SessionDep,
    work_unit_id: str | None = None,
    revision_id: str | None = None,
    artifact_digest: str | None = None,
    commit: str | None = None,
    pr_number: int | None = None,
    source_repository: str | None = None,
    environment: str | None = None,
) -> object:
    """WS-P2.6: the intent -> unit -> PR -> commit -> artifact -> deployment -> observation chain.

    Authentication-only, no role gate, matching the other read surfaces this composes
    (evidence-pack, release-artifacts, deployment-observations). Read-only: it writes nothing.
    """
    anchor = _parse_traceability_anchor(
        work_unit_id=work_unit_id,
        revision_id=revision_id,
        artifact_digest=artifact_digest,
        commit=commit,
        pr_number=pr_number,
        source_repository=source_repository,
        environment=environment,
    )
    return traceability_response(session, anchor)


def _parse_traceability_anchor(
    *,
    work_unit_id: str | None,
    revision_id: str | None,
    artifact_digest: str | None,
    commit: str | None,
    pr_number: int | None,
    source_repository: str | None,
    environment: str | None,
) -> TraceabilityAnchor:
    active = [
        (kind, value)
        for kind, value in (
            ("work_unit", work_unit_id),
            ("revision", revision_id),
            ("artifact_digest", artifact_digest),
            ("commit", commit),
            ("pr", pr_number),
            ("environment", environment),
        )
        if value is not None
    ]
    if not active:
        raise DomainError("traceability_anchor_required", "provide exactly one anchor", None)
    if len(active) > 1:
        raise DomainError("traceability_anchor_ambiguous", "provide exactly one anchor", None)
    if source_repository is not None and pr_number is None:
        raise DomainError(
            "traceability_anchor_invalid", "source_repository requires pr_number", None
        )
    kind, value = active[0]
    return _ANCHOR_BUILDERS[kind](value, source_repository)


def _build_work_unit_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="work_unit", work_unit_id=_parse_uuid(value, "work_unit_id"))


def _build_revision_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="revision", revision_id=_parse_uuid(value, "revision_id"))


def _build_commit_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    if _COMMIT_RE.fullmatch(value) is None:
        raise DomainError("invalid_commit", "commit must be a 40-char hex sha", None)
    return TraceabilityAnchor(kind="commit", commit=value)


def _build_pr_anchor(value: object, source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, int)
    if value <= 0:
        raise DomainError("invalid_pr_number", "pr_number must be positive", None)
    return TraceabilityAnchor(kind="pr", pr_number=value, source_repository=source_repository)


def _build_artifact_digest_anchor(
    value: object, _source_repository: str | None
) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="artifact_digest", artifact_digest=value)


def _build_environment_anchor(value: object, _source_repository: str | None) -> TraceabilityAnchor:
    assert isinstance(value, str)
    return TraceabilityAnchor(kind="environment", environment=value)


_ANCHOR_BUILDERS = {
    "work_unit": _build_work_unit_anchor,
    "revision": _build_revision_anchor,
    "artifact_digest": _build_artifact_digest_anchor,
    "commit": _build_commit_anchor,
    "pr": _build_pr_anchor,
    "environment": _build_environment_anchor,
}


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise DomainError(f"invalid_{field}", f"{field} must be a UUID", None) from None


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
        "follow_up": revision.follow_up,
        "change_record_id": revision.change_record_id,
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


def _knowledge_promotion_response(session: Session, proposal) -> dict[str, object]:
    return {
        "id": proposal.id,
        "correlation_identity": proposal.correlation_identity,
        "source_observation_ids": proposal.source_observation_ids,
        "source_observation_hashes": proposal.source_observation_hashes,
        "release_artifact_binding_id": proposal.release_artifact_binding_id,
        "deployment_observation_id": proposal.deployment_observation_id,
        "work_unit_id": proposal.work_unit_id,
        "package_revision_id": proposal.package_revision_id,
        "correlation_summary": proposal.correlation_summary,
        "target_brain": proposal.target_brain,
        "target_type": proposal.target_type,
        "authority": proposal.authority,
        "applicability": proposal.applicability,
        "proposed_payload": proposal.proposed_payload,
        "provenance": proposal.provenance,
        "proposal_hash": proposal.proposal_hash,
        "proposed_by": proposal.proposed_by,
        "proposed_at": proposal.proposed_at,
        "event_id": proposal.event_id,
        "idempotency_key": proposal.idempotency_key,
        "state": proposal_state(session, proposal.id),
        "actions": [
            KnowledgePromotionProposalActionResponse.model_validate(action).model_dump(mode="json")
            for action in proposal_actions(session, proposal.id)
        ],
    }


def _proposed_unit(command: ProposedUnitCommand) -> ProposedUnit:
    return ProposedUnit(
        unit_key=command.unit_key,
        title=command.title,
        outcome=command.outcome,
        required_capability=command.required_capability,
        authority=normalize_authority(command.authority),
        authority_payload=command.authority,
        context_enrichment=command.context_enrichment,
        max_attempts=command.max_attempts,
    )
