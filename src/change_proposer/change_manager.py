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


class ChangeManagerError(Exception):
    """change-manager could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


class ProposalRefused(ChangeManagerError):
    """change-manager refused the proposal. A finding, not a broken tool."""


def is_allowed_write(path: str) -> bool:
    return path == _PROPOSE


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

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The ONE way anything leaves this process, guard first.

        Every caller goes through here, including `propose`. A first version had `propose` call
        the transport directly, which left `is_allowed_write` with no production caller at all --
        the only route into the guard was a GET, so the module's headline property was enforced on
        a path production writes never took. Found by a mutation pass; it is this repository's own
        "a test calling a service is not evidence the service has a caller", reproduced inside the
        module written to embody the negative property.
        """
        if not is_allowed_write(path):
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
            # CARRY THE DETAIL. change-manager's 409 names which fields differ, and a refusal on a
            # write-once record is permanent -- an operator who cannot see WHICH frozen fact
            # drifted has no way to act on it.
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

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise ChangeManagerError(f"{path} did not answer JSON") from error

    def propose(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Propose one deploying-merge change.

        Returns the record and whether it was NEW. change-manager answers **201** for a created
        record and **200** for an identical proposal that already existed, so a re-run is a replay
        rather than a duplicate -- which is what makes this program safe to schedule.
        """
        response = self._send("POST", _PROPOSE, json=payload)
        try:
            body = response.json()
        except ValueError as error:
            raise ChangeManagerError("the proposal response was not JSON") from error
        return body, response.status_code == 201
