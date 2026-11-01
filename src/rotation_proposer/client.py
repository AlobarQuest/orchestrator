"""The one surface this program owns: two orchestrator reads on the observer bearer.

The borrowed observation client is POST-only, and widening it would widen `bump_proposer`, so the
record walk reads through this instead. Every path is matched against an anchored pattern with the
id's shape spelled out, so a prefix, a trailing slash or `.../{id}/anything-else` does not match.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

from bump_proposer.orchestrator_client import UnusableEndpointError
from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code
from rotation_proposer.alerts import USER_AGENT

TIMEOUT_SECONDS: Final = 30.0
_UUID_SHAPE: Final = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_WORK = re.compile(r"^/api/v1/change-records/[1-9][0-9]*/work$")
_HISTORY = re.compile(rf"^/api/v1/work-units/{_UUID_SHAPE}/history$")


class CallError(RuntimeError):
    """The orchestrator could not be asked, refused, or answered something unreadable."""


def permits(method: str, path: str) -> bool:
    return method == "GET" and any(pattern.fullmatch(path) for pattern in (_WORK, _HISTORY))


class Orchestrator:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        problem = base_url_problem(base_url)
        if problem is not None:
            raise UnusableEndpointError(f"the orchestrator base URL {problem}")
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": credential_key_id,
                },
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=permits,
                refuse=lambda method, path: CallError(f"this client may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise UnusableEndpointError(
                f"the orchestrator base URL is not usable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def _json(self, method: str, path: str) -> Any:
        try:
            response = self._client.request(method, path)
        except TransportFailure as failure:
            raise CallError(f"the orchestrator is unreachable: {failure.error_type}") from None
        if response.status_code >= 400:
            # The status and the refusal's own code only: a rejection body echoes the command.
            code = error_code(response)
            suffix = f" ({code})" if code else ""
            raise CallError(f"the orchestrator refused {path}: {response.status_code}{suffix}")
        try:
            return response.json()
        except ValueError as error:
            raise CallError(f"the orchestrator answered {path} without JSON") from error

    def work(self, record_id: int) -> dict[str, Any]:
        """What the change record caused: the orchestrator's answer leaves out a unit retired by
        superseding its approval, since that unit was replaced, not given up."""
        body = self._json("GET", f"/api/v1/change-records/{record_id}/work")
        if not isinstance(body, dict):
            raise CallError("the orchestrator's work answer isn't an object")
        return body

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        body = self._json("GET", f"/api/v1/work-units/{unit_id}/history")
        if not isinstance(body, list):
            raise CallError("the orchestrator's history answer isn't a list")
        return [row for row in body if isinstance(row, dict)]
