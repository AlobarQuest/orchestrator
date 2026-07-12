"""The runner's HTTP client. Report-only is enforced HERE, in code.

"Report-only" is a property of WHICH ENDPOINT the runner may call -- not of how well-behaved its
client code happens to be. So the allowlist lives in `_request`, and any other write is
structurally unreachable rather than merely untested.

Above all it may NOT call `.../deployment-observations`. That endpoint mints a WorkUnit (SUBMITTED)
plus five Evidence rows per accepted push, and its environment field admits arbitrary strings -- so
a SYSTEM-credentialed runner calling it could create unbounded post-deploy units. It would be
setting canonical lifecycle state while calling itself a reporter.
"""

from __future__ import annotations

from typing import Any

import httpx

ALLOWED_WRITE_ENDPOINTS = frozenset(
    {
        "/api/v1/observations",
        "/api/v1/reconciliation/detect",
    }
)
IN_FLIGHT_ENDPOINT = "/api/v1/in-flight-units"
ALLOWED_OBSERVATIONS = "/api/v1/observations"
ALLOWED_DETECT = "/api/v1/reconciliation/detect"


class ReconciliationError(RuntimeError):
    pass


class ForbiddenEndpointError(ReconciliationError):
    """The runner attempted a write outside its report-only surface."""


class ReconciliationClient:
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

    def in_flight_units(self) -> dict[str, Any]:
        return self._request("GET", IN_FLIGHT_ENDPOINT).json()

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(ALLOWED_OBSERVATIONS, payload)

    def detect(self, idempotency_key: str) -> dict[str, Any]:
        return self.post(
            ALLOWED_DETECT,
            {"idempotency_key": idempotency_key, "expected_version": 0},
        )

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path not in ALLOWED_WRITE_ENDPOINTS:
            raise ForbiddenEndpointError(f"the runner may not write to {path}")
        return self._request("POST", path, json=payload).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method not in {"GET", "POST"}:
            raise ForbiddenEndpointError(f"the runner may not use {method}")
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ReconciliationError(
                f"orchestrator rejected {method} {path}: {response.status_code}"
            )
        return response
