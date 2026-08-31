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


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


class LandingRefused(OrchestratorError):
    """The orchestrator refused. A fact about the subject, not a broken tool.

    It CARRIES THE REFUSAL CODE as well as the message, because not every refusal means the same
    thing to a reader. The branch-update act raises two that say only *the answer moved between
    the read and the request*, which the next pass re-decides on its own. Classifying those apart
    needs the code -- a `DomainError` reaches the wire nested under `error`, and the message is
    prose that will be reworded.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def is_allowed_read(path: str) -> bool:
    return path == _ADMISSION


def is_allowed_write(path: str) -> bool:
    return path in (_LAND, _BRANCH_UPDATE)


def _detail(response: httpx.Response) -> str:
    """The orchestrator's own explanation, bounded. Never the whole body, never headers.

    A `DomainError` reaches the wire NESTED under `error`, which is worth reading rather than
    guessing at: a check written from the handler's shape matches neither that nor the framework's
    own `detail`, and this estate has already recorded that trap. Both readings are tried, because
    a route the deployed image does not serve answers the framework's shape and this lane's routes
    are not deployed until a release.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:400]
        if payload.get("detail"):
            return str(payload["detail"])[:400]
    return f"HTTP {response.status_code}"


def _code(response: httpx.Response) -> str:
    """The refusal's own code, read from where a `DomainError` actually puts it.

    NESTED under `error`, never top-level. An unreadable body yields the empty string, which no
    classifier recognises, so an answer this program cannot parse stays a finding.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return ""


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
            self._client = httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": key_id,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        except (httpx.InvalidURL, ValueError) as error:
            # CONSTRUCTION raises for some malformed URLs and request time for others: a control
            # character is refused here by `urlparse`, while a doubled dot or an over-long DNS
            # label survives until IDNA encoding at `request`. Guarding one half leaves an
            # environment-variable typo crashing the pass with a traceback instead of reporting.
            raise OrchestratorError(
                f"the orchestrator base URL is unusable: {type(error).__name__}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The ONE way anything leaves this process, guard first."""
        permitted = is_allowed_read(path) if method == "GET" else is_allowed_write(path)
        if not permitted:
            raise ForbiddenEndpointError(f"the inert lander may not {method} {path}")
        try:
            response = self._client.request(method, path, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only. An httpx error carries the request, and a diagnostic that
            # prints what it was given is how a bearer token reaches a transcript.
            raise OrchestratorError(
                f"the orchestrator is unreachable for {method} {path}: {type(error).__name__}"
            ) from None
        if response.status_code == 409:
            raise LandingRefused(_detail(response), _code(response))
        if response.status_code >= 400:
            hint = " -- the credential is not the system one" if response.status_code == 403 else ""
            raise OrchestratorError(
                f"the orchestrator answered {response.status_code} for {path}{hint}: "
                f"{_detail(response)}"
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
