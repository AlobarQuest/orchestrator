"""The OBSERVER-credentialled WRITE. One endpoint, no reads, and the split from the policy read
is deliberate -- see `policy_client.py` for why two credentials never share a guard here.

`POST /api/v1/observations` is the OBSERVER role's entire write surface (WS-P3.6 Increment 1), and
putting the same bound in the client makes a second write structurally unreachable rather than
merely unwritten.

THIS IS THE PIN WATCHER'S CLIENT, DELIBERATELY COPIED RATHER THAN IMPORTED. Lanes in `src/` import
one another for DOMAIN knowledge -- never for plumbing. A lane that reached into a sibling for an
HTTP client would make an unrelated lane's refactor able to break this one's schedule.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

OBSERVATIONS_ENDPOINT = "/api/v1/observations"

MAX_DNS_LABEL = 63


class ObservationWriteError(RuntimeError):
    pass


class ForbiddenEndpointError(ObservationWriteError):
    """The installer attempted a write outside its recording-only surface."""


class UnusableEndpointError(RuntimeError):
    """The orchestrator URL cannot be used at all -- the operator's typo, not a bad pass."""


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnusableEndpointError("the orchestrator URL must be https with a host")
    for label in parsed.hostname.split("."):
        if not label or len(label) > MAX_DNS_LABEL:
            raise UnusableEndpointError("the orchestrator URL has a malformed host")


def is_allowed_write(path: str) -> bool:
    return path == OBSERVATIONS_ENDPOINT


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
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise UnusableEndpointError(
            f"the orchestrator URL is not usable: {type(error).__name__}"
        ) from error


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
            raise ForbiddenEndpointError(f"the installer may not write to {path}")
        try:
            response = self._client.request("POST", path, json=payload)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            raise ObservationWriteError(
                f"orchestrator is unreachable for POST {path}: {type(error).__name__}"
            ) from error
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
