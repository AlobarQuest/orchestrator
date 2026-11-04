"""Fixtures that let a pass be driven end to end without touching the estate.

Every reader in this lane takes a transport, so the whole pass is exercisable in-process. The
handler below routes on the request URL, which is what makes a test able to answer differently for
each application -- the case that matters, because the four brains share a repository and a served
commit and a fixture that could not tell them apart would pass on a lane that could not either.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from alert_hub_client import KeyedAlerts
from revision_watcher import alerts as alerts_module

TIP = "a" * 40
OLD = "b" * 40
ELSEWHERE = "c" * 40
WHEN_TIP = "2026-09-08T10:00:00Z"
WHEN_OLD = "2026-09-06T06:15:00Z"


def github_transport(
    *,
    tip: str = TIP,
    relations: dict[str, str] | None = None,
    dates: dict[str, str] | None = None,
    branch_status: int = 200,
) -> httpx.MockTransport:
    relations = relations or {}
    dates = dates or {TIP: WHEN_TIP, OLD: WHEN_OLD, ELSEWHERE: WHEN_OLD}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/branches/" in path:
            if branch_status != 200:
                return httpx.Response(branch_status, json={})
            return httpx.Response(200, json={"commit": {"sha": tip}})
        if "/compare/" in path:
            head = path.rsplit("...", 1)[-1]
            status = relations.get(head)
            if status is None:
                return httpx.Response(404, json=None)
            return httpx.Response(200, json={"status": status})
        if "/commits/" in path:
            sha = path.rsplit("/", 1)[-1]
            date = dates.get(sha)
            if date is None:
                return httpx.Response(404, json=None)
            return httpx.Response(200, json={"commit": {"committer": {"date": date}}})
        return httpx.Response(404, json=None)

    return httpx.MockTransport(handler)


def application_transport(bodies: dict[str, Any]) -> httpx.MockTransport:
    """Keyed by application HOST, so one handler answers differently per subject."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies.get(request.url.host, _MISSING)
        if body is _MISSING:
            return httpx.Response(500, text="not a health response")
        if isinstance(body, int):
            return httpx.Response(body, text="down")
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def platform_transport(applications: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=applications)

    return httpx.MockTransport(handler)


_MISSING = object()


class Hub:
    """A recording alert-hub: what was sent, and where the state file lives."""

    def __init__(self, state: Path) -> None:
        self.state = state
        self.sent: list[dict[str, str]] = []
        self.status = 202

    def keys(self, status: str) -> list[str]:
        return sorted(body["key"] for body in self.sent if body["status"] == status)

    def remembered(self) -> set[str]:
        path = self.state / alerts_module.ALERTS_FILE
        return set(json.loads(path.read_text())) if path.exists() else set()


@pytest.fixture(autouse=True)
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Hub:
    """Every test runs against a recording hub and its own state directory, never the real ones."""
    recorder = Hub(tmp_path / "alert-state")

    def _hub(request: httpx.Request) -> httpx.Response:
        recorder.sent.append(json.loads(request.content))
        return httpx.Response(recorder.status)

    monkeypatch.setattr(
        alerts_module,
        "KeyedAlerts",
        functools.partial(KeyedAlerts, transport=httpx.MockTransport(_hub), sleep=lambda _: None),
    )
    monkeypatch.setenv(alerts_module.STATE_DIR_ENV, str(recorder.state))
    monkeypatch.setenv(alerts_module.TOKEN_ENV, "alert-token")
    return recorder
