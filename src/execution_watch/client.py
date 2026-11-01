"""The one surface this lane owns: a single orchestrator read on the observer bearer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem, error_code
from execution_watch.decide import SUBJECT_KINDS, SUBJECT_UNIT, StatusRow

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


def _flag(row: dict[str, Any], field: str, *, required: bool = True) -> bool:
    value = row.get(field)
    if value is None and not required:
        return False
    if not isinstance(value, bool):
        raise CallError(f"the execution-status answer's {field} is not a boolean")
    return value


def parse_row(row: object) -> StatusRow | None:
    """One row, or `None` for a kind this lane doesn't know yet. A row with no `subject_kind` is a
    unit from a server older than these fields, and a row of a kind it knows is held to its fields:
    tolerance is for a deploy, never for a damaged answer."""
    if not isinstance(row, dict):
        raise CallError("the execution-status answer holds a row that isn't an object")
    kind = row.get("subject_kind", SUBJECT_UNIT)
    if kind not in SUBJECT_KINDS:
        return None
    unit = kind == SUBJECT_UNIT
    unit_id = _text(row, "unit_id", required=unit)
    older_server = unit and "subject_id" not in row
    subject_id = unit_id if older_server else _text(row, "subject_id", required=True)
    return StatusRow(
        unit_id=unit_id,
        state=str(_text(row, "state", required=True)),
        change_class=_text(row, "change_class", required=False),
        is_rotation=_flag(row, "is_rotation"),
        standing=_flag(row, "standing", required=unit),
        target_repository=_text(row, "target_repository", required=False),
        granted=_flag(row, "granted", required=unit),
        latest_dispatch_status=_text(row, "latest_dispatch_status", required=False),
        latest_dispatch_at=_moment(row.get("latest_dispatch_at")),
        has_claim=_flag(row, "has_claim", required=unit),
        lease_lapsed=_flag(row, "lease_lapsed", required=unit),
        breaker_open=_flag(row, "breaker_open", required=unit),
        subject_kind=str(kind),
        subject_id=str(subject_id),
        entered_at=_moment(row.get("entered_at")),
        waiting=_flag(row, "waiting", required=False),
        overdue=_flag(row, "overdue", required=False),
        policy_not_applied=_text(row, "policy_not_applied", required=False),
        proposed_by=_text(row, "proposed_by", required=False),
        rotation_package=_flag(row, "rotation_package", required=False),
        verification_overdue=_flag(row, "verification_overdue", required=False),
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
            raise CallError(f"the orchestrator base URL {problem}")
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
            raise CallError(
                f"the orchestrator base URL is not usable: {failure.error_type}"
            ) from None

    def close(self) -> None:
        self._client.close()

    def execution_status(self) -> list[StatusRow]:
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
        return [parsed for row in body if (parsed := parse_row(row)) is not None]
