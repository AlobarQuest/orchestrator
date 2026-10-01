"""Recording a release artifact moves no work unit (kept from ws52 by ADR-0051).

The route records a binding. It must never transition, claim, approve, retry or dispatch a unit;
those acts have their own routes and their own role checks.
"""

import ast
import inspect
import textwrap
from collections.abc import Callable

import pytest
from fastapi.routing import APIRoute

from orchestrator.main import API_ROUTERS


def _route_function(endpoint: Callable[..., object]) -> ast.FunctionDef:
    # The route's OWN source, wherever it lives: reading one named file would miss a matching
    # route added to another domain module and pass without having looked at it.
    node = ast.parse(textwrap.dedent(inspect.getsource(endpoint))).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _called_names(function: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


FORBIDDEN_CALLS = frozenset(
    {
        "transition_unit",
        "record_adjudication",
        "record_approval",
        "claim_unit",
        "renew_claim",
        "reclaim_expired_claim",
        "authorize_retry",
        "dispatch_work_unit",
    }
)


def _mutator_calls(functions: list[ast.FunctionDef]) -> list[str]:
    return [
        f"{node.name}:{name}"
        for node in functions
        for name in sorted(_called_names(node) & FORBIDDEN_CALLS)
    ]


def test_release_artifact_routes_call_no_lifecycle_or_worker_mutator() -> None:
    route_functions = [
        _route_function(route.endpoint)
        for router in API_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1/work-units/")
        and route.path.endswith("/release-artifacts")
    ]
    # Without this the guard passes when it finds nothing to scan.
    assert route_functions
    assert _mutator_calls(route_functions) == []


@pytest.mark.parametrize("call", sorted(FORBIDDEN_CALLS))
def test_a_route_calling_a_mutator_is_reported(call: str) -> None:
    """The control: the real routes call none of these, so only a route that does proves the check
    can fail."""
    source = f"def route(session):\n    return services.{call}(session)\n"
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _mutator_calls([node]) == [f"route:{call}"]
