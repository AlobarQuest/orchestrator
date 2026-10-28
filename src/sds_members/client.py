"""The confined reader and stager for the SDS member record.

TWO ROUTES, AND NO OTHERS, EACH FOR ITS OWN CALLER: `GET /api/v1/members` and
`POST /api/v1/member-proposals`. Staging changes no member; only a person's confirmation on
`/review` writes the record, so this program cannot enrol a repository however it is called.

**SERVICES AND MEMBER_STATES ARE COPIES.** This is a client and imports nothing from the
orchestrator, so it keeps its own copy of the vocabularies; `tests/sds_members` holds them equal
to the orchestrator's, so drift fails CI rather than a scheduled pass.

**UNREADABLE IS ONE EXCEPTION.** Transport error, non-2xx (a redirect included: the route sits
behind a proxy that gets reconfigured), a body that is not JSON, not an object, or not the
record's shape: all `MembersUnreadable`. A caller must never read a partial answer as a shorter
member list.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, error_detail

DEFAULT_BASE_URL: Final = "https://sds.alobar.net"
USER_AGENT: Final = "sds-members/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 60.0
# The observer bearer a scheduled lane reads the record with; its launcher exports it.
OBSERVER_TOKEN_ENV: Final = "SDS_MEMBERS_OBSERVER_TOKEN"
OBSERVER_KEY_ID: Final = "orchestrator-observer"

_MEMBERS: Final = "/api/v1/members"
_PROPOSALS: Final = "/api/v1/member-proposals"

# not-a-vocabulary: copies held equal to the orchestrator's by tests/sds_members/test_client.py
SERVICES: Final = (
    "dependency_updates",
    "security_patches",
    "app_changes",
    "backups",
    "monitoring",
    "rollouts",
    "credential_rotation",
)
MEMBER_STATES: Final = ("onboarding", "member", "retired")


class MembersUnreadable(Exception):
    """The member record could not be read, or the program tried a route it may not use."""


class ProposalRefused(Exception):
    """The orchestrator did not stage the proposal."""


@dataclass(frozen=True)
class App:
    name: str
    health_url: str
    branch: str


@dataclass(frozen=True)
class Hold:
    """A version ceiling on one dependency, keyed as Dependabot identifies an update."""

    ecosystem: str
    directory: str
    dependency: str
    ceiling: str


@dataclass(frozen=True)
class Member:
    repository: str
    owner: str
    state: str
    working_copy: str | None
    machine_local: bool
    apps: tuple[App, ...]
    grants: Mapping[str, str]
    # The default serves test constructors only: `_parse` requires the wire key, so a server that
    # dropped it is `MembersUnreadable`, never "nothing is held".
    holds: tuple[Hold, ...] = ()


_READ: Final = ("GET", _MEMBERS)
_STAGE: Final = ("POST", _PROPOSALS)


def _permits(route: tuple[str, str]) -> Callable[[str, str], bool]:
    """A client permits exactly the one route its caller needs, so reading cannot stage."""
    return lambda method, path: (method, path) == route


def _refuse(method: str, path: str) -> MembersUnreadable:
    return MembersUnreadable(f"sds-members may not {method} {path}")


def _client(
    token: str,
    credential_key_id: str,
    base_url: str,
    transport: httpx.BaseTransport | None,
    route: tuple[str, str],
) -> ConfinedClient:
    try:
        return ConfinedClient(
            base_url=base_url,
            user_agent=USER_AGENT,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
                "Accept": "application/json",
            },
            permits=_permits(route),
            refuse=_refuse,
        )
    except TransportFailure as failure:
        raise MembersUnreadable(f"the base URL is unusable: {failure.error_type}") from None


def _parse(wire: Any) -> Member:
    apps = tuple(App(a["name"], a["health_url"], a["branch"]) for a in wire["apps"])
    grants = {service: wire["grants"][service]["state"] for service in SERVICES}
    holds = tuple(_parse_hold(h) for h in wire["holds"])
    working_copy = wire["working_copy"]
    if working_copy is not None and not isinstance(working_copy, str):
        raise TypeError("working_copy")
    return Member(
        repository=str(wire["repository"]),
        owner=str(wire["owner"]),
        state=str(wire["state"]),
        working_copy=working_copy,
        machine_local=bool(wire["machine_local"]),
        apps=apps,
        grants=grants,
        holds=holds,
    )


def _parse_hold(wire: Any) -> Hold:
    fields = [wire[name] for name in ("ecosystem", "directory", "dependency", "ceiling")]
    if not all(isinstance(value, str) for value in fields):
        raise TypeError("hold")
    return Hold(*fields)


def read_members(
    *,
    token: str,
    credential_key_id: str,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> list[Member]:
    """Every member, retired included, as the orchestrator serves them."""
    client = _client(token, credential_key_id, base_url, transport, _READ)
    try:
        try:
            response = client.request("GET", _MEMBERS)
        except TransportFailure as failure:
            raise MembersUnreadable(
                f"the member record is unreachable: {failure.error_type}"
            ) from None
        if not 200 <= response.status_code < 300:
            raise MembersUnreadable(
                f"the orchestrator answered {response.status_code} for GET {_MEMBERS}"
            )
        try:
            body = response.json()
            members = body["members"]
            if not isinstance(members, list):
                raise TypeError("members")
            return [_parse(wire) for wire in members]
        except ValueError, KeyError, TypeError, AttributeError:
            raise MembersUnreadable(
                f"the answer to GET {_MEMBERS} was not the member record"
            ) from None
    finally:
        client.close()


def read_members_from_environment(*, transport: httpx.BaseTransport | None = None) -> list[Member]:
    """`read_members` as the observer, with the bearer the launcher exported.

    An unset or empty variable is `MembersUnreadable`, never an empty record: a lane that cannot
    read the grants must stop, not act on a smaller population.
    """
    token = os.environ.get(OBSERVER_TOKEN_ENV, "")
    if not token:
        raise MembersUnreadable(f"{OBSERVER_TOKEN_ENV} is not set")
    return read_members(token=token, credential_key_id=OBSERVER_KEY_ID, transport=transport)


def stage(
    *,
    token: str,
    credential_key_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Stage one proposal. The key travels in the body, as does the version staging requires (0)."""
    client = _client(token, credential_key_id, base_url, transport, _STAGE)
    payload = {**body, "expected_version": 0, "idempotency_key": idempotency_key}
    try:
        try:
            response = client.request("POST", _PROPOSALS, json=payload)
        except TransportFailure as failure:
            raise ProposalRefused(
                f"the orchestrator is unreachable: {failure.error_type}"
            ) from None
        if not 200 <= response.status_code < 300:
            raise ProposalRefused(
                f"the orchestrator answered {response.status_code}: {error_detail(response)}"
            )
        try:
            answer = response.json()
        except ValueError:
            raise ProposalRefused("the staging answer was not JSON") from None
        if not isinstance(answer, dict):
            raise ProposalRefused("the staging answer was not an object")
        return answer
    finally:
        client.close()
