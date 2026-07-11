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

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition
from orchestrator.main import create_app
from orchestrator.web import router as web_router

RECOVERY_ENTRY_POINTS = (
    ("src/orchestrator/services/claims.py", "requeue_unit"),
    ("src/orchestrator/services/claims.py", "authorize_retry"),
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


def test_nothing_in_the_system_can_merge() -> None:
    assert not any("merge" in state.value for state in WorkUnitState)

    paths = set(create_app().openapi()["paths"])
    paths.update(route.path for route in web_router.routes if isinstance(route, APIRoute))

    assert not any("merge" in path for path in paths)
