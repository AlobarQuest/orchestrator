"""The adapter shares no import path with the orchestrator and calls no canonical surface."""

import ast
from pathlib import Path

from tracker_projection_adapter.orchestrator_client import ALLOWED_WRITE_PATTERN

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


def test_write_pattern_matches_only_tracker_binding() -> None:
    uid = "123e4567-e89b-12d3-a456-426614174000"
    assert ALLOWED_WRITE_PATTERN.match(f"/api/v1/work-units/{uid}/tracker-binding")
    for forbidden in (
        f"/api/v1/work-units/{uid}/commands/ready",
        f"/api/v1/work-units/{uid}/evidence",
        "/api/v1/observations",
        f"/api/v1/work-units/{uid}/adjudications",
    ):
        assert not ALLOWED_WRITE_PATTERN.match(forbidden)
