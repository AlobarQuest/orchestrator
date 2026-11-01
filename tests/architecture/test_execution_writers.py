"""Who may write a lease, a unit's version and a claim's release, and who may dispatch.

Four rules in CLAUDE.md name a closed set of callers. Each set is pinned here by walking the source,
so a new caller fails this file and has to be read against the rule before it joins the table:

* A lease is granted or extended in exactly three places, each sized by `claim_lease`. A rule added
  to `claim_unit` alone misses the other two.
* `work_units.version` moves only on a state transition, in three places. A row that changes without
  a transition must not spend a caller's `expected_version`.
* `release_claim` is the only code that ends a claim, and each caller's terminal reason is listed.
  Claim predicates rest on that reason being one of these.
* `dispatch_work_unit` has one caller, the route. Nothing else starts a run.

A reference is any use of the name: a call, an import, a bare assignment to a local. So an alias
fails as surely as a call does. Dynamic access (`getattr` with a built name) escapes the walk; every
write form below is one the source could plausibly use, the same scope as `test_membership_writers`.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.main import API_ROUTERS

SRC = Path(__file__).resolve().parents[2] / "src" / "orchestrator"

CLAIMS = "services/lifecycle/claims.py"
LIFECYCLE = "services/lifecycle/lifecycle.py"
EVIDENCE = "services/verifier/evidence.py"
DISPATCH = "services/execution/dispatch.py"
RELEASE = "services/lifecycle/claim_release.py"
ROUTES = "api/routes/execution.py"
IMPORT = "<import>"

LEASE_GRANTS = {
    (CLAIMS, "claim_unit"),
    (CLAIMS, "renew_claim"),
    (CLAIMS, "_acquire_reclaimed_claim"),
}
VERSION_WRITERS = {
    (LIFECYCLE, "_perform_transition"),
    (CLAIMS, "_transition"),
    (EVIDENCE, "_system_fail_without_new_attempt"),
}
# (module, function, the terminal reason it writes). The two `lifecycle` rows are worker failure and
# human cancel; the `lease_expired` rows are reclaim, release and expiry recovery.
RELEASE_CALLS = {
    (CLAIMS, "_perform_reclaim", "lease_expired"),
    (CLAIMS, "release_expired_claim", "lease_expired"),
    (LIFECYCLE, "_apply_claim_transition_effects", "work_unit_failed"),
    (LIFECYCLE, "_apply_claim_transition_effects", "work_unit_cancelled"),
    (EVIDENCE, "recover_evidence", "lease_expired"),
}
DISPATCH_ROUTE = ("POST", "/api/v1/work-units/{unit_id}/dispatch")

RAW_UPDATE = re.compile(r"update\s+(work_units|claims)\b", re.IGNORECASE)


def _scopes(tree: ast.AST, scope: str = "") -> Iterator[tuple[ast.AST, str]]:
    """Every node with the function that encloses it, dotted when nested; "" at module level."""
    for child in ast.iter_child_nodes(tree):
        inner = scope
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            inner = f"{scope}.{child.name}" if scope else child.name
        yield child, inner
        yield from _scopes(child, inner)


def _nodes() -> Iterator[tuple[str, ast.AST, str]]:
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        for node, scope in _scopes(ast.parse(path.read_text())):
            yield module, node, scope


def _references(name: str) -> set[tuple[str, str]]:
    """Each place `name` is used, the import that binds it included."""
    found: set[tuple[str, str]] = set()
    for module, node, scope in _nodes():
        if isinstance(node, ast.alias) and node.name.split(".")[-1] == name:
            found.add((module, IMPORT))
        elif isinstance(node, ast.Name) and node.id == name:
            found.add((module, scope))
        elif isinstance(node, ast.Attribute) and node.attr == name:
            found.add((module, scope))
    return found


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _writes(attribute: str, model: str) -> set[tuple[str, str]]:
    """Where `attribute` is written: assignment, `setattr`, bulk `.values(...)` or `model(...)`."""
    found: set[tuple[str, str]] = set()
    for module, node, scope in _nodes():
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Attribute) and t.attr == attribute for t in targets):
            found.add((module, scope))
        if not isinstance(node, ast.Call):
            continue
        callee = _callee(node)
        if callee in {"values", model} and any(k.arg == attribute for k in node.keywords):
            found.add((module, scope))
        if callee == "setattr" and len(node.args) > 1:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value == attribute:
                found.add((module, scope))
    return found


def _unsized_lease_writes() -> set[tuple[str, str]]:
    """A write of `lease_expires_at` whose value isn't computed through `claim_lease`."""
    found: set[tuple[str, str]] = set()
    for module, node, scope in _nodes():
        values: list[ast.expr] = []
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "lease_expires_at" for t in node.targets
        ):
            values = [node.value]
        elif isinstance(node, ast.Call) and _callee(node) in {"values", "Claim"}:
            values = [k.value for k in node.keywords if k.arg == "lease_expires_at"]
        for value in values:
            names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            if "claim_lease" not in names:
                found.add((module, scope))
    return found


def _release_calls() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for module, node, scope in _nodes():
        if isinstance(node, ast.Call) and _callee(node) == "release_claim":
            reasons = [k.value for k in node.keywords if k.arg == "terminal_reason"]
            reason = reasons[0] if reasons else None
            value = reason.value if isinstance(reason, ast.Constant) else None
            found.add((module, scope, value if isinstance(value, str) else "<computed>"))
    return found


def test_a_lease_is_granted_only_in_its_three_places() -> None:
    assert _references("claim_lease") == LEASE_GRANTS | {(CLAIMS, IMPORT)}
    assert _writes("lease_expires_at", "Claim") == LEASE_GRANTS
    assert _unsized_lease_writes() == set()


def test_a_reclaim_is_the_only_way_to_the_third_grant() -> None:
    """The third grant sits in a helper, so the rule's `reclaim_expired_claim` is two hops away."""
    assert _references("_acquire_reclaimed_claim") == {(CLAIMS, "_perform_reclaim")}
    assert _references("_perform_reclaim") == {(CLAIMS, "reclaim_expired_claim")}


def test_a_units_version_has_three_writers() -> None:
    assert _writes("version", "WorkUnit") == VERSION_WRITERS


def test_no_raw_sql_updates_a_unit_or_a_claim() -> None:
    """A raw statement would write either column past the two tests above."""
    raw = {
        (module, node.lineno)
        for module, node, _ in _nodes()
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and RAW_UPDATE.search(node.value)
    }
    assert raw == set()


def test_only_release_claim_ends_a_claim() -> None:
    assert _writes("released_at", "Claim") == {(RELEASE, "release_claim")}
    assert _writes("terminal_reason", "Claim") == {(RELEASE, "release_claim")}


def test_release_claim_has_its_listed_callers() -> None:
    assert _release_calls() == RELEASE_CALLS
    assert _references("release_claim") == {(m, f) for m, f, _ in RELEASE_CALLS} | {
        (CLAIMS, IMPORT),
        (LIFECYCLE, IMPORT),
        (EVIDENCE, IMPORT),
    }


def test_dispatch_has_one_caller_and_it_is_the_route() -> None:
    assert _references("dispatch_work_unit") == {(ROUTES, IMPORT), (ROUTES, "dispatch_route")}
    assert _references("_dispatch_work_unit") == {(DISPATCH, "dispatch_work_unit")}
    assert _references("dispatch_workflow") == {(DISPATCH, "_dispatch_work_unit")}


def test_the_dispatch_route_is_the_one_the_rule_names() -> None:
    served = {
        (method, route.path)
        for router in API_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute) and route.endpoint.__name__ == "dispatch_route"
        for method in route.methods or ()
    }
    assert served == {DISPATCH_ROUTE}
