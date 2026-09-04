"""The watcher shares no import path with the orchestrator, writes to one endpoint, only reads."""

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from pin_watcher.compare import CLEAN_STATE, Caller
from pin_watcher.github import ForbiddenMethodError, GitHubReader, PinWatcherError
from pin_watcher.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    PinWriteError,
    UnusableEndpointError,
    is_allowed_write,
    open_client,
)

WATCHER = Path("src/pin_watcher")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "pin_watcher",
    "base64",
    "dataclasses",
    "hashlib",
    "json",
    "os",
    "re",
    "typing",
    # `urllib.parse` only, for the base-URL shape check. `urllib.request` is an HTTP client and
    # is in the invariant scan's own `HTTP_CLIENTS` set, so it could never arrive here unnoticed.
    "urllib",
    "__future__",
}


def _client(handler: Any) -> OrchestratorClient:
    return OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )


def _file_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        names |= _file_imports(path)
    return names


def test_the_watcher_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(WATCHER) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_watcher() -> None:
    assert {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "pin_watcher"} == set()


def test_the_watcher_imports_no_sibling_lane() -> None:
    """Lanes share DOMAIN knowledge; this one has none to borrow.

    Its client is the activation sweep's, deliberately COPIED -- a lane that reached into a
    sibling for plumbing would let an unrelated refactor break this lane's schedule.
    """
    siblings = {
        "activation_sweep",
        "bump_proposer",
        "change_proposer",
        "deploy_watcher",
        "estate_lander",
        "inert_lander",
        "landing_ledger",
        "reconciliation_runner",
        "tracker_projection_adapter",
        "work_carrier",
        "work_watcher",
    }
    assert {name.split(".")[0] for name in _imports(WATCHER)} & siblings == set()


def test_the_watchers_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(WATCHER)} - ALLOWED_TOP_LEVEL == set()


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/dispatch")
    assert not is_allowed_write(f"{unit}/pr-merge")


def test_only_the_two_client_modules_can_speak_http() -> None:
    """The lane's whole entry in the repository's outbound allowlist is two files.

    The split is the property rather than an exception to it: one module READS GitHub with a
    credential that must never write, the other WRITES to the orchestrator with a credential that
    must never read. A single module serving both would hold both reaches behind one guard.
    """
    speaks = {
        str(path.relative_to(WATCHER))
        for path in WATCHER.rglob("*.py")
        if {"httpx", "requests", "urllib.request", "http.client", "aiohttp"} & _file_imports(path)
    }
    assert speaks == {"github.py", "orchestrator_client.py"}


def test_the_github_reader_never_issues_anything_but_a_get() -> None:
    """Read-only by construction, not by convention: there is no method parameter to get wrong."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={})

    with GitHubReader(token="t", transport=httpx.MockTransport(handler)) as reader:
        reader.get("/repos/o/r")
    assert seen == ["GET"]


def test_the_github_reader_refuses_a_path_it_did_not_build() -> None:
    """An absolute path is the whole surface; anything else could leave the intended host."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with GitHubReader(token="t", transport=transport) as reader:
        with pytest.raises(ForbiddenMethodError):
            reader.get("https://elsewhere.example/steal")


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    """Refused before a request is built, so the guard cannot be satisfied by a 404 downstream."""
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.url.path)
        return httpx.Response(201, json={})

    with pytest.raises(ForbiddenEndpointError):
        _client(handler).post("/api/v1/work-units", {})
    assert reached == []


def test_a_rejected_write_carries_the_status_and_nothing_else() -> None:
    """A rejection body echoes the command back; printing it is how a secret reaches a log."""
    with pytest.raises(PinWriteError) as raised:
        _client(lambda r: httpx.Response(403, json={"echo": "sensitive"})).record_observation({})
    assert "403" in str(raised.value)
    assert "sensitive" not in str(raised.value)


def test_an_unreachable_github_is_this_modules_error_rather_than_a_bare_httpx_one() -> None:
    """An escape here ends the pass with a traceback instead of costing one repository its row."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with GitHubReader(token="t", transport=httpx.MockTransport(handler)) as reader:
        with pytest.raises(PinWatcherError, match="unreachable"):
            reader.get("/repos/o/r")


@pytest.mark.parametrize("url", ["https://host..example", "http://x.example", "https://", "x"])
def test_an_unusable_orchestrator_url_is_refused_before_a_client_exists(url: str) -> None:
    """`httpx` refuses some malformed URLs at the constructor and others at request time, so a
    guard on one half is not a guard -- and the two halves would carry different exit codes.
    """
    with pytest.raises(UnusableEndpointError):
        open_client(base_url=url, credential_key_id="k", token="t")


def test_a_usable_orchestrator_url_still_opens() -> None:
    """The control that keeps the guard above from being satisfied by refusing everything."""
    with open_client(
        base_url="https://sds.example.net", credential_key_id="k", token="t"
    ) as client:
        assert client is not None


def test_only_one_state_is_clean_so_a_state_added_later_is_a_finding_by_default() -> None:
    """Spelled as the complement of `current` rather than as a list of the bad ones.

    A sixth state added without touching this predicate reports; the other arrangement would
    silently exempt it, which is the direction that fails open.
    """
    assert CLEAN_STATE == "current"
    invented = Caller(
        repository="o/r",
        pin="a" * 40,
        state="something-new",
        behind_by=None,
        ahead_by=None,
        pinned_at="2026-09-01T10:00:00Z",
    )
    assert invented.is_finding
