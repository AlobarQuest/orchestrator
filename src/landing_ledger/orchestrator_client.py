"""The ledger's HTTP client for the orchestrator. Its write surface is enforced HERE, in code.

ONE endpoint: `POST /api/v1/observations`. That is the OBSERVER role's entire write surface
(WS-P3.6 Increment 1), and putting the same bound in the client means a second write is
structurally unreachable rather than merely unwritten -- the shape both sibling adapters use.

The ledger reads nothing from the orchestrator at all. It has no need to: what it records comes
from GitHub, and re-running is made safe by the observation's own idempotency, not by first
asking what is already there.
"""

from __future__ import annotations

from typing import Any

import httpx

OBSERVATIONS_ENDPOINT = "/api/v1/observations"


class LedgerWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(LedgerWriteError):
    """The ledger attempted a write outside its recording-only surface."""


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrchestratorClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(OBSERVATIONS_ENDPOINT, payload)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not is_allowed_write(path):
            raise ForbiddenEndpointError(f"the ledger may not write to {path}")
        try:
            response = self._client.request("POST", path, json=payload)
        except httpx.HTTPError as error:
            # An unreachable orchestrator raises before any status code exists. Same reasoning as
            # the reader's: it must become this client's own error, or one landing's write takes
            # the whole pass down.
            raise LedgerWriteError(
                f"orchestrator is unreachable for POST {path}: {type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise LedgerWriteError(f"orchestrator rejected POST {path}: {response.status_code}")
        return dict(response.json())
