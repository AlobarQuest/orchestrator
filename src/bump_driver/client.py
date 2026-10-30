"""The lane's three confined surfaces: change-manager's work records, read; GitHub's one pull
request read; and the orchestrator as SYSTEM, the reads a pass makes and its three writes.

Every path is matched against an anchored pattern with the id's shape spelled out, so a prefix, a
trailing slash or `.../{id}/anything-else` does not match. The change-manager listing is this
lane's own query: it borrows no sibling lane's client.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code

USER_AGENT: Final = "bump-driver/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
# The merge call waits on GitHub; a dispatch call does too.
ACT_TIMEOUT_SECONDS: Final = 120.0
GITHUB_API: Final = "https://api.github.com"
UUID_SHAPE: Final = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
ITEMS: Final = "/api/items"
WORK_SOURCE: Final = "work"
_WORK = re.compile(r"^/api/v1/change-records/[1-9][0-9]*/work$")
_INTAKE = re.compile(rf"^/api/v1/package-intakes/{UUID_SHAPE}$")
_PROPOSALS = re.compile(rf"^/api/v1/package-intakes/{UUID_SHAPE}/decomposition-proposals$")
_UNIT = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}$")
_HISTORY = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/history$")
_ADMISSION = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/pr-merge-admission$")
_DISPATCH = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/dispatch$")
_MERGE = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/pr-merge$")
_PULL = re.compile(r"^/repos/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/pulls/[1-9][0-9]*$")


class CallError(RuntimeError):
    """A service could not be asked, refused, or answered something unreadable. `status` is the
    HTTP status of a refusal and `code` its own code; `status` is `None` when nothing answered."""

    def __init__(self, message: str, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class UnusableEndpointError(RuntimeError):
    """The base URL cannot be used at all: the operator's typo, not a production fault."""


def change_manager_permits(method: str, path: str) -> bool:
    return (method, path) == ("GET", ITEMS)


def github_permits(method: str, path: str) -> bool:
    return method == "GET" and _PULL.fullmatch(path) is not None


def orchestrator_permits(method: str, path: str) -> bool:
    if method == "GET":
        reads = (_WORK, _INTAKE, _PROPOSALS, _UNIT, _HISTORY, _ADMISSION)
        return any(p.fullmatch(path) for p in reads)
    return method == "POST" and any(p.fullmatch(path) for p in (_PROPOSALS, _DISPATCH, _MERGE))


class _Client:
    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        headers: dict[str, str],
        permits: Callable[[str, str], bool],
        transport: httpx.BaseTransport | None,
        timeout: float = TIMEOUT_SECONDS,
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
                timeout=timeout,
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
                code,
            )
        try:
            return response.json()
        except ValueError as error:
            raise CallError(f"{self._name} answered {method} {path} without JSON") from error

    def _object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        body = self._json(method, path, **kwargs)
        if not isinstance(body, dict):
            raise CallError(f"{self._name}'s answer to {method} {path} isn't an object")
        return body

    def _rows(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        body = self._json("GET", path, **kwargs)
        if not isinstance(body, list):
            raise CallError(f"{self._name}'s answer to GET {path} isn't a list")
        return [row for row in body if isinstance(row, dict)]


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
        """Every record on the work pipeline, by source alone, its status filtered by the pass: a
        status filter hides pending records and an unnamed source hides proposed ones."""
        return self._rows(ITEMS, params={"source": WORK_SOURCE})


class GitHub(_Client):
    def __init__(self, *, token: str, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            "GitHub",
            base_url=GITHUB_API,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            permits=github_permits,
            transport=transport,
        )

    def pull_is_open(self, repository: str, number: int) -> bool:
        return self._object("GET", f"/repos/{repository}/pulls/{number}").get("state") == "open"


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
            timeout=ACT_TIMEOUT_SECONDS,
        )

    def work(self, change_record_id: int) -> dict[str, Any]:
        return self._object("GET", f"/api/v1/change-records/{change_record_id}/work")

    def intake(self, revision_id: str) -> dict[str, Any]:
        return self._object("GET", f"/api/v1/package-intakes/{revision_id}")

    def proposals(self, revision_id: str) -> list[dict[str, Any]]:
        return self._rows(f"/api/v1/package-intakes/{revision_id}/decomposition-proposals")

    def propose(self, revision_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._object(
            "POST", f"/api/v1/package-intakes/{revision_id}/decomposition-proposals", json=body
        )

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self._object("GET", f"/api/v1/work-units/{unit_id}")

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return self._rows(f"/api/v1/work-units/{unit_id}/history")

    def dispatch(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._object("POST", f"/api/v1/work-units/{unit_id}/dispatch", json=body)

    def merge_admission(self, unit_id: str) -> dict[str, Any]:
        return self._object("GET", f"/api/v1/work-units/{unit_id}/pr-merge-admission")

    def land(self, unit_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._object("POST", f"/api/v1/work-units/{unit_id}/pr-merge", json=body)
