from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommandBase(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)


class ClaimCommand(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class RenewCommand(BaseModel):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)


class EvidenceCommand(BaseModel):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class RevisionRegistration(BaseModel):
    package_id: str
    source_repository: str
    revision: int = Field(gt=0)
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: UUID
    enforcement_snapshot: dict[str, Any]
    authority: dict[str, Any]
    registry_version: int = Field(ge=0)


class UnitRegistration(BaseModel):
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


class LeaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    attempt: int
    lease_token: str
    expires_at: datetime


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
