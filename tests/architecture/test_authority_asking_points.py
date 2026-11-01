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
COMMAND_TERM = "runner_command_authority_violation"


def _called_name(func: ast.expr) -> str | None:
    """The name a call goes to, whether written `name(...)` or `module.name(...)`."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _callers(name: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and _called_name(node.func) == name:
                    module = path.relative_to(SOURCE).as_posix()
                    found.setdefault(module, set()).add(function.name)
    return found


def test_the_composed_predicate_has_its_listed_surfaces() -> None:
    assert _callers(COMPOSED) == ASKING_POINTS


def test_registration_asks_the_field_and_level_terms_and_not_the_command_term() -> None:
    for term in REGISTRATION_TERMS:
        assert "register_approved_unit" in _callers(term)[REGISTRATION]
    assert "register_approved_unit" not in _callers(COMMAND_TERM).get(REGISTRATION, set())


def test_callers_sees_async_defs_and_attribute_calls(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text("async def a():\n    x.target()\n\ndef b():\n    target()\n")
    tree = ast.parse(source.read_text())
    names = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _called_name(node.func) == "target"
    }
    assert names == {"a", "b"}
