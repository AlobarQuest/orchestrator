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
]


class MemberProposalResponse(BaseModel):
    """What staging returns. `review_path` is the page a person confirms it on."""

    id: UUID
    state: str
    review_path: str
    staged_by: str
    staged_at: datetime
