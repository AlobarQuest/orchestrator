from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class CommandBase(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)


class ClaimCommand(CommandBase):
    standing_context: dict[str, Any] | None = None


class RenewCommand(CommandBase):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)


class ReclaimCommand(CommandBase):
    next_owner_id: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class RecoverExpiredClaimCommand(CommandBase):
    pass


class LifecycleCommand(CommandBase):
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    standing_context: dict[str, Any] | None = None
    context_snapshot_id: UUID | None = None


class ApprovalCommand(CommandBase):
    subject_type: str = Field(pattern="^(authority|action)$")
    reason: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class RetryCommand(CommandBase):
    new_max_attempts: int = Field(gt=0)
    reason: str = Field(min_length=1)


class AdjudicationCommand(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    outcome: str = Field(pattern="^(passed|failed|waived|not_applicable)$")
    rationale: str = Field(min_length=1)
    evidence_id: UUID | None = None
    failed_evidence_id: UUID | None = None
    risk: str | None = None
    follow_up: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None


class DependencyCommand(CommandBase):
    kind: str
    required_state_or_condition: str
    depends_on_work_unit_id: UUID | None = None
    external_ref: str | None = None


class DependencyResolutionCommand(CommandBase):
    status: str = Field(pattern="^(satisfied|failed)$")
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidenceCommand(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)
    context_snapshot_id: UUID | None = None
    # A later attempt supersedes the current evidence for its AC rather than first-writing
    # over it. The service resolves which row to supersede from current_evidence, so the
    # caller signals only intent. Default False preserves first-write behavior.
    supersede: bool = False


class PreflightCommandModel(CommandBase):
    standing_context: dict[str, Any]
    purpose: str = Field(min_length=1)
    previous_context_snapshot_id: UUID | None = None
    approval_id: UUID | None = None
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)


class DispatchCommandModel(CommandBase):
    runner_attempt: int = Field(gt=0)


class InfraLaneLinkCommandModel(CommandBase):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    status: str = Field(
        pattern="^(requested|approved|executing|verification_pending|completed|failed|cancelled)$"
    )
    change_manager_ref: str = Field(min_length=1)
    change_manager_url: str | None = None
    infraops_ref: str | None = None
    approval_ref: str | None = None
    rollback_ref: str | None = None
    verify_ref: str | None = None
    final_evidence_ref: str | None = None
    payload: dict[str, Any] | None = None


class ReleaseArtifactCommandModel(CommandBase):
    package_revision_id: UUID
    package_revision_hash: str = Field(min_length=1)
    source_repository: str = Field(min_length=1)
    implementation_pr_number: int | None = Field(default=None, gt=0)
    source_commit: str = Field(min_length=1)
    merge_commit: str = Field(min_length=1)
    artifact_registry: str = Field(min_length=1)
    artifact_repository: str = Field(min_length=1)
    artifact_name: str = Field(min_length=1)
    artifact_digest: str = Field(min_length=1)
    artifact_tag: str | None = None
    workflow_run_id: str | None = None
    workflow_run_attempt: int | None = Field(default=None, gt=0)
    workflow_path: str | None = None
    workflow_ref: str | None = None
    workflow_run_url: str | None = None
    builder_id: str | None = None
    builder_class: str | None = None
    provenance_ref: str | None = None
    provenance_digest: str | None = None
    sbom_ref: str | None = None
    sbom_digest: str | None = None
    summary: dict[str, Any] | None = None


class DeploymentObservationCommandModel(CommandBase):
    environment: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    observed_artifact_digest: str = Field(min_length=1)
    deployment_ref: str = Field(min_length=1)
    deployment_url: str = Field(min_length=1)
    deployer: str = Field(min_length=1)
    observed_at: datetime
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    dispatch_summary: dict[str, Any]
    status_summary: dict[str, Any]


class ObservationCommandModel(CommandBase):
    source_system: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    source_url: str | None = None
    trust_classification: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    subject_reference: str = Field(min_length=1)
    environment: str | None = None
    observation_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    observed_at: datetime
    summary: str = Field(min_length=1)
    facts: dict[str, Any]
    payload_digest: str | None = None


class KnowledgePromotionProposalCommandModel(CommandBase):
    correlation_identity: str = Field(min_length=1, max_length=200)
    source_observation_ids: list[UUID] = Field(min_length=1)
    release_artifact_binding_id: UUID | None = None
    deployment_observation_id: UUID | None = None
    work_unit_id: UUID | None = None
    package_revision_id: UUID | None = None
    correlation_summary: str = Field(min_length=1, max_length=700)
    target_brain: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    applicability: dict[str, Any] = Field(default_factory=dict)
    proposed_payload: dict[str, Any]
    provenance: dict[str, Any] = Field(default_factory=dict)


class KnowledgePromotionSubmitCommandModel(CommandBase):
    pass


class VerifyCommandModel(CommandBase):
    pass


class VerifierNamedCheckEvidenceCommandModel(CommandBase):
    """WS-P2.20: the caller names a check and claims a conclusion; it does not report one.

    There is no field for what the check actually concluded, nor for the run that produced it.
    The orchestrator reads those from GitHub at ingestion, so a caller cannot supply both halves
    of a comparison and have the criterion resolve on its own arithmetic.
    """

    model_config = ConfigDict(extra="forbid")

    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1, max_length=100)
    dispatch_id: UUID
    repository: str = Field(min_length=1, max_length=300)
    pr_number: int = Field(gt=0)
    pr_url: str = Field(min_length=1, max_length=2000)
    head_sha: str = Field(min_length=7, max_length=64)
    check_name: str = Field(min_length=1, max_length=200)
    expected_conclusion: Literal[
        "success",
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "neutral",
        "skipped",
    ]


class AcceptanceCriterionDeclaration(BaseModel):
    """What one of the revision's required acceptance criteria actually IS.

    The bootstrap registration lane could always declare WHICH ac_ids a revision requires (the
    enforcement snapshot's list of strings) and never what any of them meant. A required ac_id
    with no criterion behind it is decidable by no actor: `human_may_adjudicate` refuses an absent
    criterion, and the verify command refuses the whole revision. Such a unit used to be
    completable only by a verifier asserting an outcome it had not evaluated (WS-P2.32).
    """

    ac_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class RevisionRegistration(CommandBase):
    package_id: str
    source_repository: str
    revision: int = Field(gt=0)
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: str = Field(min_length=1)
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)
    acceptance_criteria: list[AcceptanceCriterionDeclaration] | None = None


class UnitRegistration(CommandBase):
    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: dict[str, Any]
    max_attempts: int = Field(ge=0, default=3)
    approved_by: str
    approved_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    recovery: str | None = None
    current_state: str | None = None
    current_version: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class RevisionResponse(BaseModel):
    id: UUID
    revision: int


class UnitResponse(BaseModel):
    id: UUID
    state: str
    version: int


class ReadinessReasonResponse(BaseModel):
    code: str
    subject_id: UUID | None
    detail: str


class ReadinessResponse(BaseModel):
    status: str
    reasons: list[ReadinessReasonResponse]


class RunnerBriefWorkUnitResponse(BaseModel):
    id: UUID
    state: str
    version: int
    title: str
    outcome: str
    required_capability: str
    max_attempts: int


class RunnerBriefPackageResponse(BaseModel):
    id: str
    revision_id: UUID
    revision: int
    content_hash: str
    source_repository: str
    source_path: str
    source_commit: str


class RunnerBriefAuthorityResponse(BaseModel):
    fingerprint: str
    envelope: dict[str, Any]


class RunnerBriefReadinessResponse(BaseModel):
    status: str
    reasons: list[ReadinessReasonResponse]


class RunnerBriefTargetResponse(BaseModel):
    repository: str


class RunnerBriefResponse(BaseModel):
    work_unit: RunnerBriefWorkUnitResponse
    package: RunnerBriefPackageResponse
    authority: RunnerBriefAuthorityResponse
    acceptance_criteria: list["PackageAcceptanceCriterionResponse"]
    readiness: RunnerBriefReadinessResponse
    target: RunnerBriefTargetResponse
    standing_context: dict[str, Any]
    # Undeclared keys are dropped here silently, so the service returning a field
    # is not the same as a worker receiving one. factory-runner parses the HTTP
    # body, not the service dict.
    enrichment: dict[str, Any] | None = None


class LeaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    attempt: int
    lease_token: str
    expires_at: datetime
    context_snapshot_id: UUID | None = None


class TransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: UUID
    state: str
    version: int
    event_id: UUID


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    ac_id: str
    attempt: int
    evidence_type: str
    stable_ref: str | None
    payload: dict[str, Any] | None
    source_revision: str
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    idempotency_key: str
    supersedes_evidence_id: UUID | None
    context_snapshot_id: UUID | None = None


class ContextSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    claim_id: UUID | None
    attempt: int
    actor_id: str
    actor_role: str
    context: dict[str, Any] | list[Any]
    context_fingerprint: str
    classification: str
    decision: str
    approval_id: UUID | None
    event_id: UUID
    idempotency_key: str
    created_at: datetime


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    occurred_at: datetime
    actor_id: str
    action: str
    subject_type: str
    subject_id: UUID
    from_state: str | None
    to_state: str | None
    payload: dict[str, Any]
    correlation_id: UUID
    idempotency_key: str


class EventPublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_system: str
    source_kind: str
    source_id: UUID
    source_action: str | None
    event_id: str
    mapping_version: str
    status: str
    skip_reason: str | None
    export_ref: str | None
    attempt_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    last_attempted_at: datetime | None
    published_at: datetime | None


class DispatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    runner_attempt: int
    status: str
    reason_code: str | None
    target_repository: str
    workflow_id: str
    workflow_ref: str
    github_run_id: str | None
    github_run_url: str | None
    failure_signature: str | None
    event_id: UUID | None
    created_at: datetime
    updated_at: datetime


class InfraLaneLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    attempt: int
    status: str
    change_manager_ref: str
    change_manager_url: str | None
    infraops_ref: str | None
    approval_ref: str | None
    rollback_ref: str | None
    verify_ref: str | None
    final_evidence_ref: str | None
    payload: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    idempotency_key: str


class ReleaseArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    work_package_revision_id: UUID
    package_revision_hash: str
    source_repository: str
    implementation_pr_number: int | None
    source_commit: str
    merge_commit: str
    artifact_registry: str
    artifact_repository: str
    artifact_name: str
    artifact_digest: str
    artifact_tag: str | None
    workflow_run_id: str | None
    workflow_run_attempt: int | None
    workflow_path: str | None
    workflow_ref: str | None
    workflow_run_url: str | None
    builder_id: str | None
    builder_class: str | None
    provenance_ref: str | None
    provenance_digest: str | None
    sbom_ref: str | None
    sbom_digest: str | None
    summary: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    evidence_id: UUID
    idempotency_key: str


class DeploymentObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    release_artifact_binding_id: UUID
    implementation_work_unit_id: UUID
    work_package_revision_id: UUID
    package_revision_hash: str
    post_deploy_work_unit_id: UUID
    environment: str
    base_url: str
    observed_artifact_digest: str
    deployment_ref: str
    deployment_url: str
    deployer: str
    observed_at: datetime
    probe_summary: dict[str, Any]
    route_summary: dict[str, Any]
    auth_summary: dict[str, Any]
    dispatch_summary: dict[str, Any]
    status_summary: dict[str, Any]
    recorded_by: str
    recorded_at: datetime
    event_id: UUID
    post_deploy_event_id: UUID
    evidence_ids: list[str]
    idempotency_key: str


class ObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_system: str
    source_reference: str
    source_url: str | None
    trust_classification: str
    subject_type: str
    subject_reference: str
    environment: str | None
    observation_type: str
    status: str
    severity: str
    observed_at: datetime
    received_at: datetime
    summary: str
    facts: dict[str, Any]
    normalized_fact_hash: str
    payload_digest: str | None
    recorded_by: str
    event_id: UUID
    idempotency_key: str


class KnowledgePromotionProposalActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    proposal_id: UUID
    action: str
    brain_record_id: str | None
    brain_status: str | None
    brain_response: dict[str, Any] | None
    reason: str | None
    action_by: str
    action_at: datetime
    event_id: UUID
    idempotency_key: str


class KnowledgePromotionProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    correlation_identity: str
    source_observation_ids: list[str]
    source_observation_hashes: list[str]
    release_artifact_binding_id: UUID | None
    deployment_observation_id: UUID | None
    work_unit_id: UUID | None
    package_revision_id: UUID | None
    correlation_summary: str
    target_brain: str
    target_type: str
    authority: str
    applicability: dict[str, Any]
    proposed_payload: dict[str, Any]
    provenance: dict[str, Any]
    proposal_hash: str
    proposed_by: str
    proposed_at: datetime
    event_id: UUID
    idempotency_key: str
    state: str | None = None
    actions: list[KnowledgePromotionProposalActionResponse] = Field(default_factory=list)


class EventPublicationQueueCommand(CommandBase):
    source_kind: str | None = None
    source_id: UUID | None = None


class EventPublicationExportCommand(CommandBase):
    output_path: str = Field(min_length=1)


class EventPublicationRetryCommand(CommandBase):
    pass


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_type: str
    subject_id: UUID
    subject_revision_or_fingerprint: str
    approved_by: str
    reason: str


class AdjudicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_revision_id: UUID
    work_unit_id: UUID
    ac_id: str
    outcome: str
    decided_by: str
    rationale: str


class VerifyEvaluationResponse(BaseModel):
    ac_id: str
    evidence_type: str
    status: str
    outcome: str | None
    evidence_id: UUID | None
    finding_evidence_id: UUID | None
    adjudication_id: UUID | None
    reason: str


class VerifyResponse(BaseModel):
    unit_id: UUID
    state: str
    version: int
    result: str
    evaluations: tuple[VerifyEvaluationResponse, ...]


class StatusLedgerEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    attempt: int
    evidence_type: str
    stable_ref: str | None
    source_revision: str
    recorded_by: str
    recorded_at: datetime
    context_snapshot_id: UUID | None


class StatusLedgerAdjudicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    outcome: str
    decided_by: str
    decided_at: datetime
    evidence_id: UUID | None
    rationale: str


class StatusLedgerFailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    actor_id: str
    occurred_at: datetime
    from_state: str | None
    reason: str | None


class MetricValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    value: float | None
    basis: str


class SloReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    since: datetime
    until: datetime
    intake_to_first_work: MetricValueResponse
    queue_age: MetricValueResponse
    claim_expiry_rate: MetricValueResponse
    waiver_frequency: MetricValueResponse
    revert_rate: MetricValueResponse
    evidence_completeness: MetricValueResponse
    cost_per_unit: MetricValueResponse
    token_consumption: MetricValueResponse
    improvisation: MetricValueResponse
    budget_breach: MetricValueResponse


class StatusLedgerRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_id: str | None
    unit_id: UUID
    unit_key: str
    unit_title: str
    unit_state: str
    claim_id: UUID | None
    claim_attempt: int | None
    claim_lease_expires_at: datetime | None
    claim_released_at: datetime | None
    claim_terminal_reason: str | None
    last_heartbeat_at: datetime | None
    last_event_at: datetime | None
    blockers: list[dict[str, Any | None]]
    pending_human_approvals: list[dict[str, Any]]
    latest_evidence: StatusLedgerEvidenceResponse | None
    latest_adjudication: StatusLedgerAdjudicationResponse | None
    last_failure: StatusLedgerFailureResponse | None
    context_snapshot_id: UUID | None
    context_classification: str | None
    context_decision: str | None


class DependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    kind: str
    required_state_or_condition: str
    status: str


class PackageAcceptanceCriterionCommand(BaseModel):
    ac_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    approver: str = Field(min_length=1)


class PackageIntakeRegistration(CommandBase):
    package_id: str = Field(min_length=1)
    source_repository: str = Field(min_length=1)
    revision: int = Field(gt=0)
    content_hash: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_commit: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    approval_event_id: str = Field(min_length=1)
    approval_ledger_commit: str = Field(min_length=1)
    profile: str | None = None
    status_at_intake: str = Field(min_length=1)
    verification_mode: str = Field(min_length=1)
    verification_limitations: dict[str, Any] | list[Any] | None = None
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)
    acceptance_criteria: list[PackageAcceptanceCriterionCommand] = Field(min_length=1)
    intake_purpose: Literal["executable", "protocol_fixture"] = "executable"
    follow_up: dict[str, Any] | None = None


class PackageAcceptanceCriterionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ac_id: str
    condition: str
    evidence_type: str
    evidence: str
    approver: str


class PackageIntakeResponse(BaseModel):
    id: UUID
    package_id: str
    source_repository: str
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: str
    approval_ledger_commit: str | None
    profile: str | None
    status_at_intake: str | None
    intake_source: str
    verification_mode: str | None
    verification_limitations: dict[str, Any] | list[Any] | None
    enforcement_snapshot: dict[str, Any]
    authority_fingerprint: str
    authority: dict[str, Any] | None
    follow_up: dict[str, Any] | None
    registry_version: int
    registered_by: str
    registered_at: datetime
    acceptance_criteria: list[PackageAcceptanceCriterionResponse]


class ProposedUnitCommand(BaseModel):
    unit_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    required_capability: str = Field(min_length=1)
    authority: dict[str, Any]
    context_enrichment: dict[str, Any] | None = None
    max_attempts: int = Field(ge=0, default=3)


class ProposedDependencyCommand(BaseModel):
    source_unit_key: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    required_state_or_condition: str = Field(min_length=1)
    target_unit_key: str | None = None
    external_ref: str | None = None


class AcMappingCommandModel(BaseModel):
    ac_id: str = Field(min_length=1)
    unit_key: str = Field(min_length=1)


class RetainedAcCommandModel(BaseModel):
    ac_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class DecompositionProposalRegistration(CommandBase):
    rationale: str = Field(min_length=1)
    proposed_units: list[ProposedUnitCommand] = Field(min_length=1)
    dependencies: list[ProposedDependencyCommand] = Field(default_factory=list)
    ac_mappings: list[AcMappingCommandModel] = Field(default_factory=list)
    retained_acs: list[RetainedAcCommandModel] = Field(default_factory=list)


class DecompositionDecisionCommand(CommandBase):
    reason: str = Field(min_length=1)


class DecompositionProposalUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: dict[str, Any]
    authority_fingerprint: str
    context_enrichment: dict[str, Any] | None
    max_attempts: int


class DecompositionProposalDependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_unit_key: str
    kind: str
    target_unit_key: str | None
    external_ref: str | None
    required_state_or_condition: str


class DecompositionProposalAcMappingResponse(BaseModel):
    unit_key: str
    package_acceptance_criterion: PackageAcceptanceCriterionResponse


class DecompositionProposalRetainedAcResponse(BaseModel):
    rationale: str
    package_acceptance_criterion: PackageAcceptanceCriterionResponse


class DecompositionProposalResponse(BaseModel):
    id: UUID
    work_package_revision_id: UUID
    proposal_number: int
    state: str
    rationale: str
    proposed_by: str
    proposed_actor_role: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    created_work_unit_ids: dict[str, str] | None
    proposed_units: list[DecompositionProposalUnitResponse]
    dependencies: list[DecompositionProposalDependencyResponse]
    ac_mappings: list[DecompositionProposalAcMappingResponse]
    retained_acs: list[DecompositionProposalRetainedAcResponse]


class RecoverEvidenceCommand(CommandBase):
    """Note there is NO lease_token: the whole scenario is that the lease is gone."""

    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)


class ReconciliationDetectCommand(CommandBase):
    """The detect-pass carries the same idempotency contract as every other /api/v1 mutation.

    Its conditions dedup on the divergence hash regardless of this key, so a duplicate delivery
    surfaces as `suppressed_duplicates` rather than a second row -- but the uniform contract is
    not something a write path gets to opt out of.
    """


class ReconciliationDetectResponse(BaseModel):
    """Counters, not just a status. Fail-open is counted, so a miss is observable."""

    conditions_recorded: int
    skipped_correlations: int
    suppressed_duplicates: int


class TrackerReconciliationDetectItem(BaseModel):
    """One bound tracker item's observed completion state, reported by the adapter.

    Normalized state only -- never card text. The orchestrator owns the divergence rule; this
    carries no interpretation.
    """

    tracker_system: str = Field(min_length=1)
    external_item_id: str = Field(min_length=1)
    observed_completed: bool


class TrackerReconciliationDetectCommand(CommandBase):
    """Inbound tracker reconciliation: a batch of observed item states. Conditions dedup on the
    divergence hash regardless of the idempotency key, so a duplicate delivery surfaces as
    suppressed_duplicates rather than a second row."""

    observed_states: list[TrackerReconciliationDetectItem]


class FollowUpMintCommand(CommandBase):
    """One minting pass. It has no single subject, so `expected_version` carries no meaning here
    and only 0 is accepted -- the same contract the observation ingress uses. Per-unit
    idempotency is structural: the unit id is content-addressed from the revision id, so a
    re-run under a fresh key still mints nothing new."""


class MintedFollowUpResponse(BaseModel):
    work_unit_id: UUID
    work_package_revision_id: UUID
    due_at: datetime


class SkippedRevisionResponse(BaseModel):
    work_package_revision_id: UUID
    # A second copy of the service's skip-reason strings, because `Literal` needs literals and
    # cannot be built from constants. Kept honest by a sync test rather than by hope --
    # see test_the_response_vocabulary_matches_the_services_skip_reasons.
    reason: Literal[
        "not_required",
        "no_completed_unit",
        "units_in_flight",
        "unsettled_failed_unit",
        "not_yet_due",
        "already_minted",
        "declaration_malformed",
        "reach_undeclared",
    ]


class FollowUpMintResponse(BaseModel):
    """Counters and reasons, not just a status. A skip is counted so a miss is observable."""

    minted: list[MintedFollowUpResponse]
    skipped: list[SkippedRevisionResponse]
    considered: int


class DeadLetterEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    work_unit_id: UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None


class RequeueCommand(CommandBase):
    reason: str = Field(min_length=1)


class PrBindingCommand(CommandBase):
    """The worker reporting the pull request it opened, and its current head.

    `attempt` and `lease_token` are how the worker proves it holds this unit's claim -- the same
    proof recording evidence demands. Without it, any worker could rewrite any unit's expected
    head, and the expected head is the only thing divergence is measured against.
    """

    pr_number: int = Field(gt=0)
    head_sha: str = Field(min_length=1)
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)


class PrBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    pr_number: int
    head_sha: str
    verification_read_head_sha: str | None
    verification_read_attempt: int | None


class TrackerBindingCommand(CommandBase):
    """A tracker-projection adapter recording the external item a unit is mirrored onto.

    Projection only, like pr-binding: it never derives from tracker content and never changes
    the unit's lifecycle state, so it carries no claim proof -- only the SYSTEM actor may write.
    """

    tracker_system: str
    external_item_id: str = Field(min_length=1)
    external_url: str | None = None
    projected_state: str = Field(min_length=1)


class TrackerBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    tracker_system: str
    external_item_id: str
    external_url: str | None
    projected_state: str
    updated_at: datetime


class CostActualsCommand(CommandBase):
    """A runner reporting the actual LLM cost of one work-unit attempt.

    Carries `expected_version` (required 0, like pr-binding): cost-actuals appends an
    attempt.cost_recorded event and never targets the unit's version, so the route asserts
    expected_version == 0 as the same uniformity marker every worker write uses rather than
    exempting this path from the repo-wide "every mutation carries expected_version" invariant.
    `attempt` + `lease_token` prove the caller holds this unit's live claim, exactly as evidence
    and pr-binding demand. When `cost_known` is False (a failed attempt left no usable
    transcript) every numeric is null -- the cost is honestly absent, never a fabricated zero.
    """

    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    cost_known: bool
    llm_calls: int | None = Field(default=None, ge=0)
    num_turns: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _numerics_match_cost_known(self) -> "CostActualsCommand":
        numerics = (
            self.llm_calls,
            self.num_turns,
            self.input_tokens,
            self.output_tokens,
            self.cost_usd,
        )
        if self.cost_known and any(value is None for value in numerics):
            raise ValueError("cost_known is true but a numeric field is null")
        if not self.cost_known and any(value is not None for value in numerics):
            raise ValueError("cost_known is false but a numeric field is non-null")
        return self


class CostActualsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    attempt: int
    event_id: UUID
    cost_known: bool


class FactoryPolicyKnownGoodResponse(BaseModel):
    """One declared known-good pattern, in full.

    Everything the matcher reads is served, because an operator asking what this process enforces
    needs to be able to answer "would it recognise THIS envelope" without reading the image. Every
    field NARROWS what is recognised, so none of them reads as a permission.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    rationale: str
    decided: date
    change_class: str
    capabilities: dict[str, str]
    max_attempts: int
    max_llm_calls: int
    conformance_status: str
    target_repositories: list[str]
    command_prefixes: list[str]


class FactoryPolicyChangeWindowResponse(BaseModel):
    """The hours in which policy raises no objection to work of this reach starting.

    ``null`` for a row that declares none, which is this policy having no objection on those
    grounds -- never a window of zero length and never a default. Served in the local terms it was
    written in, zone included: an offset would be true for only half the year, and the reason the
    zone is in the artifact at all is that the question is about somebody's day.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    timezone: str
    start: str
    end: str


class FactoryPolicyLeaseResponse(BaseModel):
    """How much longer than the default this orchestrator refuses to reassign work of this reach.

    ``null`` for a row that declares none, which means the build's default hold applies -- never a
    row with no lease, because every claim has one. The default and the ceiling that bounds what a
    row may declare are served at the top level, so ``null`` can be read without the image.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    minutes: int


class FactoryPolicyLeaseBoundsResponse(BaseModel):
    """The two numbers the build owns, between which a declared lease must fall.

    Served because they are what makes a row's ``lease: null`` legible, and because they are the
    whole of why a duration in this document cannot widen anything: no value between them shortens
    a hold, and none of them switches reassignment off.
    """

    model_config = ConfigDict(from_attributes=True)

    default_minutes: int
    ceiling_minutes: int


class FactoryPolicyReachResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    member: str
    rationale: str
    decided: date
    known_good: list[FactoryPolicyKnownGoodResponse]
    change_window: FactoryPolicyChangeWindowResponse | None
    lease: FactoryPolicyLeaseResponse | None


class FactoryPolicyGrandfatheringResponse(BaseModel):
    """The revisions exempt from having to declare reach, in full.

    Not a count. This is a temporary exemption from a rule everything else is held to, and the
    operator question is which records it still covers and whether it can be deleted yet.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    revisions: list[str]


class FactoryPolicyResponse(BaseModel):
    """What policy the running process is enforcing.

    Deliberately carries no permission of any kind: the artifact answers only in refusals, so a
    field here that read as "allowed" would be the one shape this schema must never grow.
    """

    model_config = ConfigDict(from_attributes=True)

    version: int
    source: str
    # A response model silently DROPS every key the service returns and the model does not declare,
    # which is how WS-P2.12 served an empty enrichment while every service assertion passed.
    lease_bounds: FactoryPolicyLeaseBoundsResponse
    grandfathered: FactoryPolicyGrandfatheringResponse | None
    reach: list[FactoryPolicyReachResponse]


class ConsistencyFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check: str
    work_unit_id: UUID | None
    subject: str
    detail: str
    observed: str
    expected: str


class ConsistencyReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked_at: datetime
    divergent: bool
    findings: list[ConsistencyFindingResponse]


class InFlightUnitModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    unit_key: str
    state: str
    version: int
    attempt_count: int
    work_package_revision_id: UUID
    pr_number: int | None
    head_sha: str | None
    verification_read_head_sha: str | None


class ReleaseBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    binding_id: UUID
    work_unit_id: UUID
    work_unit_state: str
    source_repository: str
    artifact_digest: str
    has_post_deploy_unit: bool
    post_deploy_unit_state: str | None
    post_deploy_unit_created_at: datetime | None


class InFlightUnitsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: list[InFlightUnitModel]
    release_bindings: list[ReleaseBindingModel]


class EvidencePackWorkUnitResponse(BaseModel):
    """WS-P2.5: the subset of a work unit the evidence pack keys everything else against."""

    id: UUID
    title: str
    state: str
    authority_fingerprint: str


class EvidencePackProvenanceResponse(BaseModel):
    """The canonical package-revision facts a reviewer checks first: what was actually built."""

    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    registered_by: str


class EvidencePackAuthorityViolationResponse(BaseModel):
    code: str
    message: str
    remediation: str | None = None


class EvidencePackAuthorityResponse(BaseModel):
    authority_fingerprint: str
    envelope: dict[str, Any]
    authority_violation: EvidencePackAuthorityViolationResponse | None = None


class EvidencePackDependencyResponse(BaseModel):
    kind: str
    required_state_or_condition: str
    status: str


class EvidencePackClaimResponse(BaseModel):
    attempt: int
    claimed_by: str
    lease_expires_at: datetime
    terminal_reason: str | None = None


class EvidencePackEvidenceResponse(BaseModel):
    """One AC-keyed evidence record. `supersedes` chains to a prior entry's `id`."""

    id: UUID
    ac_id: str
    current: bool
    evidence_type: str
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    supersedes: UUID | None = None


class EvidencePackAdjudicationResponse(BaseModel):
    """One AC-keyed adjudication. Waiver fields are populated only when `outcome == "waived"`."""

    id: UUID
    ac_id: str
    outcome: str
    current: bool
    decided_by: str
    # WS-P3.7. The KIND of actor that decided, as a stored fact. NULL on every row written before
    # the column existed, and NULL means *unknown* -- a consumer must never read it as "not human".
    decided_by_role: str | None = None
    # The evidence the decision was recorded against. `failed_evidence_id` below is the waiver
    # field and answers a different question; only it was projected before.
    evidence_id: UUID | None = None
    rationale: str
    risk: str | None = None
    follow_up: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None
    failed_evidence_id: UUID | None = None


class EvidencePackCriterionRefusalResponse(BaseModel):
    """One reason the unit does not qualify. `ac_id` is null when the reason is unit-wide."""

    ac_id: str | None = None
    code: str


class EvidencePackVerifierDecidedResponse(BaseModel):
    """Whether every required acceptance criterion of this unit reached a current terminal
    adjudication that the verifier recorded from its own evaluation of evidence.

    Computed once, in `services/lifecycle.py`, and served here so an off-process consumer can read
    the answer without parsing `/history` for an opaque event payload. Fails closed in every
    direction: an unrecorded decider kind, a criterion with no single current adjudication, a
    waiver, or a revision that declares no usable criteria all make `satisfied` false and name
    themselves in `refusals`.
    """

    satisfied: bool
    refusals: list[EvidencePackCriterionRefusalResponse]


class EvidencePackApprovalResponse(BaseModel):
    subject_type: str
    decision: str
    approved_by: str
    reason: str


class EvidencePackEventPublicationResponse(BaseModel):
    source_ref: str
    status: str
    event_id: str
    export_ref: str | None = None
    last_error: str | None = None


class EvidencePackEventResponse(BaseModel):
    occurred_at: datetime
    action: str
    actor_id: str
    from_state: str | None = None
    to_state: str | None = None
    reason: str | None = None


class EvidencePackResponse(BaseModel):
    """A single work unit's full evidentiary record, structured for programmatic consumption.

    Mirrors the field set of the `/review` evidence-pack HTML page (`templates/evidence_pack.html`)
    exactly, but as JSON any authenticated caller can read -- including the runner's WORKER
    credential, which has no role gate on this route. Field names are chosen so a per-release pack
    (WS-P2.5 Increment 2) can nest a `list[EvidencePackResponse]` without renaming anything here.
    """

    work_unit: EvidencePackWorkUnitResponse
    provenance: EvidencePackProvenanceResponse
    authority: EvidencePackAuthorityResponse
    dependencies: list[EvidencePackDependencyResponse]
    claims: list[EvidencePackClaimResponse]
    evidence: list[EvidencePackEvidenceResponse]
    adjudications: list[EvidencePackAdjudicationResponse]
    verifier_decided_completion: EvidencePackVerifierDecidedResponse
    approvals: list[EvidencePackApprovalResponse]
    event_publications: list[EvidencePackEventPublicationResponse]
    events: list[EvidencePackEventResponse]


class ReleaseEvidencePackRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_id: UUID
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    registered_by: str


class ReleaseEvidencePackResponse(BaseModel):
    revision: ReleaseEvidencePackRevisionResponse
    units: list[EvidencePackResponse]
    release_artifacts: list[ReleaseArtifactResponse]
    deployments: list[DeploymentObservationResponse]


class TraceabilityAnchorResponse(BaseModel):
    """WS-P2.6: identifies which entity the caller anchored the traceability query on."""

    matched_on: str
    value: str


class TraceabilityIntentHop(BaseModel):
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    registered_by: str


class TraceabilityUnitHop(BaseModel):
    id: UUID
    unit_key: str
    title: str
    state: str
    authority_fingerprint: str
    authority_approved_by: str | None = None
    authority_decision: str | None = None


class TraceabilityPrHop(BaseModel):
    pr_number: int
    head_sha: str


class TraceabilityCommitHop(BaseModel):
    source_repository: str
    source_commit: str
    merge_commit: str
    implementation_pr_number: int | None = None


class TraceabilityArtifactHop(BaseModel):
    artifact_digest: str
    artifact_registry: str
    artifact_repository: str
    artifact_name: str
    artifact_tag: str | None = None
    workflow_run_url: str | None = None
    builder_id: str | None = None
    provenance_digest: str | None = None
    sbom_digest: str | None = None


class TraceabilityDeploymentHop(BaseModel):
    environment: str
    observed_artifact_digest: str
    digest_matches: bool
    deployment_ref: str
    deployment_url: str
    deployer: str
    observed_at: datetime
    status_summary: dict[str, Any]
    probe_summary: dict[str, Any]


class TraceabilityConditionHop(BaseModel):
    observation_kind: str
    condition_type: str
    detail: str
    resolution_generation: int
    detected_at: datetime
    open: bool
    resolution_decision: str | None = None


class TraceabilityObservationHop(BaseModel):
    source_system: str
    observation_type: str
    status: str
    severity: str
    summary: str
    observed_at: datetime


class TraceabilityChainResponse(BaseModel):
    intent: TraceabilityIntentHop
    unit: TraceabilityUnitHop
    pr: TraceabilityPrHop | None = None
    commit: list[TraceabilityCommitHop]
    artifact: list[TraceabilityArtifactHop]
    deployment: list[TraceabilityDeploymentHop]
    conditions: list[TraceabilityConditionHop]
    observations: list[TraceabilityObservationHop]


class TraceabilityResponse(BaseModel):
    anchor: TraceabilityAnchorResponse
    chains: list[TraceabilityChainResponse]
