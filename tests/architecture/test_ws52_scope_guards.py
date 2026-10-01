import ast
import inspect
import textwrap
from collections.abc import Callable
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.main import API_ROUTERS

RELEASE_SERVICE = Path("src/orchestrator/services/release/release_artifacts.py")
# Every route module, so a file-level scan sees a route whichever domain module it is added to.
ROUTE_SOURCES = sorted(Path("src/orchestrator/api/routes").glob("*.py"))


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


def test_ws52_release_routes_do_not_call_lifecycle_or_worker_mutators() -> None:
    route_functions = [
        _route_function(route.endpoint)
        for router in API_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1/work-units/")
        and route.path.endswith("/release-artifacts")
    ]
    forbidden_calls = {
        "transition_unit",
        "record_adjudication",
        "record_approval",
        "claim_unit",
        "renew_claim",
        "reclaim_expired_claim",
        "authorize_retry",
        "dispatch_work_unit",
    }
    # Without this the guard passes when it finds nothing to scan.
    assert route_functions
    matches = [
        f"{node.name}:{name}"
        for node in route_functions
        for name in sorted(_called_names(node) & forbidden_calls)
    ]

    assert not matches


def test_ws52_release_service_does_not_merge_deploy_dispatch_or_observe_production() -> None:
    source = RELEASE_SERVICE.read_text(encoding="utf-8").lower()
    forbidden = (
        "gh pr merge",
        "git push origin main",
        "coolify",
        "deploy",
        "workflow_dispatch",
        "post-deploy",
        "post_deploy",
        "health/live",
        "health/ready",
    )
    matches = [value for value in forbidden if value in source]

    assert not matches
