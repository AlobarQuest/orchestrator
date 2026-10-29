"""The estate lander's own change-manager surface. ONE path, read only, checked first.

The lander reads which deploying-merge changes have a record, and nothing else. It used to borrow
the producer's client, whose surface also proposes and retires records; the lander never called
either, but a client that could is a surface the lander held without needing it. This one cannot
write at all: `is_allowed_write` answers `False` for every path.

The listing is sent exactly as the producer sends it -- `source` named, no status filter -- and
both constants are the producer's own, imported rather than copied, because `change_proposer`
writes the records this program reads.
"""

from __future__ import annotations

from typing import Any

import httpx

from change_proposer.change_manager import DEFAULT_BASE_URL, DEPLOY_SOURCE
from estate_clients.confined import ConfinedClient, TransportFailure, error_detail

__all__ = ["DEFAULT_BASE_URL", "ChangeManagerClient", "ChangeManagerError"]

USER_AGENT = "estate-lander/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0

_ITEMS = "/api/items"


class ChangeManagerError(Exception):
    """change-manager could not be asked, or answered something this pass cannot read."""


class ForbiddenEndpointError(ChangeManagerError):
    """This program tried to reach a path it is not allowed to reach."""


def is_allowed_read(path: str) -> bool:
    return path == _ITEMS


def is_allowed_write(_path: str) -> bool:
    return False


def _permits(method: str, path: str) -> bool:
    return is_allowed_read(path) if method == "GET" else method == "POST" and is_allowed_write(path)


class ChangeManagerClient:
    def __init__(
        self,
        token: str,
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
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                permits=_permits,
                refuse=lambda method, path: ForbiddenEndpointError(
                    f"the lander may not {method} {path} on change-manager"
                ),
            )
        except TransportFailure as failure:
            # Construction is guarded as well as the request (ADR-0050).
            raise ChangeManagerError(
                f"the change-manager base URL is unusable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ChangeManagerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The ONE way anything leaves this process; the confined client asks `_permits` first."""
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise ChangeManagerError(
                f"change-manager is unreachable for {method} {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            hint = (
                " -- the credential is not scoped for this route"
                if response.status_code == 403
                else ""
            )
            raise ChangeManagerError(
                f"change-manager answered {response.status_code} for {path}{hint}: "
                f"{error_detail(response)}"
            )
        return response

    def records(self) -> list[dict[str, Any]]:
        """Every change record on the deploying-merge pipeline, whatever its status.

        NAMED SOURCE, NO STATUS FILTER: change-manager withholds a proposed source from a caller
        that does not name one, and applies `status` as a SQL predicate, so either mistake reads a
        clean empty list having examined nothing.
        """
        response = self._send("GET", _ITEMS, params={"source": DEPLOY_SOURCE})
        try:
            body = response.json()
        except ValueError as error:
            raise ChangeManagerError(f"{_ITEMS} did not answer JSON") from error
        if not isinstance(body, list):
            raise ChangeManagerError("the change-record listing did not answer a list")
        return [row for row in body if isinstance(row, dict)]
