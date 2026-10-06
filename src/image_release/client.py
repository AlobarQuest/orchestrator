"""The binder's three confined HTTP surfaces, one per credential, and nothing beyond them.

THREE CLIENTS BECAUSE THREE IDENTITIES. Binding and observing are admitted only for SYSTEM;
verifying only for VERIFIER; and the auth-posture probe must ask once with NO credential at all.
One client holding two bearers behind one allowlist would let either reach the other's routes, so
each identity gets its own surface, enforced by its own matcher:

* SYSTEM: GET the candidates, GET a binding's deployment observations, GET the dead-letter queue
  (the configured-M2M probe); POST a binding, POST a deployment observation.
* VERIFIER: POST `/verify`. Nothing else, not even a read.
* ANONYMOUS: GET the two health routes, the OpenAPI document and the dead-letter queue (the
  missing-M2M probe). No write at all.

Both id-bearing paths are matched against anchored patterns with the id's shape spelled out, so a
prefix, a trailing slash or `.../{id}/anything-else` does not match.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code

CANDIDATES_ENDPOINT = "/api/v1/machine-activation-candidates"
DEAD_LETTER_ENDPOINT = "/api/v1/dead-letter"
HEALTH_LIVE = "/health/live"
HEALTH_READY = "/health/ready"
OPENAPI_DOCUMENT = "/openapi.json"

UUID_SHAPE = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_BIND = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/release-artifacts$")
_OBSERVE = re.compile(rf"^/api/v1/release-artifacts/{UUID_SHAPE}/deployment-observations$")
_VERIFY = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/verify$")

# The candidate read is scoped to this kind: "already bound" and "already observed" then mean a
# container-image binding, and a machine-local one on the same unit neither hides nor stands in
# for it.
CANDIDATE_KIND = "container_image"

USER_AGENT = "image-release/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0


class ReleaseCallError(RuntimeError):
    """The orchestrator could not be asked, or answered in a way this binder cannot interpret."""


class ForbiddenEndpointError(ReleaseCallError):
    """A client tried to reach a path its identity is not allowed to reach."""


class UnusableEndpointError(RuntimeError):
    """The base URL cannot be used at all -- the operator's typo, not a production fault."""


def _bare(path: str) -> str:
    return path.split("?", 1)[0]


def system_permits(method: str, path: str) -> bool:
    path = _bare(path)
    if method == "GET":
        return (
            path in {CANDIDATES_ENDPOINT, DEAD_LETTER_ENDPOINT} or _OBSERVE.match(path) is not None
        )
    if method == "POST":
        return _BIND.match(path) is not None or _OBSERVE.match(path) is not None
    return False


def verifier_permits(method: str, path: str) -> bool:
    return method == "POST" and _VERIFY.match(_bare(path)) is not None


def anonymous_permits(method: str, path: str) -> bool:
    return method == "GET" and _bare(path) in {
        HEALTH_LIVE,
        HEALTH_READY,
        OPENAPI_DOCUMENT,
        DEAD_LETTER_ENDPOINT,
    }


def bind_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/release-artifacts"


def observe_path(binding_id: str) -> str:
    return f"/api/v1/release-artifacts/{binding_id}/deployment-observations"


def verify_path(work_unit_id: str) -> str:
    return f"/api/v1/work-units/{work_unit_id}/verify"


@dataclass(frozen=True)
class Probe:
    """One GET's status, and its JSON body when it had one. Never headers, never raw bytes."""

    status_code: int
    body: Any


class _Lane:
    """One identity's confined client. Every request leaves through `_send`, permit first."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        permits: Callable[[str, str], bool],
        transport: httpx.BaseTransport | None,
    ) -> None:
        problem = base_url_problem(base_url)
        if problem is not None:
            raise UnusableEndpointError(f"the base URL {problem}")
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers=headers,
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

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise ReleaseCallError(
                f"unreachable for {method} {path}: {failure.error_type}"
            ) from None

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        if response.status_code >= 400:
            # The status and the refusal's own CODE only. A rejection body echoes the command
            # back, and a diagnostic that prints what it was given is how a value that should not
            # be in a transcript gets into one.
            code = error_code(response)
            suffix = f" ({code})" if code else ""
            raise ReleaseCallError(f"rejected {method} {path}: {response.status_code}{suffix}")
        try:
            return response.json()
        except ValueError as error:
            raise ReleaseCallError(f"answered {method} {path} with a non-JSON body") from error

    def _probe(self, path: str) -> Probe:
        response = self._send("GET", path)
        try:
            body = response.json()
        except ValueError:
            body = None
        return Probe(status_code=response.status_code, body=body)


def _bearer(credential_key_id: str, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Credential-Key-Id": credential_key_id}


class SystemClient(_Lane):
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            headers=_bearer(credential_key_id, token),
            permits=system_permits,
            transport=transport,
        )

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        body = self._json(
            "GET",
            CANDIDATES_ENDPOINT,
            params={"repository": repository, "kind": CANDIDATE_KIND},
        )
        if not isinstance(body, list):
            raise ReleaseCallError("the orchestrator did not answer with a list of candidates")
        return [row for row in body if isinstance(row, dict)]

    def observations(self, binding_id: str) -> list[dict[str, Any]]:
        body = self._json("GET", observe_path(binding_id))
        if not isinstance(body, list):
            raise ReleaseCallError("the orchestrator did not answer with a list of observations")
        return [row for row in body if isinstance(row, dict)]

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._json("POST", bind_path(work_unit_id), json=payload)
        if not isinstance(body, dict):
            raise ReleaseCallError("the orchestrator did not answer with a binding")
        return body

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._json("POST", observe_path(binding_id), json=payload)
        if not isinstance(body, dict):
            raise ReleaseCallError("the orchestrator did not answer with an observation")
        return body

    def authenticated_probe(self, path: str) -> Probe:
        """The configured-M2M half of the auth probe. The permit allows the dead-letter GET only."""
        return self._probe(path)


class VerifierClient(_Lane):
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            headers=_bearer(credential_key_id, token),
            permits=verifier_permits,
            transport=transport,
        )

    def verify(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._json("POST", verify_path(work_unit_id), json=payload)
        if not isinstance(body, dict):
            raise ReleaseCallError("the orchestrator did not answer with a verification")
        return body


class AnonymousClient(_Lane):
    def __init__(self, *, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            base_url=base_url, headers={}, permits=anonymous_permits, transport=transport
        )

    def probe(self, path: str) -> Probe:
        return self._probe(path)
