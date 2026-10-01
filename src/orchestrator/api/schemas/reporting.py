from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)


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
