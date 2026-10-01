"""Request and response models the routes of more than one domain use.

Every other model lives in `api/schemas/<domain>.py`, beside the routes of the domain that serves
it; a model moves here only when a second domain's routes need it.
"""

from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class CommandBase(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)


class ChangeWindowOverrideModel(BaseModel):
    """A supervised act's statement that it may start outside the hours policy declares.

    `reason` is deliberately UNCONSTRAINED here, against this module's habit of `min_length=1`.
    A constrained field would answer `{}` and `{"reason": null}` with a 422 listing a field
    location, where the requirement is a named refusal a caller can act on -- and the requirement
    itself belongs to the type that carries the override, so it holds for a caller reaching the
    services directly as well as for this one. Presence is what declares the override; the reason
    is what makes the record worth reading, and the two are separable only if this model lets an
    override arrive without one.
    """

    reason: str | None = None


class LifecycleCommand(CommandBase):
    attempt: int | None = Field(default=None, gt=0)
    lease_token: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    standing_context: dict[str, Any] | None = None
    context_snapshot_id: UUID | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    recovery: str | None = None
    current_state: str | None = None
    current_version: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class TransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: UUID
    state: str
    version: int
    event_id: UUID


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_type: str
    subject_id: UUID
    subject_revision_or_fingerprint: str
    approved_by: str
    reason: str
