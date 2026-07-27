"""The adapter's HTTP client for the orchestrator. The write surface is enforced HERE, in code.

The adapter may READ canonical state and WRITE exactly two things: a unit's tracker-item
binding, and an append-only inbound reconciliation report. Every other path -- commands,
evidence, adjudications, observations, release artifacts -- is structurally unreachable.
Both permitted writes are provably non-canonical (exit #9): the tracker is projection, never
canonical, and the reconciliation report only records observed divergence -- it never mutates
lifecycle state itself.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

STATUS_LEDGER_ENDPOINT = "/api/v1/status-ledger"
TRACKER_BINDINGS_ENDPOINT = "/api/v1/tracker-bindings"
# The tracker-binding write: /api/v1/work-units/<uuid>/tracker-binding. `\Z` (not `$`) so a
# trailing newline cannot slip through.
TRACKER_BINDING_PATTERN = re.compile(r"^/api/v1/work-units/[0-9a-fA-F-]{36}/tracker-binding\Z")
# WS-P2.7 Inc-2: the inbound report. Report-only -- it records append-only divergence conditions
# and can never change canonical state (exit #9). Fixed path, so an exact-string gate.
TRACKER_DETECT_ENDPOINT = "/api/v1/reconciliation/tracker-detect"


def _is_allowed_write(path: str) -> bool:
    """The adapter's TWO permitted writes, both provably non-canonical (a projection binding and
    an append-only reconciliation report). Every lifecycle/command/adjudication/observation path
    stays structurally unreachable."""
    return path == TRACKER_DETECT_ENDPOINT or bool(TRACKER_BINDING_PATTERN.match(path))


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

    def report_tracker_reconciliation(
        self, *, observed_states: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        return self.post(
            TRACKER_DETECT_ENDPOINT,
            {
                "observed_states": observed_states,
                "idempotency_key": idempotency_key,
                "expected_version": 0,
            },
        )

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not _is_allowed_write(path):
            raise ForbiddenEndpointError(f"the adapter may not write to {path}")
        return self._request("POST", path, json=payload).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method not in {"GET", "POST"}:
            raise ForbiddenEndpointError(f"the adapter may not use {method}")
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ProjectionError(f"orchestrator rejected {method} {path}: {response.status_code}")
        return response
