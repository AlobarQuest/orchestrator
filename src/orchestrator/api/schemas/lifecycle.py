from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from orchestrator.api.schemas.common import CommandBase


class ClaimCommand(CommandBase):
    standing_context: dict[str, Any] | None = None


class RenewCommand(CommandBase):
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)


class ReclaimCommand(CommandBase):
    next_owner_id: str = Field(min_length=1)
    standing_context: dict[str, Any] | None = None


class RetryCommand(CommandBase):
    new_max_attempts: int = Field(gt=0)
    reason: str = Field(min_length=1)


class PreflightCommandModel(CommandBase):
    standing_context: dict[str, Any]
    purpose: str = Field(min_length=1)
    previous_context_snapshot_id: UUID | None = None
    approval_id: UUID | None = None
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)


class UnitResponse(BaseModel):
    id: UUID
    state: str
    version: int


class LeaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    attempt: int
    lease_token: str
    expires_at: datetime
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
    def _numerics_match_cost_known(self) -> CostActualsCommand:
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
