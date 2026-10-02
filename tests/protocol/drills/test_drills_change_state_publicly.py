"""The drills change state only through public routes.

A drill that writes a row, or calls a service function directly, proves something about a path
production cannot take. So drill files may read the database but never write it, and may not
import a service. The one sanctioned write is lease expiry, because `DEFAULT_LEASE` is fifteen real
minutes and nothing may shorten it: `expire_latest_claim` is the only function in the shared setup
allowed to commit.
"""

import ast
from pathlib import Path

import pytest

DRILLS = sorted(Path("tests/protocol/drills").glob("test_drill_*.py"))
SUPPORT = Path("tests/_support/protocol.py")
WRITES = frozenset({"commit", "add", "add_all", "delete", "flush", "merge", "execute"})
SANCTIONED_WRITERS = ["expire_latest_claim"]


def _writes(tree: ast.AST) -> list[str]:
    return [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in WRITES
    ]


def _service_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
    return [name for name in found if name.startswith("orchestrator.services")]


def _writing_functions(tree: ast.Module) -> list[str]:
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and _writes(node)]


def test_there_are_drills_to_check() -> None:
    assert len(DRILLS) == 5


@pytest.mark.parametrize("path", DRILLS, ids=lambda path: path.stem)
def test_a_drill_neither_writes_nor_calls_a_service(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert _writes(tree) == []
    assert _service_imports(tree) == []


def test_lease_expiry_is_the_only_write_the_shared_setup_makes() -> None:
    tree = ast.parse(SUPPORT.read_text(encoding="utf-8"))

    assert _writing_functions(tree) == SANCTIONED_WRITERS


@pytest.mark.parametrize(
    "source",
    [
        "session.commit()\n",
        "session.add(row)\n",
        "session.execute(text('UPDATE claims SET x = 1'))\n",
        "from orchestrator.services.lifecycle import claims\n",
        "import orchestrator.services.verifier.evidence\n",
    ],
)
def test_the_scan_sees_a_write_or_a_service_call(source: str) -> None:
    tree = ast.parse(source)

    assert _writes(tree) or _service_imports(tree)


def test_the_scan_passes_a_read() -> None:
    tree = ast.parse("rows = session.scalars(select(Claim))\nclient.post('/api/v1/x')\n")

    assert (_writes(tree), _service_imports(tree)) == ([], [])
