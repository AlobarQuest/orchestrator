"""The membership proposal vocabulary. It lives with the service that validates it: services
import nothing from `api`, and the API schema re-exports it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

MemberState = Literal["onboarding", "member", "retired"]
GrantState = Literal["not_assessed", "remediating", "granted", "not_applicable", "withdrawn"]
MemberService = Literal[
    "dependency_updates",
    "security_patches",
    "app_changes",
    "backups",
    "monitoring",
    "rollouts",
    "credential_rotation",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberApp(_Strict):
    name: str
    health_url: HttpUrl
    branch: str = "main"


class GrantChange(_Strict):
    state: GrantState
    reason: str


class MemberChange(_Strict):
    """One member's change. `None` means unchanged; `apps`, when given, replaces the whole list."""

    repository: str
    reason: str
    state: MemberState | None = None
    owner: str | None = None
    working_copy: str | None = None
    machine_local: bool | None = None
    apps: list[MemberApp] | None = None
    grants: dict[MemberService, GrantChange] = Field(default_factory=dict)


class MemberProposalBody(_Strict):
    summary: str
    seeded: bool = False
    changes: list[MemberChange] = Field(min_length=1, max_length=100)
