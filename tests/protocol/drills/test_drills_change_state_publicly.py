"""The drills change state only through public routes.

A drill that writes a row, or calls a service or route function directly, proves something about a
path production cannot take. So drill files may read the database but never write it, and may not
import a service, a route module or a SQL write construct. The one sanctioned write is lease expiry,
because `DEFAULT_LEASE` is fifteen real minutes and nothing may shorten it: `expire_latest_claim` is
the only function in the shared setup allowed to write.

The scan catches accidental shortcuts, not deliberate evasion: `getattr(session, "commit")()` would
pass it. It is a tripwire for a drill author reaching for the convenient path, not a sandbox.
"""

import ast
from pathlib import Path

import pytest

DRILL_DIR = Path("tests/protocol/drills")
DRILLS = sorted(DRILL_DIR.glob("test_drill_*.py"))
SCANNED = sorted(DRILL_DIR.glob("*.py"))
SHARED_SETUP = [Path("tests/_support/protocol.py"), Path("tests/protocol/conftest.py")]
WRITES = frozenset(
    {
        "commit",
        "add",
        "add_all",
        "delete",
        "flush",
        "merge",
        "execute",
        "begin",
        "exec_driver_sql",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "bulk_update_mappings",
    }
)
FORBIDDEN_MODULES = ("orchestrator.services", "orchestrator.api.routes")
SQL_WRITE_CONSTRUCTS = frozenset({"update", "insert", "delete", "text"})
SANCTIONED_WRITERS = ["expire_latest_claim"]


def _writes(tree: ast.AST) -> list[str]:
    return [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in WRITES
    ]


def _forbidden_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(FORBIDDEN_MODULES)]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            if node.module.startswith(FORBIDDEN_MODULES):
                found.append(node.module)
            elif node.module == "orchestrator" and names & {"services", "api"}:
                found.append(f"orchestrator.{sorted(names & {'services', 'api'})[0]}")
            elif node.module == "orchestrator.api" and "routes" in names:
                found.append("orchestrator.api.routes")
            elif node.module.startswith("sqlalchemy") and names & SQL_WRITE_CONSTRUCTS:
                found.append(f"{node.module}: {sorted(names & SQL_WRITE_CONSTRUCTS)}")
    return found


def _writing_functions(tree: ast.AST) -> list[str]:
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    return sorted(
        node.name for node in ast.walk(tree) if isinstance(node, functions) and _writes(node)
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_there_are_drills_to_check() -> None:
    assert len(DRILLS) == 5


@pytest.mark.parametrize("path", SCANNED, ids=lambda path: path.stem)
def test_a_drill_file_neither_writes_nor_reaches_past_the_routes(path: Path) -> None:
    tree = _parse(path)

    assert _writes(tree) == []
    assert _forbidden_imports(tree) == []


def test_lease_expiry_is_the_only_write_the_shared_setup_makes() -> None:
    writers = [name for path in SHARED_SETUP for name in _writing_functions(_parse(path))]

    assert writers == SANCTIONED_WRITERS
    assert all(_forbidden_imports(_parse(path)) == [] for path in SHARED_SETUP)


@pytest.mark.parametrize(
    "source",
    [
        "session.commit()\n",
        "session.add(row)\n",
        "session.execute(text('UPDATE claims SET x = 1'))\n",
        "with engine.begin() as connection: pass\n",
        "connection.exec_driver_sql('DELETE FROM claims')\n",
        "session.bulk_update_mappings(Claim, rows)\n",
        "from orchestrator.services.lifecycle import claims\n",
        "import orchestrator.services.verifier.evidence\n",
        "from orchestrator import services\n",
        "from orchestrator.api import routes\n",
        "from orchestrator.api.routes.work_units import claim\n",
        "from sqlalchemy import update\n",
        "from sqlalchemy.sql import text\n",
    ],
)
def test_the_scan_sees_a_write_or_a_forbidden_import(source: str) -> None:
    tree = ast.parse(source)

    assert _writes(tree) or _forbidden_imports(tree)


def test_the_scan_sees_a_write_in_a_nested_or_async_function() -> None:
    tree = ast.parse(
        "class Helper:\n"
        "    def reset(self, session):\n"
        "        session.commit()\n"
        "async def later(session):\n"
        "    await session.flush()\n"
    )

    assert _writing_functions(tree) == ["later", "reset"]


def test_the_scan_passes_a_read() -> None:
    tree = ast.parse(
        "from sqlalchemy import Engine, select\n"
        "from orchestrator.persistence.models import Claim\n"
        "rows = session.scalars(select(Claim))\n"
        "client.post('/api/v1/x')\n"
    )

    assert (_writes(tree), _forbidden_imports(tree)) == ([], [])
