"""The lane's confined surface on the orchestrator: read units, file named checks and verify, as
VERIFIER. Its other requests are `required_checks.py`'s GitHub read and alert-hub, through
`alerts.py`.

Four paths and no others, each matched against an anchored pattern with the id's shape spelled
out, so a prefix, a trailing slash or `.../{id}/anything-else` does not match.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code

LEDGER = "/api/v1/status-ledger"
UUID_SHAPE = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_UNIT = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}$")
_VERIFY = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/verify$")
_NAMED_CHECK = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/verifier-evidence/named-check$")
NOT_CONCLUDED = "named_check_not_concluded"
USER_AGENT = "rotation-verifier/1 (+AlobarQuest/orchestrator)"
PENDING_STATES = ("submitted", "verifying")
TIMEOUT_SECONDS = 30.0


class VerifierCallError(RuntimeError):
    """The orchestrator could not be asked, or refused, or answered something unreadable."""


class CheckNotConcluded(VerifierCallError):
    """The check has not finished on the armed head: wait, and file it again next pass."""


class ForbiddenEndpointError(VerifierCallError):
    """The lane tried to reach a path it may not."""


class UnusableEndpointError(RuntimeError):
    """The base URL cannot be used at all: the operator's typo, not a production fault."""


def permits(method: str, path: str) -> bool:
    if method == "GET":
        return path == LEDGER or _UNIT.fullmatch(path) is not None
    return method == "POST" and (
        _VERIFY.fullmatch(path) is not None or _NAMED_CHECK.fullmatch(path) is not None
    )


class VerifierClient:
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
            raise UnusableEndpointError(f"the base URL {problem}")
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
                refuse=lambda method, path: ForbiddenEndpointError(
                    f"this client may not {method} {path}"
                ),
            )
        except TransportFailure as failure:
            raise UnusableEndpointError(
                f"the base URL is not usable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def pending(self) -> list[dict[str, Any]]:
        """The status ledger's units awaiting the verifier: submitted, and any a verification left
        in `verifying` when it failed part-way (the verifier resumes from there)."""
        rows: list[dict[str, Any]] = []
        for state in PENDING_STATES:
            body = self._json("GET", LEDGER, params={"state": state})
            if not isinstance(body, list):
                raise VerifierCallError("the status ledger did not answer with a list")
            rows += [row for row in body if isinstance(row, dict)]
        return rows

    def unit(self, unit_id: str) -> dict[str, Any]:
        body = self._json("GET", f"/api/v1/work-units/{unit_id}")
        if not isinstance(body, dict):
            raise VerifierCallError("the orchestrator did not answer with a work unit")
        return body

    def verify(self, unit_id: str, version: int, idempotency_key: str) -> dict[str, Any]:
        body = self._json(
            "POST",
            f"/api/v1/work-units/{unit_id}/verify",
            json={"expected_version": version, "idempotency_key": idempotency_key},
        )
        if not isinstance(body, dict):
            raise VerifierCallError("the orchestrator did not answer with a verification")
        return body

    def named_check(self, unit_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """File one named check; the orchestrator observes how it concluded. Raises
        `CheckNotConcluded` while the check is still running."""
        body = self._json(
            "POST", f"/api/v1/work-units/{unit_id}/verifier-evidence/named-check", json=command
        )
        if not isinstance(body, dict):
            raise VerifierCallError("the orchestrator did not answer with evidence")
        return body

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise VerifierCallError(
                f"unreachable for {method} {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            # The status and the refusal's own code only: a rejection body echoes the command.
            code = error_code(response)
            suffix = f" ({code})" if code else ""
            if code == NOT_CONCLUDED:
                raise CheckNotConcluded(f"{method} {path}: {code}")
            raise VerifierCallError(f"rejected {method} {path}: {response.status_code}{suffix}")
        try:
            return response.json()
        except ValueError as error:
            raise VerifierCallError(f"answered {method} {path} with a non-JSON body") from error
