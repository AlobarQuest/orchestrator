"""The confined change-manager surface for the producer. THREE paths, checked before the transport.

The producer proposes a deploying-merge change, reads the records it has already made, and retires
one whose pull request was closed without merging. It can reach nothing else. That bound exists on
BOTH sides -- change-manager's `propose` scope refuses `approve` with a 403 whatever this client
sends -- and it is asserted here as well, on purpose. The server-side scope is the control; this is
the statement of intent that makes a mistake in this program fail before a request leaves it, and
that keeps the bound true in a development deployment where the narrow secrets are unset.

**The producer must never approve, and the reason is the whole increment.** Increment 3's
admission term reads these records. A producer that could also approve one would be a system
asking itself for permission, and the control would be decorative.

**THE COUNT IN THE FIRST LINE IS A BEHAVIOURAL CLAIM.** It read "ONE path" until increment 5b,
and this repository's own record of what goes wrong here is a scope artifact whose prose kept
asserting a property one increment had already falsified. Both write paths take a FACT and let the
server decide what follows; neither lets this program choose a status.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://change-mgr.alobar.net"
USER_AGENT = "change-proposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0

# The write surface: propose one deploying-merge change, and retire one whose pull request was
# closed without merging. Anchored so that a prefix, a trailing slash or a traversal does not
# match, and so `…/approve` cannot.
#
# THE SECOND IS NARROWER THAN IT LOOKS, and the difference is why the producer may hold it. It
# takes an observed FACT and lets the server decide what follows, exactly as proposing does -- and
# unlike proposing it is one-directional: its only outcome removes permission. A producer that
# lied to it could stop a landing it was going to be able to make anyway, and could cause none.
_PROPOSE = "/api/deploy-changes"
_RETIRE = re.compile(r"^/api/items/[1-9][0-9]*/deploy-retirement$")

# The one observation the retirement route accepts, mirrored from change-manager's own closed
# vocabulary (`app/deploy_retirement.py`). One member, because the route's justification is that
# its outcome cannot be chosen.
CLOSED_UNMERGED = "pull_request_closed_unmerged"

# What the listing calls the pipeline these records arrive on. change-manager withholds a proposed
# source from any caller that does not name one, so a sweep that forgot this would read an empty
# list and retire nothing while reporting a clean pass.
DEPLOY_SOURCE = "deploy"

# Who the retirement is attributed to. `decided_by` is a latest-writer column by decision, so a
# retirement records this producer and the human approval it supersedes stays legible in the
# event chain rather than in that column.
PRODUCER_ACTOR = "change-proposer"
_ITEMS = "/api/items"


class ChangeManagerError(Exception):
    """change-manager could not be asked, or refused in a way this pass cannot interpret."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


class ProposalRefused(ChangeManagerError):
    """change-manager refused the proposal. A finding, not a broken tool."""


def is_allowed_write(path: str) -> bool:
    return path == _PROPOSE or _RETIRE.match(path) is not None


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
        permitted = is_allowed_read(path) if method == "GET" else is_allowed_write(path)
        if not permitted:
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

    def records(self) -> list[dict[str, Any]]:
        """Every change record on the deploying-merge pipeline, whatever its status.

        NAMED SOURCE, NO STATUS FILTER. change-manager withholds a proposed source from a caller
        that does not name one, so an unnamed query answers with a clean empty list -- and it
        applies `status` as a SQL predicate, so filtering server-side would hide exactly the
        records a sweep is looking for. Both mistakes report success having examined nothing.
        """
        body = self._request("GET", _ITEMS, params={"source": DEPLOY_SOURCE})
        if not isinstance(body, list):
            raise ChangeManagerError("the change-record listing did not answer a list")
        return [row for row in body if isinstance(row, dict)]

    def retire(self, item_id: int, *, pull_request_number: int) -> dict[str, Any]:
        """Retire one record whose pull request was closed without merging.

        The caller states the fact and the subject; the server decides the status. Idempotent by
        design -- a record already terminal answers unchanged, because this runs on every pass.
        """
        body = self._request(
            "POST",
            f"/api/items/{item_id}/deploy-retirement",
            json={
                "observation": CLOSED_UNMERGED,
                "pull_request_number": pull_request_number,
                "actor": PRODUCER_ACTOR,
            },
        )
        if not isinstance(body, dict):
            raise ChangeManagerError("the retirement response was not an object")
        return body
