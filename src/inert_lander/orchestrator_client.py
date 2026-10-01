"""The confined orchestrator surface for the inert-population lander. THREE paths, checked
before the transport.

It asks whether a pull request the update bot opened may be landed into a repository where
landing on the default branch changes nothing already serving and, when told to, asks for the
landing or for the much smaller act of bringing a stale branch up to date with its base. It can
reach nothing else -- not a work unit, not a decomposition, not an approval, and not the sibling
lane's three routes either.

**Neither act decides anything.** Every term is evaluated inside the orchestrator, in the
transaction that records the act, so this program relays answers and never composes one. That is
the whole reason it is allowed to be a scheduled job: the thing running unattended is a caller,
not a judge.

**A SECOND CLIENT RATHER THAN A PARAMETER ON THE FIRST**, and the reason is the allowlist itself.
`estate_lander`'s client names its own three paths as literals so that a mistake in that program
fails before a request leaves it; a shared client taking the paths from its caller would move the
decision into the caller and leave neither program able to state its surface. The two lanes are
also not equally consequential -- that one rewrites a default branch and starts a rollout on a
running service -- so the surfaces are deliberately not interchangeable.
"""

from __future__ import annotations

from typing import Any

import httpx

from estate_clients.confined import (
    ConfinedClient,
    TransportFailure,
    error_code,
    error_detail,
)

# The two refusal classes live in the leaf `lander.errors`, because `lander.core` catches them;
# imported so this client raises exactly the classes that body can see, and re-exported.
from lander.errors import LandingRefused, OrchestratorError

DEFAULT_BASE_URL = "https://sds.alobar.net"
USER_AGENT = "inert-lander/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 60.0

_ADMISSION = "/api/v1/inert-pr-merge-admission"
_LAND = "/api/v1/inert-pr-merge"

# The SECOND write, and it is the reason this lane can require freshness at all. Requiring a head
# current with its base is a tightening over the workflow this lane replaces, which required
# nothing -- and a landing stales every sibling, so a requirement with nothing to satisfy it would
# be a stall rather than a gate. It is much the smaller of the two acts: it brings a topic branch
# up to date with its base, where the other rewrites a default branch.
_BRANCH_UPDATE = "/api/v1/inert-pr-branch-update"


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_read(path: str) -> bool:
    return path == _ADMISSION


def is_allowed_write(path: str) -> bool:
    return path in (_LAND, _BRANCH_UPDATE)


def _permits(method: str, path: str) -> bool:
    return is_allowed_read(path) if method == "GET" else is_allowed_write(path)


class OrchestratorClient:
    def __init__(
        self,
        token: str,
        key_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": key_id,
                    "Accept": "application/json",
                },
                permits=_permits,
                refuse=lambda method, path: ForbiddenEndpointError(
                    f"the inert lander may not {method} {path}"
                ),
            )
        except TransportFailure as failure:
            # Construction is guarded as well as the request: some malformed URLs are refused
            # here and others only at request time (ADR-0050).
            raise OrchestratorError(
                f"the orchestrator base URL is unusable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The ONE way anything leaves this process; the confined client asks `_permits` first."""
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise OrchestratorError(
                f"the orchestrator is unreachable for {method} {path}: {failure.error_type}"
            ) from None
        if response.status_code == 409:
            raise LandingRefused(error_detail(response), error_code(response))
        if response.status_code >= 400:
            hint = " -- the credential is not the system one" if response.status_code == 403 else ""
            raise OrchestratorError(
                f"the orchestrator answered {response.status_code} for {path}{hint}: "
                f"{error_detail(response)}"
            )
        return response

    def _object(self, response: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise OrchestratorError(f"the {what} was not JSON") from error
        if not isinstance(body, dict):
            raise OrchestratorError(f"the {what} was not an object")
        return body

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
        response = self._send(
            "GET", _ADMISSION, params={"repository": repository, "pr_number": pr_number}
        )
        return self._object(response, "admission answer")

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Ask for the landing, NAMING the head the admission answer was about.

        The orchestrator refuses a head that has moved since, which is what stops a rebase between
        the answer and the request landing a tree nobody evaluated.
        """
        response = self._send(
            "POST",
            _LAND,
            json={
                "repository": repository,
                "pr_number": pr_number,
                "expected_head_sha": head_sha,
                "idempotency_key": idempotency_key,
            },
        )
        return self._object(response, "landing response")

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Ask for this pull request's head to be brought up to date with its base.

        NAMING THE HEAD, for the same reason the landing does: the orchestrator refuses a head
        that has moved since the answer was read, so a rebase between the two is refused here
        rather than acted on against a branch nobody looked at.

        Whether it QUALIFIES is not this program's judgment and is not asserted here. The
        orchestrator composes that answer again inside the transaction that acts, and a request
        for one that does not qualify is refused by name.
        """
        response = self._send(
            "POST",
            _BRANCH_UPDATE,
            json={
                "repository": repository,
                "pr_number": pr_number,
                "expected_head_sha": head_sha,
                "idempotency_key": idempotency_key,
            },
        )
        return self._object(response, "branch-update response")
