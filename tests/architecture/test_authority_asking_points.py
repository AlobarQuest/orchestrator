"""Where the composed runner-authority question is asked, by walking the source.

`runner_authority_violation` is asked at breakdown ingress, at authority approval (the service and
the form the `/review` page renders) and at dispatch admission. The evidence pack reports it.
Unit registration asks only two of its three terms, the envelope's fields and its capability levels,
so a command-list defect in a hand-registered unit waits for approval or admission. Each surface's
behaviour is tested beside it; this file fails on a surface that joins, leaves or swaps its term.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path("src/orchestrator")
COMPOSED = "runner_authority_violation"
ASKING_POINTS = {
    "services/intake/decomposition.py": {"_validate_unit_constraints"},
    "services/intake/packages.py": {"record_approval"},
    "web.py": {"approve_authority"},
    "services/execution/dispatch.py": {"_authority_violation_reason"},
    "services/reporting/evidence_pack.py": {"evidence_pack_projection"},
}
REGISTRATION = "services/intake/packages.py"
REGISTRATION_TERMS = {"runner_envelope_field_violation", "validate_unit_capabilities"}


def _callers(name: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == name
                ):
                    module = path.relative_to(SOURCE).as_posix()
                    found.setdefault(module, set()).add(function.name)
    return found


def test_the_composed_predicate_has_its_listed_surfaces() -> None:
    assert _callers(COMPOSED) == ASKING_POINTS


def test_registration_asks_the_field_and_level_terms() -> None:
    for term in REGISTRATION_TERMS:
        assert "register_approved_unit" in _callers(term)[REGISTRATION]
