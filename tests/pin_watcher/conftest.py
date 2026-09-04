"""A GitHub the watcher can be pointed at, built from a description of an estate.

Every test states the estate it is about -- which repositories exist, which carry a caller, what
each caller pins -- and gets a transport that answers exactly that. Nothing here reaches the
network, and a path the fixture was not told about answers 404, so a test that measured something
the estate does not contain fails rather than passing on a default.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

RUNNER = "AlobarQuest/factory-runner"
RECOMMENDED = "a" * 40
RECOMMENDED_DATE = "2026-09-01T10:00:00Z"


def _contents(body: str) -> httpx.Response:
    encoded = base64.b64encode(body.encode()).decode()
    return httpx.Response(200, json={"content": encoded, "encoding": "base64"})


class Estate:
    """What GitHub would say, if the estate were as described."""

    def __init__(
        self,
        *,
        callers: dict[str, str],
        recommended: str = RECOMMENDED,
        comparisons: dict[str, dict[str, Any]] | None = None,
        dates: dict[str, str] | None = None,
        unreadable: set[str] | None = None,
        other_repositories: tuple[str, ...] = (),
        archived: tuple[str, ...] = (),
    ) -> None:
        self.callers = callers
        self.recommended = recommended
        self.comparisons = comparisons or {}
        self.dates = {recommended: RECOMMENDED_DATE, **(dates or {})}
        self.unreadable = unreadable or set()
        self.other_repositories = other_repositories
        self.archived = archived
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if request.method != "GET":
            return httpx.Response(405)
        if path == "/user/repos":
            page = int(request.url.params.get("page", "1"))
            if page > 1:
                return httpx.Response(200, json=[])
            names = [
                *self.callers,
                *self.other_repositories,
                *self.unreadable,
            ]
            return httpx.Response(
                200,
                json=[{"full_name": name, "archived": False} for name in names]
                + [{"full_name": name, "archived": True} for name in self.archived],
            )
        if path == f"/repos/{RUNNER}/contents/RECOMMENDED_CALLER_PIN":
            return _contents(self.recommended + "\n")
        caller_suffix = "/contents/.github/workflows/factory-runner-pilot.yml"
        if path.endswith(caller_suffix):
            repository = path[len("/repos/") : -len(caller_suffix)]
            if repository in self.unreadable:
                return _contents("jobs:\n  run:\n    uses: nothing-useful\n")
            pin = self.callers.get(repository)
            if pin is None:
                return httpx.Response(404)
            return _contents(
                f"jobs:\n  run:\n    uses: AlobarQuest/factory-runner/"
                f".github/workflows/factory-runner.yml@{pin}\n"
            )
        if path.startswith(f"/repos/{RUNNER}/compare/"):
            pin = path.rsplit("...", 1)[-1]
            answer = self.comparisons.get(pin)
            if answer is None:
                return httpx.Response(404)
            return httpx.Response(200, json=answer)
        if path.startswith(f"/repos/{RUNNER}/commits/"):
            sha = path.rsplit("/", 1)[-1]
            date = self.dates.get(sha)
            if date is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"commit": {"committer": {"date": date}}})
        return httpx.Response(404)


def identical() -> dict[str, Any]:
    return {"status": "identical", "behind_by": 0, "ahead_by": 0}


def behind(count: int) -> dict[str, Any]:
    return {"status": "behind", "behind_by": count, "ahead_by": 0}


def ahead(count: int) -> dict[str, Any]:
    return {"status": "ahead", "behind_by": 0, "ahead_by": count}


def diverged(back: int, forward: int) -> dict[str, Any]:
    return {"status": "diverged", "behind_by": back, "ahead_by": forward}


@pytest.fixture
def written() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def orchestrator(written: list[dict[str, Any]]) -> httpx.MockTransport:
    """An orchestrator that accepts observations and remembers them."""

    def handle(request: httpx.Request) -> httpx.Response:
        written.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "00000000-0000-0000-0000-000000000000"})

    return httpx.MockTransport(handle)
