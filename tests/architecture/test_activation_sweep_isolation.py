"""The sweep shares no import path with the orchestrator, writes to one endpoint, never pulls."""

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from activation_sweep.checkout import READ_ONLY, ForbiddenCommandError, run_git
from activation_sweep.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    is_allowed_write,
)

SWEEP = Path("src/activation_sweep")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "activation_sweep",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "os",
    "pathlib",
    "re",
    "subprocess",
    "typing",
    "__future__",
}

# The subcommands that would make this program act on the machine rather than read it. Named HERE
# rather than in the source, because a second copy of the allowlist beside the allowlist is a
# thing that can drift; what the source holds is the one list of what is permitted, and this is
# the control proving the complement is refused.
NEVER = ("pull", "merge", "reset", "checkout", "clean", "commit", "push", "rebase", "stash")


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


def test_the_sweep_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(SWEEP) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_sweep() -> None:
    assert {
        name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "activation_sweep"
    } == set()


def test_the_sweeps_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(SWEEP)} - ALLOWED_TOP_LEVEL == set()


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/evidence")
    assert not is_allowed_write(f"{unit}/dispatch")
    assert not is_allowed_write(f"{unit}/pr-merge")


def test_only_the_client_module_can_speak_http() -> None:
    """The program's whole entry in the repository's outbound allowlist is ONE file, and this is
    what keeps it there. Both halves of the malformed-URL guard live in that module -- `httpx`
    refuses some URLs at the constructor and others at request time -- so the CLI needs no import
    of an HTTP client to translate either, and a future one would red here first.
    """
    speaks = {
        str(path.relative_to(SWEEP))
        for path in SWEEP.rglob("*.py")
        if {"httpx", "requests", "urllib.request", "http.client", "aiohttp"} & _file_imports(path)
    }

    assert speaks == {"orchestrator_client.py"}


def test_the_sweep_has_no_read_surface_at_all() -> None:
    """It needs nothing back, so there is no reader to bound -- and none to widen by accident.

    Written as an assertion about the module rather than as prose, so adding a read means
    deleting a test that says reads do not exist, which is a decision somebody has to make.
    """
    import activation_sweep.orchestrator_client as client_module

    assert not hasattr(client_module, "is_allowed_read")
    assert not any(
        hasattr(OrchestratorClient, name) for name in ("get", "read_observations", "_read")
    )


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = _client(handler)
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/dispatch", {})
    assert seen == []
    assert client.record_observation({}) == {}
    assert seen == ["/api/v1/observations"]


def test_the_git_surface_can_read_and_fetch_and_nothing_else(tmp_path: Path) -> None:
    """ADR-0030 stops at recording. `pull` is UNREACHABLE here, not merely unused.

    `fetch` is the one member that writes anything, and what it writes is remote-tracking refs --
    never HEAD, never the index, never a tracked file.
    """
    assert "fetch" in READ_ONLY
    for subcommand in NEVER:
        assert subcommand not in READ_ONLY
        with pytest.raises(ForbiddenCommandError):
            run_git(tmp_path, subcommand)
    with pytest.raises(ForbiddenCommandError):
        run_git(tmp_path)
