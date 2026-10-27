from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from orchestrator.api.schemas.common import CommandBase
from orchestrator.services.membership.changes import (
    GrantChange,
    MemberApp,
    MemberChange,
    MemberProposalBody,
)
from orchestrator.services.membership.members import GrantView, MemberView

__all__ = [
    "GrantChange",
    "MemberApp",
    "MemberChange",
    "MemberProposalBody",
    "MemberProposalRequest",
    "MemberProposalResponse",
    "MemberView",
    "MembersResponse",
    "GrantView",
]


class MemberProposalRequest(CommandBase, MemberProposalBody):
    """The staging request: the proposal plus the idempotency key and expected version every
    command carries. Only the proposal body is stored and digested."""


class MemberProposalResponse(BaseModel):
    """What staging returns. `review_path` is the page a person confirms it on."""

    id: UUID
    state: str
    review_path: str
    staged_by: str
    staged_at: datetime


class MembersResponse(BaseModel):
    members: list[MemberView]
