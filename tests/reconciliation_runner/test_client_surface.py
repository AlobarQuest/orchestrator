"""AC-009: the runner shares no import path with the orchestrator.

`orchestrator.persistence` above all: a database session would let the runner write canonical
state directly, and the report-only mandate would be a comment rather than a property.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`.
"""

import ast
from pathlib import Path

from reconciliation_runner.client import ALLOWED_WRITE_ENDPOINTS

RUNNER = Path("src/reconciliation_runner")


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
