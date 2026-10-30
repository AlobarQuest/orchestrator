"""Which pull requests are the SDS's own reverts, as the orchestrator recorded opening them.

Landing Part 3b. When the orchestrator judges a bump it landed bad, it opens the revert through its
Dispatch App on a branch `sds-revert/pr-<bump>` and records it (`revert_opened`). That pull request
is a deploying merge, so this producer files its change record -- as `dependency-revert`, which
deploy policy 14 approves on the revert's own terms, and only when a record here names it. A
Dispatch App pull request no record names is the SDS's App doing something the SDS did not record,
and is never filed as anything.

ONE ROUTE, `GET /api/v1/opened-reverts`, read with the observer bearer the launcher already exports
for the member record. It reads the orchestrator's records only; nothing it asks reaches GitHub.

UNREADABLE IS ONE EXCEPTION, as for the member record: a transport failure, a non-2xx (a redirect
included) and a body that is not the listing are all `RevertsUnreadable`, never an empty listing --
an empty one would be a true statement that no revert is open.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure

DEFAULT_BASE_URL: Final = "https://sds.alobar.net"
USER_AGENT: Final = "change-proposer/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 60.0
OBSERVER_TOKEN_ENV: Final = "SDS_MEMBERS_OBSERVER_TOKEN"
OBSERVER_KEY_ID: Final = "orchestrator-observer"
OPENED_REVERTS_PATH: Final = "/api/v1/opened-reverts"

# The App the orchestrator opens its reverts through, as GitHub's REST `pulls` spells
# `user.login`. Deploy policy 14 names it as the only author of a `dependency-revert` pull request.
# Spelled here and not in the orchestrator, whose word guards refuse it in code.
DISPATCH_APP_LOGIN: Final = "alobar-sds-dispatch[bot]"

# The orchestrator's revert branches start with this (`revert_build.REVERT_BRANCH_PREFIX`, held
# equal by a test); a revert of pull request N is opened on `sds-revert/pr-N`.
REVERT_BRANCH_PREFIX: Final = "sds-revert/"


class RevertsUnreadable(Exception):
    """The recorded reverts could not be read, or the program tried a route it may not use."""


@dataclass(frozen=True)
class RecordedRevert:
    """A revert the SDS opened: the repository (lower-cased), the bump's pull request and the
    revert's."""

    repository: str
    pr_number: int
    revert_pr_number: int

    def names(self, repository: str, pull: dict[str, Any]) -> bool:
        """Is `pull` this revert: the same repository and number, on this record's own branch?"""
        return (
            self.repository == repository.lower()
            and self.revert_pr_number == pull.get("number")
            and pull.get("head_ref") == f"{REVERT_BRANCH_PREFIX}pr-{self.pr_number}"
        )


def _permits(method: str, path: str) -> bool:
    return (method, path) == ("GET", OPENED_REVERTS_PATH)


def _refuse(method: str, path: str) -> RevertsUnreadable:
    return RevertsUnreadable(f"change-proposer may not {method} {path}")


def _number(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("number")
    return value


def _parse(wire: Any) -> RecordedRevert:
    repository = wire["repository"]
    if not isinstance(repository, str) or not repository:
        raise TypeError("repository")
    return RecordedRevert(
        repository.lower(), _number(wire["pr_number"]), _number(wire["revert_pr_number"])
    )


def read_recorded_reverts(
    *,
    token: str,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> frozenset[RecordedRevert]:
    """Every revert the orchestrator recorded opening and has not landed."""
    try:
        client = ConfinedClient(
            base_url=base_url,
            user_agent=USER_AGENT,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": OBSERVER_KEY_ID,
                "Accept": "application/json",
            },
            permits=_permits,
            refuse=_refuse,
        )
    except TransportFailure as failure:
        raise RevertsUnreadable(f"the base URL is unusable: {failure.error_type}") from None
    try:
        try:
            response = client.request("GET", OPENED_REVERTS_PATH)
        except TransportFailure as failure:
            raise RevertsUnreadable(
                f"the orchestrator is unreachable: {failure.error_type}"
            ) from None
        if not 200 <= response.status_code < 300:
            raise RevertsUnreadable(
                f"the orchestrator answered {response.status_code} for GET {OPENED_REVERTS_PATH}"
            )
        try:
            reverts = response.json()["reverts"]
            if not isinstance(reverts, list):
                raise TypeError("reverts")
            return frozenset(_parse(wire) for wire in reverts)
        except ValueError, KeyError, TypeError, AttributeError:
            raise RevertsUnreadable(
                f"the answer to GET {OPENED_REVERTS_PATH} was not the listing"
            ) from None
    finally:
        client.close()


def read_recorded_reverts_from_environment(
    *, transport: httpx.BaseTransport | None = None
) -> frozenset[RecordedRevert]:
    """`read_recorded_reverts` as the observer, with the bearer the launcher exported."""
    token = os.environ.get(OBSERVER_TOKEN_ENV, "")
    if not token:
        raise RevertsUnreadable(f"{OBSERVER_TOKEN_ENV} is not set")
    return read_recorded_reverts(token=token, transport=transport)
