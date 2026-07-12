"""AC-009: the runner shares no import path with the orchestrator.

`orchestrator.persistence` above all: a database session would let the runner write canonical
state directly, and the report-only mandate would be a comment rather than a property.
"""

import ast
from pathlib import Path

from reconciliation_runner.client import ALLOWED_WRITE_ENDPOINTS

RUNNER = Path("src/reconciliation_runner")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "pydantic",
    "typer",
    "reconciliation_runner",
    "json",
    "os",
    "hashlib",
    "datetime",
    "pathlib",
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


def test_the_runner_imports_nothing_from_the_orchestrator() -> None:
    offenders = {name for name in _imports(RUNNER) if name.split(".")[0] == "orchestrator"}

    assert offenders == set()


def test_the_orchestrator_imports_nothing_from_the_runner() -> None:
    offenders = {
        name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "reconciliation_runner"
    }

    assert offenders == set()


def test_the_runner_depends_only_on_httpx_pydantic_and_typer() -> None:
    offenders = {name.split(".")[0] for name in _imports(RUNNER)} - ALLOWED_TOP_LEVEL

    assert offenders == set()


def _string_constants(root: Path) -> set[str]:
    """Every string CONSTANT in the runner, excluding docstrings.

    Scanning raw text would flag the docstrings that EXPLAIN why an endpoint is forbidden --
    prose that is worth keeping. What must not exist is a callable path.
    """
    values: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in docstrings
            ):
                values.add(node.value)
    return values


def test_the_runners_write_surface_is_exactly_two_endpoints() -> None:
    assert ALLOWED_WRITE_ENDPOINTS == frozenset(
        {"/api/v1/observations", "/api/v1/reconciliation/detect"}
    )

    # No CALLABLE path to canonical state exists in the runner -- not merely untested, unreachable.
    constants = _string_constants(RUNNER)
    forbidden = {"deployment-observations", "/commands/", "/adjudications", "/evidence"}
    offenders = {value for value in constants for marker in forbidden if marker in value}

    assert offenders == set()
