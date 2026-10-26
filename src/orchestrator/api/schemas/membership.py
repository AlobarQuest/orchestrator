from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from orchestrator.services.membership.changes import (
    GrantChange,
    MemberApp,
    MemberChange,
    MemberProposalBody,
)

__all__ = [
    "GrantChange",
    "MemberApp",
    "MemberChange",
    "MemberProposalBody",
    "MemberProposalResponse",
    "MemberView",
    "MembersResponse",
    "GrantView",
]


class MemberProposalResponse(BaseModel):
    """What staging returns. `review_path` is the page a person confirms it on."""

    id: UUID
    state: str
    review_path: str
    staged_by: str
    staged_at: datetime


class GrantView(BaseModel):
    state: str
    seeded: bool
    set_by: str
    set_at: datetime
    reason: str


class MemberView(BaseModel):
    """One member with a grant for each of the seven services, keyed by wire value."""

    repository: str
    owner: str
    state: str
    working_copy: str | None
    machine_local: bool
    apps: list[MemberApp]
    grants: dict[str, GrantView]


class MembersResponse(BaseModel):
    members: list[MemberView]
