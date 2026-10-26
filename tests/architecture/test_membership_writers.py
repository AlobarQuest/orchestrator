"""Who may write the SDS member tables, and through which routes.

Membership is explicit: no lane enrolls a repository. Only a human confirming a proposal on
`/review` writes the member tables, and only `proposals.py` holds that code. Role refusal itself
(SYSTEM stages, HUMAN decides, every other role is refused) is pinned behaviourally by
`tests/services/test_member_proposals.py` and `tests/web/test_member_proposals.py`.
"""

import ast
import inspect
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
WRITE_ROUTES = frozenset(
    {
        "/api/v1/member-proposals",
        "/review/member-proposals/{proposal_id}/confirm",
        "/review/member-proposals/{proposal_id}/withdraw",
    }
)


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


def _writes(tree: ast.AST) -> list[str]:
    """Constructions of, `session.add` of, and attribute assignments on the write models."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in WRITE_MODELS:
                problems.append(f"line {node.lineno}: constructs {node.func.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"add", "add_all", "merge", "delete"} and node.args:
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


def _violations(modules: dict[str, ast.Module]) -> list[str]:
    problems: list[str] = []
    for relative, tree in modules.items():
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


def _reaches_a_write(function: object) -> bool:
    tree = ast.parse(inspect.getsource(function))  # type: ignore[arg-type]
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in WRITE_FUNCTIONS)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_FUNCTIONS)
        )
        for node in ast.walk(tree)
    )


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

    assert reaching == WRITE_ROUTES
