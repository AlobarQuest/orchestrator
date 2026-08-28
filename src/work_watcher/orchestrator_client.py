"""The confined orchestrator surface for the retirement. ONE path, and it is a READ.

**THE CLAIM IN THE FIRST LINE IS BEHAVIOURAL.** `is_allowed_read` returns True for exactly one
anchored template and there is no write path in this module at all -- no branch that could be
reached wrongly, and no ordering in which a partial failure leaves anything changed in the system
that owns the work. If a write is ever added here, this sentence is false and must move with it.

**THE VERDICT IS THE ORCHESTRATOR'S, NOT THIS PROGRAM'S.** `all_units_completed` is computed in
the transaction that reads the units (ADR-0029), and this program relays it. That is the whole
reason the read exists in that shape: a reduction computed here would be a reduction every future
producer implements again, and they would not agree. Nothing in this module inspects the unit
states to reach its own conclusion -- they are carried for a reader, so that a wrong answer can be
diagnosed rather than merely disbelieved.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx

DEFAULT_BASE_URL: Final = "https://sds.alobar.net"
USER_AGENT: Final = "work-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0

_WORK = re.compile(r"^/api/v1/change-records/[0-9]{1,9}/work$")


class OrchestratorError(Exception):
    """The orchestrator could not be asked, or answered in a way this pass cannot interpret."""


class ForbiddenEndpointError(OrchestratorError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_read(path: str) -> bool:
    return _WORK.match(path) is not None


class WorkCompletion:
    """What the orchestrator says a change record caused, relayed unchanged."""

    __slots__ = ("all_units_completed", "unit_states", "revision_count")

    def __init__(
        self, *, all_units_completed: bool, unit_states: tuple[str, ...], revision_count: int
    ) -> None:
        self.all_units_completed = all_units_completed
        self.unit_states = unit_states
        self.revision_count = revision_count


class OrchestratorClient:
    def __init__(
        self,
        token: str,
        key_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._injected = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Credential-Key-Id": key_id,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def work_for(self, change_record_id: int) -> WorkCompletion:
        """Ask what this record caused and whether all of it is done.

        A record with no revision is an ordinary 200 with an empty list and a false verdict, not a
        404 -- it is the state of every record a person approved that the carry has not reached.
        So this method has no absent case to distinguish, which is the point of that route
        answering the way it does.
        """
        path = f"/api/v1/change-records/{change_record_id}/work"
        body = self._get(path)
        completed = body.get("all_units_completed")
        units = body.get("units")
        revisions = body.get("revision_ids")
        if not isinstance(completed, bool) or not isinstance(units, list):
            # A response model DROPS every key it does not declare, so a field that stopped being
            # served arrives as absence rather than as an error. Refusing here is what turns a
            # silently narrowed contract into a finding instead of a verdict of "not done".
            raise OrchestratorError(
                f"the orchestrator did not answer whether change record {change_record_id} "
                "is complete"
            )
        return WorkCompletion(
            all_units_completed=completed,
            unit_states=tuple(str(unit.get("state")) for unit in units if isinstance(unit, dict)),
            revision_count=len(revisions) if isinstance(revisions, list) else 0,
        )

    def _get(self, path: str) -> dict[str, Any]:
        """The ONE way anything leaves this process, guard first."""
        if not is_allowed_read(path):
            raise ForbiddenEndpointError(f"this program may not GET {path}")
        try:
            client = self._injected or httpx.Client(
                base_url=self._base_url, timeout=self._timeout, headers=self._headers
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise OrchestratorError(
                f"the orchestrator base URL is unusable: {type(error).__name__}"
            ) from None
        try:
            response = client.get(path)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise OrchestratorError(
                f"the orchestrator is unreachable for GET {path}: {type(error).__name__}"
            ) from None
        finally:
            if self._injected is None:
                client.close()
        if not 200 <= response.status_code < 300:
            raise OrchestratorError(
                f"the orchestrator answered {response.status_code} for GET {path}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise OrchestratorError("the orchestrator response was not JSON") from error
        if not isinstance(payload, dict):
            raise OrchestratorError("the orchestrator response was not an object")
        return payload
