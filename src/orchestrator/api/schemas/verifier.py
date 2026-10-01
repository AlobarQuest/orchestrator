from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from orchestrator.api.schemas.common import CommandBase


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


class RecoverEvidenceCommand(CommandBase):
    """Note there is NO lease_token: the whole scenario is that the lease is gone."""

    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)
