from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class LifecycleCommand(CommandBase):
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)
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
