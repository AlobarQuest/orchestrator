"""The installer shares no import path with the orchestrator; each client reaches one route."""

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from tool_installer.github import ForbiddenMethodError, GitHubReader, GitHubReadError
from tool_installer.orchestrator_client import (
    ForbiddenEndpointError,
    ObservationWriteError,
    OrchestratorClient,
    UnusableEndpointError,
    is_allowed_write,
    open_client,
)
from tool_installer.policy_client import ForbiddenEndpointError as ForbiddenReadError
from tool_installer.policy_client import (
    PolicyClient,
    PolicyReadError,
    is_allowed_read,
    open_policy_client,
)
from tool_installer.policy_client import UnusableEndpointError as PolicyUrlError

INSTALLER = Path("src/tool_installer")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "tool_installer",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "os",
    "re",
    "shutil",
    "subprocess",
    "sys",
    "tempfile",
    "pathlib",
    "typing",
    "zoneinfo",
    # `urllib.parse` only, for the base-URL shape check. `urllib.request` is an HTTP client and is
    # in the invariant scan's own `HTTP_CLIENTS` set, so it could never arrive here unnoticed.
    "urllib",
    "__future__",
}


def _observer(handler: Any) -> OrchestratorClient:
    return OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )


def _system(handler: Any) -> PolicyClient:
    return PolicyClient(
        base_url="https://x",
        credential_key_id="orchestrator-system",
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


def test_the_installer_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(INSTALLER) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_installer() -> None:
    found = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "tool_installer"}
    assert found == set()


def test_the_installer_imports_no_sibling_lane() -> None:
    """Lanes share DOMAIN knowledge; this one has none to borrow.

    Its window predicate is the orchestrator's, deliberately COPIED -- see `window.py`. A lane
    that reached into a sibling for plumbing would let an unrelated refactor break its schedule.
    """
    siblings = {
        "activation_sweep",
        "bump_proposer",
        "change_proposer",
        "deploy_watcher",
        "estate_lander",
        "inert_lander",
        "landing_ledger",
        "pin_watcher",
        "reconciliation_runner",
        "tracker_projection_adapter",
        "work_carrier",
        "work_watcher",
    }
    assert {name.split(".")[0] for name in _imports(INSTALLER)} & siblings == set()


def test_the_installers_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(INSTALLER)} - ALLOWED_TOP_LEVEL == set()


def test_only_the_three_client_modules_can_speak_http() -> None:
    """The lane's whole entry in the repository's outbound allowlist is three files.

    The split is the property rather than an exception to it: one module READS GitHub with a
    credential that must never write, one READS the deployed policy with the SYSTEM bearer, and
    one WRITES to the orchestrator with the OBSERVER bearer. A single module serving any two would
    hold both reaches behind one guard, and the SYSTEM bearer is the one that can drive a work
    unit's lifecycle.
    """
    speaks = {
        str(path.relative_to(INSTALLER))
        for path in INSTALLER.rglob("*.py")
        if {"httpx", "requests", "urllib.request", "http.client", "aiohttp"} & _file_imports(path)
    }
    assert speaks == {"github.py", "policy_client.py", "orchestrator_client.py"}


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/dispatch")
    assert not is_allowed_write(f"{unit}/pr-merge")


def test_the_system_credential_may_read_the_policy_and_nothing_else() -> None:
    """The SYSTEM bearer can drive a unit's lifecycle, so the module holding it reaches one route.

    Spelled as equality against the one permitted path rather than as a list of refused ones: a
    route invented later is refused by default, where the other arrangement would admit it.
    """
    assert is_allowed_read("/api/v1/factory-policy")
    assert not is_allowed_read("/api/v1/factory-policy/")
    assert not is_allowed_read("/api/v1/work-units")
    assert not is_allowed_read("/api/v1/in-flight-units")
    assert not is_allowed_read("/api/v1/status-ledger")


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    """Refused before a request is built, so the guard cannot be satisfied by a 404 downstream."""
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.url.path)
        return httpx.Response(201, json={})

    with pytest.raises(ForbiddenEndpointError):
        _observer(handler).post("/api/v1/work-units", {})
    assert reached == []


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.url.path)
        return httpx.Response(200, json={})

    with pytest.raises(ForbiddenReadError):
        _system(handler).get("/api/v1/work-units")
    assert reached == []


def test_the_policy_client_never_issues_anything_but_a_get() -> None:
    """Read-only by construction, not by convention: there is no method parameter to get wrong."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={"reach": []})

    _system(handler).factory_policy()
    assert seen == ["GET"]


def test_the_github_reader_never_issues_anything_but_a_get() -> None:
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
        with pytest.raises(ForbiddenMethodError):
            reader.get("//elsewhere.example/steal")


@pytest.mark.parametrize(
    ("client", "error"),
    [
        (_observer, ObservationWriteError),
        (_system, PolicyReadError),
    ],
)
def test_a_rejection_carries_the_status_and_nothing_else(client: Any, error: type) -> None:
    """A rejection body echoes the command back; printing it is how a secret reaches a log."""
    responder = client(lambda r: httpx.Response(403, json={"echo": "sensitive"}))
    with pytest.raises(error) as raised:
        if isinstance(responder, OrchestratorClient):
            responder.record_observation({})
        else:
            responder.factory_policy()
    assert "403" in str(raised.value)
    assert "sensitive" not in str(raised.value)


def test_an_unreachable_github_is_this_modules_error_rather_than_a_bare_httpx_one() -> None:
    """An escape here ends the pass with a traceback instead of a named refusal."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with GitHubReader(token="t", transport=httpx.MockTransport(handler)) as reader:
        with pytest.raises(GitHubReadError, match="unreachable"):
            reader.get("/repos/o/r")


@pytest.mark.parametrize("url", ["https://host..example", "http://x.example", "https://", "x"])
@pytest.mark.parametrize(
    ("opener", "error"),
    [(open_client, UnusableEndpointError), (open_policy_client, PolicyUrlError)],
)
def test_an_unusable_orchestrator_url_is_refused_before_a_client_exists(
    opener: Any, error: type, url: str
) -> None:
    """`httpx` refuses some malformed URLs at the constructor and others at request time, so a
    guard on one half is not a guard -- and the two halves would carry different exit codes.

    EACH CLIENT MODULE RAISES ITS OWN ERROR, and pairing them here rather than catching a shared
    base is the point: the two hold different credentials, and a CLI that caught one family for
    both would treat a policy-read failure and an observation-write failure as one condition when
    they are refused at different steps of the pass and carry different exit codes.
    """
    with pytest.raises(error):
        opener(base_url=url, credential_key_id="k", token="t")


@pytest.mark.parametrize("opener", [open_client, open_policy_client])
def test_a_usable_orchestrator_url_still_opens(opener: Any) -> None:
    """The control that keeps the guard above from being satisfied by refusing everything."""
    with opener(base_url="https://sds.example.net", credential_key_id="k", token="t") as client:
        assert client is not None
