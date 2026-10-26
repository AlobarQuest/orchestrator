"""Who may write the SDS member tables, and through which routes.

Membership is explicit: no lane enrolls a repository. Only a human confirming a proposal on
`/review` writes the member tables, and only `proposals.py` holds that code. The role each route
requires is pinned in `WRITE_ROUTES` and proved behaviourally by
`tests/web/test_member_proposals.py::test_each_write_route_refuses_every_other_role`.
"""

import ast
import inspect
from collections.abc import Callable
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.main import API_ROUTERS
from orchestrator.web import router as web_router

SRC = Path(__file__).resolve().parents[2] / "src" / "orchestrator"

WRITE_MODELS = frozenset({"SdsMember", "SdsMemberGrant", "SdsMemberApp"})
PROPOSAL_MODEL = "SdsMemberProposal"
ALL_MODELS = WRITE_MODELS | {PROPOSAL_MODEL}

# Reads happen in `proposals.py` and `members.py`; `models.py` defines them. `changes.py` is the
# payload vocabulary and names none of them, so it is not listed.
MAY_NAME = frozenset(
    {
        "persistence/models.py",
        "services/membership/proposals.py",
        "services/membership/members.py",
    }
)
MAY_WRITE = frozenset({"services/membership/proposals.py"})

WRITE_FUNCTIONS = frozenset(
    {"stage_member_proposal", "confirm_member_proposal", "withdraw_member_proposal"}
)
# The route -> the one role it requires. The table is the contract; the web test hits each route
# with every other role.
WRITE_ROUTES = {
    "/api/v1/member-proposals": "SYSTEM",
    "/review/member-proposals/{proposal_id}/confirm": "HUMAN",
    "/review/member-proposals/{proposal_id}/withdraw": "HUMAN",
}

# Member table names: raw SQL naming one is a write path the model checks cannot see.
TABLE_NAMES = frozenset(
    {"sds_members", "sds_member_grants", "sds_member_apps", "sds_member_proposals"}
)
# Where the table names may appear as string constants: the model that defines them.
MAY_NAME_TABLES = frozenset({"persistence/models.py"})
CORE_WRITES = frozenset({"update", "insert", "delete", "setattr"})


def _modules() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text())
        for path in sorted(SRC.rglob("*.py"))
    }


def _names(tree: ast.AST, wanted: frozenset[str]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in wanted:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in wanted:
            found.add(node.attr)
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in wanted:
            found.add(node.name.split(".")[-1])
        elif isinstance(node, ast.Constant) and node.value in wanted:
            found.add(node.value)  # a string annotation or mapper reference
    return found


def _callee(call: ast.Call) -> str | None:
    """The called name, whether `update(...)` or `sa.update(...)`."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _writes(tree: ast.AST) -> list[str]:
    """Constructions of, `session.add` of, and attribute assignments on the write models."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in WRITE_MODELS:
                problems.append(f"line {node.lineno}: constructs {node.func.id}")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            # An aliased import (`update as sa_update`) would hide the call from `_callee`.
            problems.extend(
                f"line {node.lineno}: imports {a.name}" for a in node.names if a.name in CORE_WRITES
            )
        if isinstance(node, ast.Call):
            callee = _callee(node)
            if callee in CORE_WRITES:
                problems.append(f"line {node.lineno}: calls {callee}(...)")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"add", "add_all", "merge"} and node.args:
                problems.append(f"line {node.lineno}: session.{node.func.attr}(...)")
    return problems


def _assigned_attributes(tree: ast.AST) -> list[str]:
    targets: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets.append(node.target)
    return [f"line {t.lineno}: assigns .{t.attr}" for t in targets if isinstance(t, ast.Attribute)]


def _table_strings(tree: ast.AST) -> set[str]:
    return {
        word
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for word in TABLE_NAMES
        if word in node.value
    }


def _violations(modules: dict[str, ast.Module]) -> list[str]:
    problems: list[str] = []
    for relative, tree in modules.items():
        tables = _table_strings(tree)
        if tables and relative not in MAY_NAME_TABLES:
            problems.append(f"{relative} names tables {sorted(tables)} in a string")
        named = _names(tree, ALL_MODELS)
        if not named or relative in {"persistence/models.py"}:
            continue
        if relative not in MAY_NAME:
            problems.append(f"{relative} names {sorted(named)}")
        elif relative not in MAY_WRITE:
            # members.py reads: it may not construct, add to a session, or assign attributes.
            problems.extend(f"{relative} {p}" for p in _writes(tree) + _assigned_attributes(tree))
    return problems


def test_only_the_proposal_service_writes_member_tables() -> None:
    assert _violations(_modules()) == []


def test_the_writers_guard_sees_a_stray_write() -> None:
    stray = ast.parse(
        "from orchestrator.persistence.models import SdsMember\n"
        "def f(session):\n"
        "    session.add(SdsMember(repository='a/b'))\n"
    )
    assert _violations({"services/other.py": stray}) == ["services/other.py names ['SdsMember']"]
    reader = ast.parse(
        "from orchestrator.persistence.models import SdsMember\n"
        "def f(session, m):\n"
        "    m.state = 'member'\n"
        "    session.add(SdsMember(repository='a/b'))\n"
    )
    assert len(_violations({"services/membership/members.py": reader})) == 3


def test_the_writers_guard_sees_core_statements_and_raw_sql() -> None:
    core = ast.parse(
        "import sqlalchemy as sa\n"
        "from orchestrator.persistence.models import SdsMember\n"
        "def f(session):\n"
        "    session.execute(update(SdsMember).values(state='x'))\n"
        "    session.execute(sa.delete(SdsMember))\n"
        "    session.execute(sa.insert(SdsMember))\n"
        "    setattr(SdsMember, 'state', 'x')\n"
    )
    found = _violations({"services/membership/members.py": core})
    assert len(found) == 4, found
    aliased = ast.parse(
        "from sqlalchemy import update as sa_update\n"
        "from orchestrator.persistence.models import SdsMember\n"
    )
    assert _violations({"services/membership/members.py": aliased}) == [
        "services/membership/members.py line 1: imports update"
    ]
    raw = ast.parse("from sqlalchemy import text\nQ = text('UPDATE sds_members SET state = 1')\n")
    assert _violations({"services/unrelated.py": raw}) == [
        "services/unrelated.py names tables ['sds_members'] in a string"
    ]
    assert _violations({"persistence/models.py": raw}) == []


def _calls(node: ast.AST) -> set[str]:
    return {
        name for call in ast.walk(node) if isinstance(call, ast.Call) if (name := _callee(call))
    }


def _reaches(module: ast.Module, entry: str) -> bool:
    """Whether `entry` calls a write function, directly or through same-module helpers."""
    local = {
        node.name: node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    seen: set[str] = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in seen or name not in local:
            continue
        seen.add(name)
        called = _calls(local[name])
        if called & WRITE_FUNCTIONS:
            return True
        pending.extend(called & local.keys())
    return False


def _reaches_a_write(function: Callable[..., object]) -> bool:
    module = inspect.getmodule(function)
    assert module is not None
    return _reaches(ast.parse(inspect.getsource(module)), function.__name__)


def test_the_route_walk_follows_a_local_helper() -> None:
    module = ast.parse(
        "def helper(session):\n"
        "    return stage_member_proposal(session)\n"
        "def middle(session):\n"
        "    return helper(session)\n"
        "def endpoint(session):\n"
        "    return middle(session)\n"
        "def unrelated(session):\n"
        "    return list_members(session)\n"
        "def loop_a():\n"
        "    return loop_b()\n"
        "def loop_b():\n"
        "    return loop_a()\n"
    )
    assert _reaches(module, "endpoint")
    assert not _reaches(module, "unrelated")
    assert not _reaches(module, "loop_a")  # a cycle ends


def test_membership_write_routes_are_exactly_these() -> None:
    # `include_router` copies routes, so the routers are walked directly; each route's path
    # already carries its router's prefix.
    routes = [
        route
        for router in (*API_ROUTERS, web_router)
        for route in router.routes
        if isinstance(route, APIRoute)
    ]
    assert len(routes) > 50  # the walk found the app, not an empty shell
    reaching = {route.path for route in routes if _reaches_a_write(route.endpoint)}

    assert reaching == set(WRITE_ROUTES)
