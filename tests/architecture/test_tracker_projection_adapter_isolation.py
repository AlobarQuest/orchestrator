"""The adapter shares no import path with the orchestrator and calls no canonical surface."""

import ast
from pathlib import Path

import httpx
import pytest

from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    _is_allowed_write,
)

ADAPTER = Path("src/tracker_projection_adapter")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "tracker_projection_adapter",
    "json",
    "os",
    "re",
    "dataclasses",
    "datetime",
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


def test_adapter_imports_nothing_from_the_orchestrator() -> None:
    offenders = {n for n in _imports(ADAPTER) if n.split(".")[0] == "orchestrator"}
    assert offenders == set()


def test_orchestrator_imports_nothing_from_the_adapter() -> None:
    offenders = {
        n for n in _imports(ORCHESTRATOR) if n.split(".")[0] == "tracker_projection_adapter"
    }
    assert offenders == set()


def test_adapter_third_party_deps_are_confined() -> None:
    offenders = {n.split(".")[0] for n in _imports(ADAPTER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_write_surface_allows_only_the_two_report_only_endpoints() -> None:
    assert _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/tracker-binding"
    )
    assert _is_allowed_write("/api/v1/reconciliation/tracker-detect")
    assert not _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready"
    )
    assert not _is_allowed_write("/api/v1/work-units/00000000-0000-0000-0000-000000000000/evidence")
    assert not _is_allowed_write("/api/v1/observations")
    assert not _is_allowed_write(
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/adjudications"
    )


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-system",
        token="t",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready", {})
    assert seen == []
