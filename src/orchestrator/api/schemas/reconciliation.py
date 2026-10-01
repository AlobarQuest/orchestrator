from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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
