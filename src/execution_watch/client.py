"""The one surface this lane owns: a single orchestrator read on the observer bearer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from bump_proposer.orchestrator_client import UnusableEndpointError
from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code
from execution_watch.decide import UnitRow

USER_AGENT: Final = "execution-watch/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS: Final = 30.0
_STATUS = re.compile(r"^/api/v1/execution-status$")


class CallError(RuntimeError):
    """The orchestrator could not be asked, refused, or answered something unreadable."""


def permits(method: str, path: str) -> bool:
    return method == "GET" and _STATUS.fullmatch(path) is not None


def _moment(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CallError("the execution-status answer holds a time that isn't text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CallError("the execution-status answer holds an unreadable time") from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _text(row: dict[str, Any], field: str, *, required: bool) -> str | None:
    value = row.get(field)
    if isinstance(value, str) and value:
        return value
    if value is None and not required:
        return None
    raise CallError(f"the execution-status answer's {field} is not usable")


def _flag(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise CallError(f"the execution-status answer's {field} is not a boolean")
    return value


def parse_row(row: object) -> UnitRow:
    if not isinstance(row, dict):
        raise CallError("the execution-status answer holds a row that isn't an object")
    return UnitRow(
        unit_id=str(_text(row, "unit_id", required=True)),
        state=str(_text(row, "state", required=True)),
        change_class=_text(row, "change_class", required=False),
        is_rotation=_flag(row, "is_rotation"),
        standing=_flag(row, "standing"),
        target_repository=_text(row, "target_repository", required=False),
        granted=_flag(row, "granted"),
        latest_dispatch_status=_text(row, "latest_dispatch_status", required=False),
        latest_dispatch_at=_moment(row.get("latest_dispatch_at")),
        has_claim=_flag(row, "has_claim"),
        lease_lapsed=_flag(row, "lease_lapsed"),
        breaker_open=_flag(row, "breaker_open"),
    )


class Orchestrator:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        problem = base_url_problem(base_url)
        if problem is not None:
            raise UnusableEndpointError(f"the orchestrator base URL {problem}")
        try:
            self._client = ConfinedClient(
                base_url=base_url,
                user_agent=USER_AGENT,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Credential-Key-Id": credential_key_id,
                },
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                permits=permits,
                refuse=lambda method, path: CallError(f"this client may not {method} {path}"),
            )
        except TransportFailure as failure:
            raise UnusableEndpointError(
                f"the orchestrator base URL is not usable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def execution_status(self) -> list[UnitRow]:
        path = "/api/v1/execution-status"
        try:
            response = self._client.request("GET", path)
        except TransportFailure as failure:
            raise CallError(f"the orchestrator is unreachable: {failure.error_type}") from None
        if response.status_code >= 400:
            code = error_code(response)
            suffix = f" ({code})" if code else ""
            raise CallError(f"the orchestrator refused {path}: {response.status_code}{suffix}")
        try:
            body = response.json()
        except ValueError:
            raise CallError(f"the orchestrator answered {path} without JSON") from None
        if not isinstance(body, list):
            raise CallError("the execution-status answer isn't a list")
        return [parse_row(row) for row in body]
