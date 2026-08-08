"""The ledger shares no import path with the orchestrator and writes to one endpoint."""

import ast
from pathlib import Path

import httpx
import pytest

from landing_ledger.github import ForbiddenMethodError, GitHubReader
from landing_ledger.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    is_allowed_write,
)

LEDGER = Path("src/landing_ledger")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "landing_ledger",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "os",
    "re",
    "typing",
    "__future__",
}


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
    return names


def test_the_ledger_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(LEDGER) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_ledger() -> None:
    assert {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "landing_ledger"} == (
        set()
    )


def test_the_ledgers_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(LEDGER)} - ALLOWED_TOP_LEVEL == set()


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/evidence")
    assert not is_allowed_write(f"{unit}/adjudications")
    assert not is_allowed_write(f"{unit}/dispatch")


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/dispatch", {})
    assert seen == []


def test_the_github_half_only_ever_reads() -> None:
    """It is a reader. The one call site takes a path and issues GET; there is no other verb."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={})

    reader = GitHubReader(token="t", transport=httpx.MockTransport(handler))
    reader.get("/repos/AlobarQuest/orchestrator")

    assert seen == ["GET"]
    with pytest.raises(ForbiddenMethodError):
        reader.get("repos/AlobarQuest/orchestrator")
