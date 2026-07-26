"""The adapter's HTTP client for the orchestrator. The write surface is enforced HERE, in code.

The adapter may READ canonical state and WRITE exactly one thing: a unit's tracker-item
binding. Every other path -- commands, evidence, adjudications, observations, release
artifacts -- is structurally unreachable. The tracker is projection, never canonical.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

STATUS_LEDGER_ENDPOINT = "/api/v1/status-ledger"
TRACKER_BINDINGS_ENDPOINT = "/api/v1/tracker-bindings"
# The ONLY write the adapter may make. Concrete: /api/v1/work-units/<uuid>/tracker-binding.
# `\Z` (not `$`) so a trailing newline cannot slip through: this pattern is the sole in-process
# gate on the adapter's full-SYSTEM bearer, so it matches the whole string exactly.
ALLOWED_WRITE_PATTERN = re.compile(r"^/api/v1/work-units/[0-9a-fA-F-]{36}/tracker-binding\Z")


class ProjectionError(RuntimeError):
    pass


class ForbiddenEndpointError(ProjectionError):
    """The adapter attempted a write outside its projection-only surface."""


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

    def status_ledger(self) -> list[dict[str, Any]]:
        return self._request(
            "GET", STATUS_LEDGER_ENDPOINT, params={"include_inactive": "true"}
        ).json()

    def tracker_bindings(self) -> list[dict[str, Any]]:
        return self._request("GET", TRACKER_BINDINGS_ENDPOINT).json()

    def upsert_tracker_binding(
        self,
        *,
        work_unit_id: str,
        tracker_system: str,
        external_item_id: str,
        external_url: str | None,
        projected_state: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        path = f"/api/v1/work-units/{work_unit_id}/tracker-binding"
        return self.post(
            path,
            {
                "tracker_system": tracker_system,
                "external_item_id": external_item_id,
                "external_url": external_url,
                "projected_state": projected_state,
                "idempotency_key": idempotency_key,
                "expected_version": 0,
            },
        )

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not ALLOWED_WRITE_PATTERN.match(path):
            raise ForbiddenEndpointError(f"the adapter may not write to {path}")
        return self._request("POST", path, json=payload).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method not in {"GET", "POST"}:
            raise ForbiddenEndpointError(f"the adapter may not use {method}")
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ProjectionError(f"orchestrator rejected {method} {path}: {response.status_code}")
        return response
