"""The executor's one surface on the orchestrator: its WORKER bearer's reads and writes only.

The executor pulls (ADR-0055 decision 1). It reads the ready queue, a unit, its package
revision, history and evidence, the machine identities the orchestrator loaded, the member record
(`grants.py`), and the observations a scheduled reader posted (`readers.py`); it claims, renews,
starts, files evidence, submits and fails.
Every path is matched against an anchored pattern before a request leaves
(`estate_clients.confined`), and any other is refused without a byte sent. The bearer comes from
BWS at start-up and is never logged; failures carry the orchestrator's error code and message,
never the request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import httpx

from estate_clients.confined import (
    ConfinedClient,
    TransportFailure,
    base_url_problem,
    error_code,
    error_detail,
)
from rotation_worker.values import Value

USER_AGENT: Final = "rotation-worker/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
UUID_SHAPE: Final = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
LEDGER: Final = "/api/v1/status-ledger"
IDENTITIES: Final = "/api/v1/m2m-identities"
HELD: Final = "/api/v1/held-credentials"
MEMBERS: Final = "/api/v1/members"
OBSERVATIONS: Final = "/api/v1/observations"
_UNIT: Final = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}$")
_UNIT_RECORDS: Final = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/(history|evidence)$")
_INTAKE: Final = re.compile(rf"^/api/v1/package-intakes/{UUID_SHAPE}$")
_LEASE: Final = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/(claim|renew|evidence)$")
_COMMAND: Final = re.compile(rf"^/api/v1/work-units/{UUID_SHAPE}/commands/(start|submit|fail)$")


class OrchestratorError(RuntimeError):
    """The orchestrator refused or couldn't be reached. `code` is its error code, if it gave one."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def permits(method: str, path: str) -> bool:
    bare = path.split("?", 1)[0]
    if method == "GET":
        return bare in (LEDGER, IDENTITIES, HELD, MEMBERS, OBSERVATIONS) or any(
            p.match(bare) for p in (_UNIT, _UNIT_RECORDS, _INTAKE)
        )
    return method == "POST" and any(p.match(bare) for p in (_LEASE, _COMMAND))


@dataclass(frozen=True)
class Lease:
    attempt: int
    token: str


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        key_id: str,
        bearer: Value,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        problem = base_url_problem(base_url)
        if problem is not None:
            raise OrchestratorError(f"the orchestrator URL {problem}")
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers={
                    "Authorization": f"Bearer {bearer.reveal()}",
                    "X-Credential-Key-Id": key_id,
                },
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=permits,
                refuse=lambda method, path: OrchestratorError(f"may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise OrchestratorError(
                f"the orchestrator URL is unusable: {failure.error_type}"
            ) from None

    def ready_units(self) -> list[str]:
        rows = self._send("GET", LEDGER, params={"state": "ready"})
        return [str(row["unit_id"]) for row in rows]

    def active_units(self) -> list[dict[str, Any]]:
        """Every unit not completed or cancelled, as the status ledger's rows."""
        return self._send("GET", LEDGER)

    def identities(self) -> dict[str, dict[str, Any]]:
        """The machine identities the orchestrator loaded (ADR-0055 amendment 9)."""
        return self._send("GET", IDENTITIES)

    def held(self) -> dict[str, dict[str, Any]]:
        """The fingerprint of each credential the orchestrator loaded to call another service
        (ADR-0055 amendment 13), by the variable that holds it."""
        return self._send("GET", HELD)

    def members(self) -> dict[str, Any]:
        """The SDS member record, retired members included (`grants.py` reads it)."""
        return self._send("GET", MEMBERS)

    def observations(
        self, source_system: str, subject_reference: str, observed_from: datetime
    ) -> list[dict[str, Any]]:
        """One subject's observations from one source, observed at or after `observed_from`."""
        params = {
            "source_system": source_system,
            "subject_reference": subject_reference,
            "observed_from": observed_from.isoformat(),
        }
        return self._send("GET", OBSERVATIONS, params=params)

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self._send("GET", f"/api/v1/work-units/{unit_id}")

    def package_intake(self, revision_id: str) -> dict[str, Any]:
        return self._send("GET", f"/api/v1/package-intakes/{revision_id}")

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return self._send("GET", f"/api/v1/work-units/{unit_id}/history")

    def evidence(self, unit_id: str) -> list[dict[str, Any]]:
        return self._send("GET", f"/api/v1/work-units/{unit_id}/evidence")

    def claim(self, unit_id: str, version: int, key: str) -> Lease:
        body = {"idempotency_key": key, "expected_version": version}
        granted = self._send("POST", f"/api/v1/work-units/{unit_id}/claim", json=body)
        return Lease(int(granted["attempt"]), str(granted["lease_token"]))

    def renew(self, unit_id: str, version: int, lease: Lease, key: str) -> None:
        body = {
            "idempotency_key": key,
            "expected_version": version,
            "attempt": lease.attempt,
            "lease_token": lease.token,
        }
        self._send("POST", f"/api/v1/work-units/{unit_id}/renew", json=body)

    def file_evidence(self, unit_id: str, body: dict[str, Any]) -> None:
        self._send("POST", f"/api/v1/work-units/{unit_id}/evidence", json=body)

    def start(self, unit_id: str, version: int, lease: Lease, key: str) -> int:
        return self._command(f"/api/v1/work-units/{unit_id}/commands/start", version, lease, key)

    def submit(self, unit_id: str, version: int, lease: Lease, key: str) -> int:
        return self._command(f"/api/v1/work-units/{unit_id}/commands/submit", version, lease, key)

    def fail(self, unit_id: str, version: int, lease: Lease, key: str, reason: str) -> int:
        path = f"/api/v1/work-units/{unit_id}/commands/fail"
        return self._command(path, version, lease, key, reason=reason)

    def _command(
        self, path: str, version: int, lease: Lease, key: str, reason: str | None = None
    ) -> int:
        body: dict[str, Any] = {
            "idempotency_key": key,
            "expected_version": version,
            "attempt": lease.attempt,
            "lease_token": lease.token,
        }
        if reason is not None:
            body["reason"] = reason
        return int(self._send("POST", path, json=body)["version"])

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except TransportFailure as failure:
            raise OrchestratorError(
                f"{method} {path} got no answer: {failure.error_type}"
            ) from None
        if response.status_code >= 400:
            raise OrchestratorError(
                f"{method} {path}: HTTP {response.status_code}: {error_detail(response)}",
                error_code(response),
            )
        try:
            return response.json()
        except ValueError:
            raise OrchestratorError(f"{method} {path}: the answer isn't JSON") from None
