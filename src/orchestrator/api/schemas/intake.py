from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from orchestrator.api.schemas.common import CommandBase


class ApprovalCommand(CommandBase):
    subject_type: str = Field(pattern="^(authority|action)$")
    reason: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class DependencyCommand(CommandBase):
    kind: str
    required_state_or_condition: str
    depends_on_work_unit_id: UUID | None = None
    external_ref: str | None = None


class DependencyResolutionCommand(CommandBase):
    status: str = Field(pattern="^(satisfied|failed)$")
    detail: dict[str, Any] = Field(default_factory=dict)


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
    acceptance_criteria: list[PackageAcceptanceCriterionResponse]
    readiness: RunnerBriefReadinessResponse
    target: RunnerBriefTargetResponse
    standing_context: dict[str, Any]
    # Undeclared keys are dropped here silently, so the service returning a field
    # is not the same as a worker receiving one. factory-runner parses the HTTP
    # body, not the service dict.
    enrichment: dict[str, Any] | None = None


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
    # ADR-0026: the change-manager record a human approved to cause this work. Bounded by int4
    # because the column is an Integer, and `strict` because pydantic's lax mode reads `true`
    # as 1 -- which would attribute a revision to change record 1 rather than refusing.
    change_record_id: int | None = Field(default=None, gt=0, le=2_147_483_647, strict=True)
    # ADR-0026 amendment 1: the observation that caused the record above. Typed as a UUID so
    # pydantic refuses a malformed one with a 422 rather than letting an unwrapped
    # `uuid.UUID(bad)` reach the wire as a bare 500 further in. Whether the id names a row that
    # EXISTS is a database question and is answered in the service, not here.
    originating_observation_id: UUID | None = None


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
    change_record_id: int | None = None
    originating_observation_id: UUID | None = None
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


class ChangeRecordUnitResponse(BaseModel):
    """One unit the change record caused, and the state the verdict below was computed from."""

    unit_id: UUID
    unit_key: str
    revision_id: UUID
    state: str


class ChangeRecordWorkResponse(BaseModel):
    """What a change record caused, and whether it is done (ADR-0029).

    `all_units_completed` is named for the narrow rule rather than for anything that reads as a
    synonym for "settled": it is true when there is at least one unit and every one of them is
    `completed`, and false for every other shape including a record nothing has carried yet.

    The units are served ALONGSIDE the verdict rather than instead of it. A response model drops
    every key it does not declare, so a consumer reading a field this model omits gets silence --
    which is why the evidence for the verdict has to be declared here to travel at all.
    """

    change_record_id: int
    revision_ids: list[UUID]
    units: list[ChangeRecordUnitResponse]
    all_units_completed: bool
