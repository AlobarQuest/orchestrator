"""The membership proposal vocabulary. It lives with the service that validates it: services
import nothing from `api`, and the API schema re-exports it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator

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
    health_url: str
    branch: str = "main"

    @field_validator("health_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        """Validate as an http(s) URL but keep the text as written: the revision watcher's list
        must match it byte for byte, and `HttpUrl` would normalize `https://x.test` to `.../`."""
        TypeAdapter(HttpUrl).validate_python(value)
        return value


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
