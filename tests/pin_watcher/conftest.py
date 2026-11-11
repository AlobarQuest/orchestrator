"""A GitHub the watcher can be pointed at, built from a description of an estate.

Every test states the estate it is about -- which repositories exist, which carry a caller, what
each caller pins, which are members -- and gets a transport that answers exactly that, plus an
orchestrator that serves the member record. Nothing here reaches the network, and a path the
fixture was not told about answers 404, so a test that measured something the estate does not
contain fails rather than passing on a default.

Every repository is owned by the account its name starts with, a user account unless `accounts`
says otherwise, and is public unless it is in `private`.
"""

from __future__ import annotations

import base64
import functools
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from alert_hub_client import KeyedAlerts
from pin_watcher import alerts as alerts_module

RUNNER = "AlobarQuest/factory-runner"
SERVICES = (
    "dependency_updates",
    "security_patches",
    "app_changes",
    "backups",
    "monitoring",
    "rollouts",
    "credential_rotation",
)
RECOMMENDED = "a" * 40
RECOMMENDED_DATE = "2026-09-01T10:00:00Z"


def _contents(body: str) -> httpx.Response:
    encoded = base64.b64encode(body.encode()).decode()
    return httpx.Response(200, json={"content": encoded, "encoding": "base64"})


def member_wire(repository: str, state: str = "member") -> dict[str, Any]:
    """One member as `GET /api/v1/members` serves it."""
    return {
        "repository": repository,
        "owner": "placeholder-owner",
        "state": state,
        "working_copy": None,
        "machine_local": False,
        "apps": [],
        "grants": {service: {"state": "not_granted"} for service in SERVICES},
        "holds": [],
    }


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
        private: tuple[str, ...] = (),
        members: tuple[str, ...] | None = None,
        retired: tuple[str, ...] = (),
        accounts: dict[str, str] | None = None,
        unreadable_accounts: dict[str, str] | None = None,
        unreadable_repositories: tuple[str, ...] = (),
        endless: bool = False,
    ) -> None:
        self.callers = callers
        self.recommended = recommended
        self.comparisons = comparisons or {}
        self.dates = {recommended: RECOMMENDED_DATE, **(dates or {})}
        self.unreadable = unreadable or set()
        self.other_repositories = other_repositories
        self.archived = archived
        self.private = private
        self.names = [*callers, *other_repositories, *self.unreadable, *archived, *private]
        # By default one repository is a member and the rest come through the public listing.
        self.members = members if members is not None else tuple(self.names[:1])
        self.retired = retired
        # login (casefolded) -> GitHub's `type`; absent means a user account.
        self.accounts = {k.casefold(): v for k, v in (accounts or {}).items()}
        # login (casefolded) -> "type" (the account answers 404), "page" (a page answers 500) or
        # "missing" (a page answers 404).
        self.unreadable_accounts = {k.casefold(): v for k, v in (unreadable_accounts or {}).items()}
        self.unreadable_repositories = unreadable_repositories
        self.endless = endless
        self.requests: list[str] = []
        self.listing_queries: list[tuple[str, dict[str, str]]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def record(self) -> httpx.MockTransport:
        """The orchestrator's member record, as `GET /api/v1/members` serves it."""
        body = {
            "members": [member_wire(name) for name in self.members]
            + [member_wire(name, "retired") for name in self.retired]
        }

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/v1/members":
                return httpx.Response(200, json=body)
            return httpx.Response(404)

        return httpx.MockTransport(handle)

    def _type(self, login: str) -> str:
        return self.accounts.get(login.casefold(), "User")

    def _owned_by(self, login: str) -> list[dict[str, Any]]:
        return [
            {
                "full_name": name,
                "private": name in self.private,
                "archived": name in self.archived,
            }
            for name in self.names
            if name.split("/", 1)[0].casefold() == login.casefold()
        ]

    def _listing(self, request: httpx.Request, login: str, kind: str) -> httpx.Response:
        self.listing_queries.append((request.url.path, dict(request.url.params)))
        if self._type(login) != kind:
            return httpx.Response(404)
        failure = self.unreadable_accounts.get(login.casefold())
        if failure in ("page", "missing"):
            return httpx.Response(500 if failure == "page" else 404)
        if self.endless:
            return httpx.Response(200, json=self._owned_by(login) or [{"x": 1}])
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=[] if page > 1 else self._owned_by(login))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if request.method != "GET":
            return httpx.Response(405)
        segments = path.strip("/").split("/")
        if segments[0] == "users" and len(segments) == 2:
            login = segments[1]
            if self.unreadable_accounts.get(login.casefold()) == "type":
                return httpx.Response(404)
            return httpx.Response(200, json={"login": login, "type": self._type(login)})
        if segments[0] == "users" and segments[2:] == ["repos"]:
            return self._listing(request, segments[1], "User")
        if segments[0] == "orgs" and segments[2:] == ["repos"]:
            return self._listing(request, segments[1], "Organization")
        if segments[0] == "repos" and len(segments) == 3:
            name = f"{segments[1]}/{segments[2]}"
            if name in self.unreadable_repositories or name not in self.names:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "full_name": name,
                    "private": name in self.private,
                    "archived": name in self.archived,
                },
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
