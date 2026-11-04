from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)

from orchestrator.api.schemas.common import CommandBase


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
