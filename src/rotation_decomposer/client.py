"""The lane's two confined surfaces: change-manager's work records, read; and the orchestrator,
read and one write, the decomposition proposal, as SYSTEM.

Every path is matched against an anchored pattern with the id's shape spelled out, so a prefix,
a trailing slash or `.../{id}/anything-else` does not match.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code

USER_AGENT: Final = "rotation-decomposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
UUID_SHAPE: Final = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
ITEMS: Final = "/api/items"
WORK_SOURCE: Final = "work"
_WORK = re.compile(r"^/api/v1/change-records/[1-9][0-9]*/work$")
_MEMBERS: Final = "/api/v1/members"
_INTAKE = re.compile(rf"^/api/v1/package-intakes/{UUID_SHAPE}$")
_PROPOSALS = re.compile(rf"^/api/v1/package-intakes/{UUID_SHAPE}/decomposition-proposals$")


class CallError(RuntimeError):
    """A service could not be asked, refused, or answered something unreadable. `status` is the
    HTTP status of a refusal, and `None` when nothing was answered."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UnusableEndpointError(RuntimeError):
    """The base URL cannot be used at all: the operator's typo, not a production fault."""


def change_manager_permits(method: str, path: str) -> bool:
    return (method, path) == ("GET", ITEMS)


def orchestrator_permits(method: str, path: str) -> bool:
    if method == "GET":
        return path == _MEMBERS or any(p.fullmatch(path) for p in (_WORK, _INTAKE, _PROPOSALS))
    return method == "POST" and _PROPOSALS.fullmatch(path) is not None


class _Client:
    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        headers: dict[str, str],
        permits: Any,
        transport: httpx.BaseTransport | None,
    ) -> None:
        problem = base_url_problem(base_url)
        if problem is not None:
            raise UnusableEndpointError(f"the {name} base URL {problem}")
        self._name = name
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers=headers,
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=permits,
                refuse=lambda method, path: CallError(f"this client may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise UnusableEndpointError(
                f"the {name} base URL is not usable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise CallError(
                f"{self._name} unreachable for {method} {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            # The status and the refusal's own code only: a rejection body echoes the command.
            code = error_code(response)
            suffix = f" ({code})" if code else ""
            raise CallError(
                f"{self._name} refused {method} {path}: {response.status_code}{suffix}",
                response.status_code,
            )
        try:
            return response.json()
        except ValueError as error:
            raise CallError(f"{self._name} answered {method} {path} without JSON") from error


class ChangeManagerReader(_Client):
    def __init__(
        self, *, base_url: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        super().__init__(
            "change-manager",
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            permits=change_manager_permits,
            transport=transport,
        )

    def work_records(self) -> list[dict[str, Any]]:
        """Every record on the work pipeline, by source alone, its status filtered here: a status
        filter hides pending records and an unnamed source hides proposed ones.

        The work carrier lists the same records, but this lane's only clients are the ones here
        (`test_rotation_edges.py`), so the query is its own."""
        body = self._json("GET", ITEMS, params={"source": WORK_SOURCE})
        if not isinstance(body, list):
            raise CallError("change-manager's record listing isn't a list")
        return [row for row in body if isinstance(row, dict)]


class Orchestrator(_Client):
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            "the orchestrator",
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "X-Credential-Key-Id": credential_key_id},
            permits=orchestrator_permits,
            transport=transport,
        )

    def members(self) -> Any:
        """The member record's body, unparsed: `rotation_granted` reads it and refuses a shape it
        doesn't know, so a wrong answer is never taken for a shorter record."""
        return self._json("GET", _MEMBERS)

    def work(self, change_record_id: int) -> dict[str, Any]:
        body = self._json("GET", f"/api/v1/change-records/{change_record_id}/work")
        if not isinstance(body, dict):
            raise CallError("the orchestrator's work answer isn't an object")
        return body

    def intake(self, revision_id: str) -> dict[str, Any]:
        body = self._json("GET", f"/api/v1/package-intakes/{revision_id}")
        if not isinstance(body, dict):
            raise CallError("the orchestrator's intake answer isn't an object")
        return body

    def proposals(self, revision_id: str) -> list[dict[str, Any]]:
        body = self._json("GET", f"/api/v1/package-intakes/{revision_id}/decomposition-proposals")
        if not isinstance(body, list):
            raise CallError("the orchestrator's proposal listing isn't a list")
        return [row for row in body if isinstance(row, dict)]

    def propose(self, revision_id: str, body: dict[str, Any]) -> dict[str, Any]:
        answer = self._json(
            "POST", f"/api/v1/package-intakes/{revision_id}/decomposition-proposals", json=body
        )
        if not isinstance(answer, dict):
            raise CallError("the orchestrator's proposal answer isn't an object")
        return answer
