"""The sweep's HTTP client for the orchestrator. Its surface is enforced HERE, in code.

ONE endpoint, `POST /api/v1/observations`, and NO reads at all. That endpoint is the OBSERVER
role's entire write surface (WS-P3.6 Increment 1), and putting the same bound in the client makes
a second write structurally unreachable rather than merely unwritten -- the shape every sibling
adapter uses.

The read surface is empty because this sweep genuinely needs nothing back. What it records comes
from local git, and re-running is made safe by the observation's own idempotency rather than by
first asking what is already there. The landing ledger's audit had to widen its reads and did so
as a decision; there is nothing here to widen, and `is_allowed_read` does not exist rather than
existing and returning False, so a future read has to be written deliberately.
"""

from __future__ import annotations

from typing import Any

import httpx

OBSERVATIONS_ENDPOINT = "/api/v1/observations"


class SweepWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(SweepWriteError):
    """The sweep attempted a write outside its recording-only surface."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad checkout.

    Deliberately NOT a `SweepWriteError`: that family is what the CLI treats as costing one
    checkout its row, and this is the tool being unusable for every checkout at once.

    TODAY THE DISTINCTION IS TAXONOMY RATHER THAN BEHAVIOUR, and saying so is more useful than a
    test that would assert the class hierarchy back to itself. `open_client` is called before the
    per-checkout loop, so that handler never sees this error whichever family it belongs to -- a
    mutation reparenting it under `SweepWriteError` survives the whole suite, which is how this
    note came to be written rather than the original claim that it would not.
    """


def open_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> OrchestratorClient:
    """Construct the client, translating a malformed base URL into this module's own error.

    `httpx` refuses some malformed URLs at the CONSTRUCTOR and others at request time, so a guard
    on one half is not a guard. Both halves live HERE, beside each other -- which also means the
    CLI imports no HTTP client at all, and this program's entry in the repository's outbound
    allowlist stays at exactly one file.
    """
    try:
        return OrchestratorClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=transport,
        )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {type(error).__name__}"
        ) from error


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
            raise ForbiddenEndpointError(f"the sweep may not write to {path}")
        try:
            response = self._client.request("POST", path, json=payload)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            # THREE exception families, and the third is the one a two-member tuple misses: IDNA
            # encoding of a malformed HOST raises `UnicodeError`, which is a `ValueError` and
            # neither of the other two. The triggers are ordinary environment-variable typos -- a
            # doubled dot, a DNS label over 63 characters -- and an escape here takes the whole
            # pass down with a traceback instead of costing one checkout its row.
            raise SweepWriteError(
                f"orchestrator is unreachable for POST {path}: {type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise SweepWriteError(f"orchestrator rejected POST {path}: {response.status_code}")
        return dict(response.json())
