"""The confined change-manager surface for this producer. TWO paths, checked before the transport.

It proposes work and reads the records it has already made. It can reach nothing else -- not
approval, which is the human decision ADR-0028 keeps and the whole reason a machine may write
the record at all. The bound exists on BOTH sides: change-manager's `propose` scope refuses
every status-moving route whatever this client sends. Asserting it here as well is what makes a
mistake in this program fail before a request leaves it, and keeps the bound true in a
development deployment where the narrow credential is unset.

**A SEPARATE CLIENT FROM `change_proposer`'s, and the reason is the write path.** The estate
lander reuses that client because it needed the same read; this program needs a different
route, and adding `/api/work-changes` to that module's allowlist would widen the surface a
different program asserts about itself. Two narrow allowlists, each naming what its own program
does, beat one that names the union.

**PROPOSING IS IDEMPOTENT SERVER-SIDE.** change-manager answers 201 for a new record and 200
for an identical proposal that already exists, keyed on the package revision -- so a re-run is a
replay, which is what makes this safe to schedule. A 409 is a real finding: it means a different
proposal already stands for this exact revision, and since one revision carries one bump, that
can only be somebody having asserted different facts about the same work.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

DEFAULT_BASE_URL: Final = "https://change-mgr.alobar.net"
USER_AGENT: Final = "bump-proposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0

# The write surface: propose one piece of work. Anchored so a prefix, a trailing slash or a
# traversal does not match, and so no `/api/items/{id}/...` verb can.
_PROPOSE: Final = "/api/work-changes"
_ITEMS: Final = "/api/items"

# What the listing calls the pipeline these records arrive on (`app/sources.py::WORK_SOURCE`).
# change-manager WITHHOLDS a proposed source from any caller that does not name one, so a read
# that forgot this would answer a clean empty list and report that nothing has been proposed.
WORK_SOURCE: Final = "work"

_ALLOWED_WRITE: Final = re.compile(rf"^{re.escape(_PROPOSE)}$")


class ChangeManagerError(Exception):
    """change-manager could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


class ProposalRefused(ChangeManagerError):
    """change-manager refused the proposal. A finding, not a broken tool."""


def is_allowed_write(path: str) -> bool:
    return _ALLOWED_WRITE.match(path) is not None


def is_allowed_read(path: str) -> bool:
    return path == _ITEMS


def _detail(response: httpx.Response) -> str:
    """change-manager's own explanation, bounded. Never the whole body, never headers."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail)[:300] if detail else f"HTTP {response.status_code}"


class ChangeManagerClient:
    def __init__(
        self,
        token: str,
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
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
        except (httpx.InvalidURL, ValueError) as error:
            # Construction raises for some malformed URLs and request time for others, and the
            # split is not obvious: a control character is refused here by `urlparse`, while a
            # doubled dot or an over-long DNS label survives until IDNA encoding at `request`.
            # Guarding only the request path leaves an environment-variable typo crashing the
            # pass with a traceback instead of reporting a finding.
            raise ChangeManagerError(
                f"the change-manager base URL is unusable: {type(error).__name__}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ChangeManagerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The ONE way anything leaves this process, guard first."""
        permitted = is_allowed_read(path) if method == "GET" else is_allowed_write(path)
        if not permitted:
            raise ForbiddenEndpointError(f"the producer may not {method} {path}")
        try:
            response = self._client.request(method, path, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only. An httpx error carries the request, and a diagnostic
            # that prints what it was given is how a bearer token reaches a transcript.
            # `ValueError` is in the tuple because IDNA encoding of a malformed host raises
            # `UnicodeError`, which is neither an `HTTPError` nor an `InvalidURL`.
            raise ChangeManagerError(
                f"change-manager is unreachable for {method} {path}: {type(error).__name__}"
            ) from None
        if response.status_code == 409:
            raise ProposalRefused(f"change-manager refused {method} {path}: {_detail(response)}")
        if response.status_code >= 400:
            hint = (
                " -- the credential is not scoped for this route"
                if response.status_code == 403
                else ""
            )
            raise ChangeManagerError(
                f"change-manager answered {response.status_code} for {path}{hint}: "
                f"{_detail(response)}"
            )
        return response

    def propose(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Propose one piece of work. Returns the record and whether it was NEW."""
        response = self._send("POST", _PROPOSE, json=payload)
        try:
            body = response.json()
        except ValueError as error:
            raise ChangeManagerError("the proposal response was not JSON") from error
        if not isinstance(body, dict):
            raise ChangeManagerError("the proposal response was not an object")
        return body, response.status_code == 201

    def work_records(self) -> list[dict[str, Any]]:
        """Every record on the work pipeline, whatever its status.

        NAMED SOURCE, NO STATUS FILTER, for the two reasons the deploy producer's own listing
        gives: an unnamed query is answered without proposed sources at all, and `status` is a
        SQL predicate, so filtering server-side hides exactly the records this pass looks for.
        Both mistakes report success having examined nothing.
        """
        response = self._send("GET", _ITEMS, params={"source": WORK_SOURCE})
        try:
            body = response.json()
        except ValueError as error:
            raise ChangeManagerError("the record listing did not answer JSON") from error
        if not isinstance(body, list):
            raise ChangeManagerError("the record listing did not answer a list")
        return [row for row in body if isinstance(row, dict)]
