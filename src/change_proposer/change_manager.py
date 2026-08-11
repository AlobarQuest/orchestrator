"""The confined change-manager surface for the producer. ONE path, checked before the transport.

The producer proposes a deploying-merge change and can reach nothing else. That bound now exists
on BOTH sides -- change-manager gained a `propose` scope in this same increment, so the server
refuses `approve` with a 403 whatever this client sends -- and it is asserted here as well, on
purpose. The server-side scope is the control; this is the statement of intent that makes a
mistake in this program fail before a request leaves it, and that keeps the bound true in a
development deployment where the narrow secrets are unset.

**The producer must never approve, and the reason is the whole increment.** Increment 3's
admission term reads these records. A producer that could also approve one would be a system
asking itself for permission, and the control would be decorative.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://change-mgr.alobar.net"
USER_AGENT = "change-proposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0

# The whole write surface: propose one deploying-merge change. Anchored so that a prefix, a
# trailing slash or a traversal does not match, and so `…/approve` cannot.
_PROPOSE = "/api/deploy-changes"
# The one read, used to report what already exists rather than to decide anything.
_ITEMS = "/api/items"


class ChangeManagerError(Exception):
    """change-manager could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


class ProposalRefused(ChangeManagerError):
    """change-manager refused the proposal. A finding, not a broken tool."""


def is_allowed_write(path: str) -> bool:
    return path == _PROPOSE


def is_allowed_read(path: str) -> bool:
    return path == _ITEMS


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
            # CONSTRUCTION raises for some malformed URLs and request time for others, and the
            # split is not obvious: a control character is refused here by `urlparse`, while a
            # doubled dot or an over-long DNS label survives until IDNA encoding at `request`.
            # Guarding only the request path therefore leaves an env-var typo crashing the pass
            # with a traceback instead of reporting a finding -- found by the test for exactly
            # this class, which is the second time this family has been caught by probing the
            # real library rather than by reading it.
            raise ChangeManagerError(
                f"the change-manager base URL is unusable: {type(error).__name__}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ChangeManagerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        allowed = is_allowed_write(path) if method != "GET" else is_allowed_read(path)
        if not allowed:
            raise ForbiddenEndpointError(f"the producer may not {method} {path}")
        try:
            response = self._client.request(method, path, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only. An httpx error carries the request, and a diagnostic that
            # prints what it was given is how a bearer token reaches a transcript. `ValueError` is
            # in the tuple because IDNA encoding of a malformed host raises `UnicodeError`, which
            # is a `ValueError` and is neither an `HTTPError` nor an `InvalidURL` -- the escape
            # increment 3 found reaching a bare HTTP 500 through a reader promising never to raise.
            raise ChangeManagerError(
                f"change-manager is unreachable for {method} {path}: {type(error).__name__}"
            ) from None
        if response.status_code == 409:
            raise ProposalRefused(f"change-manager refused {method} {path}: 409")
        if response.status_code >= 400:
            raise ChangeManagerError(f"change-manager answered {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as error:
            raise ChangeManagerError(f"{path} did not answer JSON") from error

    def propose(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Propose one deploying-merge change.

        Returns the record and whether it was NEW. change-manager answers 201 for a created record
        and 200 for an identical proposal that already existed, so a re-run is a replay rather than
        a duplicate -- which is what makes this program safe to schedule.
        """
        try:
            response = self._client.request("POST", _PROPOSE, json=payload)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise ChangeManagerError(
                f"change-manager is unreachable for POST {_PROPOSE}: {type(error).__name__}"
            ) from None
        if response.status_code == 409:
            raise ProposalRefused(
                "change-manager already holds a different record for this pull request"
            )
        if response.status_code >= 400:
            detail = ""
            if response.status_code == 403:
                detail = " -- the credential is not propose-scoped for this route"
            raise ChangeManagerError(
                f"change-manager answered {response.status_code} to the proposal{detail}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise ChangeManagerError("the proposal response was not JSON") from error
        return body, response.status_code == 201

    def existing_deploy_records(self) -> list[dict[str, Any]]:
        """Every deploying-merge record, for reporting what is already routed.

        Deliberately not used to decide whether to propose: `propose` is idempotent server-side by
        content, and a client-side "does it exist" check would be a second, racier copy of a rule
        the server already owns.
        """
        body = self._request("GET", _ITEMS, params={"source": "deploy"})
        return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []
