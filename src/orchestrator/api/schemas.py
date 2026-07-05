from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
