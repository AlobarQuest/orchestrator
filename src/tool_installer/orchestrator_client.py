"""The OBSERVER-credentialled WRITE. One endpoint, no reads, and the split from the policy read
is deliberate -- see `policy_client.py` for why two credentials never share a guard here.

`POST /api/v1/observations` is the OBSERVER role's entire write surface (WS-P3.6 Increment 1), and
putting the same bound in the client makes a second write structurally unreachable rather than
merely unwritten.

The transport and its guards are the estate's shared confined client (ADR-0050); what is this
program's own is the one path it may reach, its exception classes and their wording.
"""

from __future__ import annotations

from typing import Any

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem

OBSERVATIONS_ENDPOINT = "/api/v1/observations"


class ObservationWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(ObservationWriteError):
    """The installer attempted a write outside its recording-only surface."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad pass."""


def _validate_base_url(base_url: str) -> None:
    problem = base_url_problem(base_url)
    if problem is not None:
        raise UnusableEndpointError(f"the orchestrator URL {problem}")


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


def _permits(method: str, path: str) -> bool:
    return method == "POST" and is_allowed_write(path)


def _refuse(_method: str, path: str) -> ForbiddenEndpointError:
    return ForbiddenEndpointError(f"the installer may not write to {path}")


def open_client(
    *,
    base_url: str,
    credential_key_id: str,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> OrchestratorClient:
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
            raise ObservationWriteError(
                f"orchestrator is unreachable for POST {path}: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            raise ObservationWriteError(
                f"orchestrator rejected POST {path}: {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise ObservationWriteError(
                f"orchestrator answered POST {path} with a non-JSON body"
            ) from error
        return dict(body)
