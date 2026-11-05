"""No SQL `IN (...)` text is built with `repr`.

`f"col IN {VOCAB!r}"` renders a one-element tuple as `('a',)`, which is not valid to compare with
a migration's text (and is an error in some dialects). `models.py` and every migration after 0047
join quoted members explicitly. Older revisions (0012, 0013, 0014, 0019) used `!r` and are history.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MODELS = ROOT / "src" / "orchestrator" / "persistence" / "models.py"
MIGRATIONS = ROOT / "migrations" / "versions"
FIRST_GUARDED_REVISION = 48


def _guarded_files() -> list[Path]:
    revisions = [
        path
        for path in sorted(MIGRATIONS.glob("*.py"))
        if (match := re.match(r"(\d+)_", path.name))
        and int(match.group(1)) >= FIRST_GUARDED_REVISION
    ]
    return [MODELS, *revisions]


def _literal_text(node: ast.AST) -> str:
    return "".join(
        part.value
        for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


def repr_in_violations(source: str) -> list[int]:
    """Line numbers of an `IN` text built with `!r`, `repr(` or `%r`."""
    violations: list[int] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            uses_repr = any(
                isinstance(part, ast.FormattedValue) and part.conversion == ord("r")
                for part in node.values
            )
            if uses_repr and " IN " in _literal_text(node):
                violations.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "repr"
        ):
            continue
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = node.left
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                if " IN " in left.value and "%r" in left.value:
                    violations.append(node.lineno)
    # `repr(` inside a string-building expression that names IN: check the enclosing statement.
    for statement in ast.walk(tree):
        if isinstance(statement, ast.stmt) and not isinstance(
            statement, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)
        ):
            calls_repr = any(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "repr"
                for sub in ast.walk(statement)
            )
            if calls_repr and " IN " in _literal_text(statement):
                violations.append(statement.lineno)
    return sorted(set(violations))


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda path: path.name)
def test_no_in_list_is_built_with_repr(path: Path) -> None:
    assert repr_in_violations(path.read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        'x = f"x IN {V!r}"',
        'x = "x IN %r" % (V,)',
        'x = "x IN " + repr(V)',
        'x = f"x IN {repr(V)}"',
    ],
)
def test_the_guard_fires_on_each_seeded_shape(source: str) -> None:
    assert repr_in_violations(source) != []


def test_the_guard_ignores_repr_that_builds_no_in_list() -> None:
    assert repr_in_violations('x = f"name {V!r}"\ny = repr(V)') == []


def test_the_guard_scans_models_and_only_late_migrations() -> None:
    names = {path.name for path in _guarded_files()}
    assert "models.py" in names
    assert not {n for n in names if n[:4] in {"0012", "0013", "0014", "0019"}}
