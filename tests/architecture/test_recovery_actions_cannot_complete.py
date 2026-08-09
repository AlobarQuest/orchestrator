"""WS-P2.1 Task 11: recovery actions can never declare completion, waive, or merge (AC-006).

STATING THIS CORRECTLY MATTERS. The tempting proof -- "no WORKER edge reaches COMPLETED" -- is a
NON-SEQUITUR for these actions: retry and cancel are HUMAN-surfaced, and HUMAN_EDGES *does*
contain SUBMITTED/VERIFYING/AWAITING_REVIEW -> COMPLETED. Proving something true about workers
says nothing about a human-surfaced recovery action.

The real guarantee is two-fold:
  1. every recovery entry point HARDCODES its target state (READY or CANCELLED, never COMPLETED);
  2. every transition into COMPLETED -- by ANY role -- is gated by `completion_satisfied`.
"""

import ast
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from orchestrator.api.routes import router as api_router
from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition
from orchestrator.web import router as web_router

RECOVERY_ENTRY_POINTS = (
    ("src/orchestrator/services/claims.py", "requeue_unit"),
    ("src/orchestrator/services/claims.py", "authorize_retry"),
    ("src/orchestrator/services/claims.py", "recover_expired_claim"),
)
ALLOWED_RECOVERY_TARGETS = {"READY", "CANCELLED"}


def _state_members(path: str, function: str) -> set[str]:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "WorkUnitState"
            }
    raise AssertionError(f"{function} not found in {path}")


@pytest.mark.parametrize(("path", "function"), RECOVERY_ENTRY_POINTS)
def test_each_recovery_entry_point_hardcodes_a_target_that_is_not_completed(
    path: str, function: str
) -> None:
    members = _state_members(path, function)

    assert "COMPLETED" not in members
    assert members & ALLOWED_RECOVERY_TARGETS


def test_every_transition_into_completed_is_gated_by_completion_satisfied() -> None:
    """The guarantee that actually holds for EVERY role, human included."""
    completing = {edge for edge in LEGAL_EDGES if edge[1] is WorkUnitState.COMPLETED}
    assert completing

    for source, target in completing:
        for role in EDGE_ROLES[(source, target)]:
            with pytest.raises(DomainError) as error:
                authorize_transition(
                    source, target, role, TransitionGuards(completion_satisfied=False)
                )
            assert error.value.code == "completion_incomplete"


def test_no_recovery_path_grants_a_waiver() -> None:
    source = Path("src/orchestrator/services/claims.py").read_text()
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "waived" not in literals


# ADR-0020's named exception, in this guard -- the EIGHTH place the merge prohibition lives, and
# one that neither the ADR's inventory of four nor the workstream's revised inventory of six names.
#
# **Keyed on (METHOD, path), not on path alone, and that is the whole narrowness.** A set of paths
# excuses every verb served at them, so an ACTING endpoint hung off a path a reporting one already
# made respectable would inherit its excuse silently -- which is precisely the shape the acting
# increment would otherwise take. Adding a verb is adding an entry, in the open, with a reason.
#
# What this cannot enforce, and should not pretend to: whether a landing changes something already
# serving is a fact about the target repository, decided at runtime by asking the estate, not
# something readable from the spelling of a route. This guard's job is that no endpoint can NAME a
# merge without somebody deciding it may.
MERGE_NAMING_ROUTES = {
    # Report-only. It answers whether the factory MAY land a unit's pull request; it holds no
    # credential, imports no client, and nothing it returns causes anything to happen.
    ("GET", "/api/v1/work-units/{unit_id}/pr-merge-admission"),
}


def _routed_endpoints() -> set[tuple[str, str]]:
    """Every (method, path) the application serves, read STRUCTURALLY from the routers.

    Deliberately not `openapi()["paths"]`, which this guard used until ADR-0020 gave it an
    allowlist. That surface is SCHEMA-visible only, so a route declared `include_in_schema=False`
    is invisible to it -- demonstrated by registering one -- and it collapses every verb onto one
    key. Both blind spots are survivable under a blanket prohibition and are not under an
    exemption, because both let an endpoint reach the excused set without being named.

    Read from the two routers rather than from the assembled application: composition wraps them
    in an internal type whose nested routes are not reachable by the obvious attribute, so walking
    the app finds a fraction of the surface and the guard passes on what it cannot see. The route
    inventories in `test_scope_guards.py` already read the `/review` router this way.
    """
    return {
        (method, route.path)
        for route in [*api_router.routes, *web_router.routes]
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
    }


def _merge_naming_endpoints() -> set[tuple[str, str]]:
    return {(method, path) for method, path in _routed_endpoints() if "merge" in path}


def test_nothing_in_the_system_can_merge() -> None:
    assert not any("merge" in state.value for state in WorkUnitState)

    named = _merge_naming_endpoints()

    assert named <= MERGE_NAMING_ROUTES, (
        f"these endpoints name a merge and are not the bounded exception ADR-0020 allows: "
        f"{sorted(named - MERGE_NAMING_ROUTES)}. Merging was Devon's gate; an endpoint that names "
        "one must be added here openly, with a reason -- never by rewording the path, and never "
        "by hanging a new verb off a path that was excused for a different one."
    )


def test_the_merge_naming_exception_names_only_endpoints_that_need_it() -> None:
    """The same rot check the other merge exemptions carry: an exemption nobody needs is an
    exemption nobody is watching."""
    named = _merge_naming_endpoints()

    assert MERGE_NAMING_ROUTES <= named, (
        f"these exempt endpoints are not served, or no longer name a merge: "
        f"{sorted(MERGE_NAMING_ROUTES - named)}"
    )
