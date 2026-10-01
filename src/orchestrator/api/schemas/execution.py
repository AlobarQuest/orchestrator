from datetime import date, datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from orchestrator.api.schemas.common import ChangeWindowOverrideModel, CommandBase


class DispatchCommandModel(CommandBase):
    runner_attempt: int = Field(gt=0)
    # ADR-0032. Suppresses `outside_change_window` and nothing else, and grants nothing to the
    # act that lands the pull request the run produces -- that act carries its own.
    change_window_override: ChangeWindowOverrideModel | None = None


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


class FactoryPolicyKnownGoodResponse(BaseModel):
    """One declared known-good pattern, in full.

    Everything the matcher reads is served, because an operator asking what this process enforces
    needs to be able to answer "would it recognise THIS envelope" without reading the image. Every
    field NARROWS what is recognised, so none of them reads as a permission.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    rationale: str
    decided: date
    change_class: str
    capabilities: dict[str, str]
    max_attempts: int
    max_llm_calls: int
    conformance_status: str
    target_repositories: list[str]
    command_prefixes: list[str]


class FactoryPolicyChangeWindowResponse(BaseModel):
    """The hours in which policy raises no objection to work of this reach starting.

    ``null`` for a row that declares none, which is this policy having no objection on those
    grounds -- never a window of zero length and never a default. Served in the local terms it was
    written in, zone included: an offset would be true for only half the year, and the reason the
    zone is in the artifact at all is that the question is about somebody's day.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    timezone: str
    start: str
    end: str


class FactoryPolicyLeaseResponse(BaseModel):
    """How much longer than the default this orchestrator refuses to reassign work of this reach.

    ``null`` for a row that declares none, which means the build's default hold applies -- never a
    row with no lease, because every claim has one. The default and the ceiling that bounds what a
    row may declare are served at the top level, so ``null`` can be read without the image.
    """

    model_config = ConfigDict(from_attributes=True)

    rationale: str
    decided: date
    minutes: int


class FactoryPolicyLeaseBoundsResponse(BaseModel):
    """The two numbers the build owns, between which a declared lease must fall.

    Served because they are what makes a row's ``lease: null`` legible, and because they are the
    whole of why a duration in this document cannot widen anything: no value between them shortens
    a hold, and none of them switches reassignment off.
    """

    model_config = ConfigDict(from_attributes=True)

    default_minutes: int
    ceiling_minutes: int


class FactoryPolicyReachResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    member: str
    rationale: str
    decided: date
    known_good: list[FactoryPolicyKnownGoodResponse]
    change_window: FactoryPolicyChangeWindowResponse | None
    lease: FactoryPolicyLeaseResponse | None


class FactoryPolicyResponse(BaseModel):
    """What policy the running process is enforcing.

    Deliberately carries no permission of any kind: the artifact answers only in refusals, so a
    field here that read as "allowed" would be the one shape this schema must never grow.
    """

    model_config = ConfigDict(from_attributes=True)

    version: int
    source: str
    # A response model silently DROPS every key the service returns and the model does not declare,
    # which is how WS-P2.12 served an empty enrichment while every service assertion passed.
    lease_bounds: FactoryPolicyLeaseBoundsResponse
    reach: list[FactoryPolicyReachResponse]
