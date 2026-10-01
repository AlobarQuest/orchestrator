"""The watcher's HTTP client for the orchestrator. Its surface is enforced HERE, in code.

ONE endpoint, `POST /api/v1/observations`, and NO reads at all. That endpoint is the OBSERVER
role's entire write surface (WS-P3.6 Increment 1), and putting the same bound in the client makes
a second write structurally unreachable rather than merely unwritten.

The transport and its guards are the estate's shared confined client (ADR-0050); what is this
program's own is the one path it may reach, its exception classes and their wording.
"""

from __future__ import annotations

from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem

OBSERVATIONS_ENDPOINT = "/api/v1/observations"


class PinWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(PinWriteError):
    """The sweep attempted a write outside its recording-only surface."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad checkout.

    Deliberately NOT a `PinWriteError`: that family is what the CLI treats as costing one
    checkout its row, and this is the tool being unusable for every checkout at once.

    TODAY THE DISTINCTION IS TAXONOMY RATHER THAN BEHAVIOUR, and saying so is more useful than a
    test that would assert the class hierarchy back to itself. Everything that raises this is
    raised by `open_client`, which the CLI calls before the per-checkout loop, so that handler
    never sees it whichever family it belongs to -- a mutation reparenting it under
    `PinWriteError` survives the whole suite, which is how this note came to be written rather
    than the original claim that it would not.
    """


def _validate_base_url(base_url: str) -> None:
    """Refuse a URL that would only fail once a request was made.

    `httpx` refuses some malformed URLs at the CONSTRUCTOR and others at REQUEST time, so a guard
    on one half is not a guard -- and the two halves would otherwise carry different exit codes,
    since a request-time failure is indistinguishable from the orchestrator being down. Measured
    against the real library: a control character raises `InvalidURL` at construction, while
    `https://host..example` and a label over sixty-three characters construct cleanly and raise
    `UnicodeError` from IDNA encoding at request time.
    """
    problem = base_url_problem(base_url)
    if problem is not None:
        raise UnusableEndpointError(f"the orchestrator URL {problem}")


def open_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> OrchestratorClient:
    """Construct the client, translating an unusable base URL into this module's own error.

    Both halves of the guard live HERE, beside each other -- which also means the CLI imports no
    HTTP client at all, and this program's entry in the repository's outbound allowlist stays at
    exactly one file.
    """
    _validate_base_url(base_url)
    try:
        return OrchestratorClient(
            base_url=base_url,
            credential_key_id=credential_key_id,
            token=token,
            transport=transport,
        )
    except TransportFailure as failure:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {failure.error_type}"
        ) from None


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


def _permits(method: str, path: str) -> bool:
    return method == "POST" and is_allowed_write(path)


def _refuse(_method: str, path: str) -> ForbiddenEndpointError:
    return ForbiddenEndpointError(f"the watcher may not write to {path}")


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = ConfinedClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
            permits=_permits,
            refuse=_refuse,
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
        try:
            response = self._client.request("POST", path, json=payload)
        except TransportFailure as failure:
            raise PinWriteError(
                f"orchestrator is unreachable for POST {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            # The status only. A rejection body echoes the command back, and a diagnostic that
            # prints what it was given is how a value that should not be in a transcript gets
            # into one.
            raise PinWriteError(f"orchestrator rejected POST {path}: {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            # A 2xx that is not JSON -- a 204, or a proxy's page, since redirects are not
            # followed. Left outside this guard it would escape as a bare `ValueError` and be
            # reported as though a caller had been unreadable.
            raise PinWriteError(f"orchestrator answered POST {path} with a non-JSON body") from (
                error
            )
        return dict(body)
