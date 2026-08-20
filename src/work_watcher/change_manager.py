"""The confined change-manager surface for the retirement. ONE write, and it is one-directional.

**THE COUNT IN THE FIRST LINE IS A BEHAVIOURAL CLAIM.** `is_allowed_write` returns True for
exactly one anchored path template and False for everything else, so this program cannot reach
`POST /api/deploy-changes` -- the route its scope permits and that can write `approved` through
the pinned policy -- nor any decision verb, nor the general `resolve`. If a second write is ever
added here, this sentence is false and must move with it; this repository has already been bitten
by a docstring that went on asserting a count one increment had falsified.

**THE SCOPE IS WIDER THAN THIS SURFACE, DELIBERATELY.** The retirement route joins `propose`
alongside the deploying-merge retirement rather than taking a scope of its own (ADR-0029), so the
bearer this program holds could, at the service, reach the proposal ingresses too. What keeps it
from doing so is the allowlist below, checked before any request is built -- the same arrangement
`bump_proposer` and `change_proposer` already use to hold one scope and assert different surfaces.

**THE READ IS THE CARRY'S, ON PURPOSE.** Enumerating the approved queue is one question with one
correct parse, and a second implementation of it here would be a second place for the row
validation and the source re-check to drift. `work_carrier.change_manager.HttpWorkRecordSource`
names the pipeline in its query and re-checks `source` and `status` on every row; borrowing it
means this program reads exactly what the carry reads, which is also what makes "retirement runs
over the same queue the carry is about to read" true rather than approximately true.

**A RETIREMENT CAN ONLY EVER REMOVE PERMISSION.** That is what makes it acceptable for an
unattended program to hold it. A bug here stops work that a person had approved and that would
have been carried anyway; it cannot cause work, cannot approve anything, and cannot reach a status
any consumer acts on.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

DEFAULT_BASE_URL: Final = "https://change-mgr.alobar.net"
USER_AGENT: Final = "work-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0

# The observation this program reports, and the only one the route accepts. Mirrored from
# `app/work_retirement.py::WORK_UNIT_COMPLETED`, which is the party that decides what follows from
# it; a value that stopped agreeing is refused there rather than acted on here.
WORK_UNIT_COMPLETED: Final = "work_unit_completed"

# Who the retirement is recorded against. It names the MECHANISM rather than a person or a
# credential, exactly as change-manager's own settlement actor does: what decided is a fact the
# orchestrator derived, and `actor` on a change-manager decision is caller-declared free text, so
# a name invented here would attest nothing.
RETIREMENT_ACTOR: Final = "work-watcher"

# The whole write surface. Anchored, with the id shape spelled out, so `…/{id}/resolve` does not
# match and neither does a prefix, a trailing slash, or a traversal.
_RETIRE = re.compile(r"^/api/items/[0-9]{1,9}/work-retirement$")


class ChangeManagerError(Exception):
    """change-manager could not be asked, or answered in a way this pass cannot interpret."""


class RetirementRefused(ChangeManagerError):
    """change-manager refused the retirement. A fact about the subject, not a broken tool."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_write(path: str) -> bool:
    return _RETIRE.match(path) is not None


class RetirementClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._client = client

    def retire(self, item_id: int, *, package_id: str, package_revision: int) -> dict[str, Any]:
        """Report that the work this record asked for was built. Returns the updated record.

        The locator is sent as well as the item id because the route checks it: naming the subject
        twice is what makes the retirement about something this program actually observed, rather
        than about whichever record an identifier happened to select.
        """
        path = f"/api/items/{item_id}/work-retirement"
        if not is_allowed_write(path):  # pragma: no cover - the template is a constant
            raise ForbiddenEndpointError(f"this program may not POST {path}")
        body = {
            "observation": WORK_UNIT_COMPLETED,
            "package_id": package_id,
            "package_revision": package_revision,
            "actor": RETIREMENT_ACTOR,
        }
        return self._post(path, body)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """The ONE way anything leaves this process, guard first."""
        if not is_allowed_write(path):
            raise ForbiddenEndpointError(f"this program may not POST {path}")
        try:
            client = self._client or httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {self._token}"},
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # `httpx` raises at the CONSTRUCTOR for some malformed URLs and at request time for
            # others, and the third family is a `ValueError`: IDNA encoding of a malformed host
            # raises `UnicodeError`, which is neither an `HTTPError` nor an `InvalidURL`. An
            # environment-variable typo is an ordinary mistake and must be a finding, not a
            # traceback out of a scheduled job.
            raise ChangeManagerError(f"change-manager base URL is unusable: {error}") from error
        try:
            response = client.post(path, json=body)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # The exception TYPE only. An httpx error carries the request, and a diagnostic that
            # prints what it was given is how a bearer token reaches a log.
            raise ChangeManagerError(
                f"change-manager is unreachable for POST {path}: {type(error).__name__}"
            ) from None
        finally:
            if self._client is None:
                client.close()
        if not 200 <= response.status_code < 300:
            # ANY non-2xx rather than `>= 400`, for the reason the carry records: this service sits
            # behind a proxy, and a redirect waved through to `.json()` reports a routing refusal
            # as a response-encoding fault every morning.
            raise RetirementRefused(
                f"change-manager answered {response.status_code} for POST {path}: "
                f"{_detail(response)}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ChangeManagerError("the retirement response was not JSON") from error
        if not isinstance(payload, dict):
            raise ChangeManagerError("the retirement response was not an object")
        return payload


def _detail(response: httpx.Response) -> str:
    """change-manager's own explanation, bounded. Never the whole body, never headers."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])[:400]
    return f"HTTP {response.status_code}"
