import ast
import inspect
import textwrap
from collections.abc import Callable
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.main import API_ROUTERS

OBSERVATION_SERVICE = Path("src/orchestrator/services/release/observations.py")
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


def test_ws61_observation_routes_do_not_call_lifecycle_or_worker_mutators() -> None:
    route_functions = [
        _route_function(route.endpoint)
        for router in API_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/observations"
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
        "verify_work_unit",
        "record_deployment_observation",
        "record_release_artifact",
    }
    # Without this the guard passes when it finds nothing to scan.
    assert route_functions
    matches = [
        f"{node.name}:{name}"
        for node in route_functions
        for name in sorted(_called_names(node) & forbidden_calls)
    ]

    assert not matches


def test_ws61_observation_service_does_not_merge_deploy_or_call_external_tools() -> None:
    source = OBSERVATION_SERVICE.read_text(encoding="utf-8").lower()
    forbidden = (
        "gh pr merge",
        "git push origin main",
        "workflow_dispatch",
        "coolify api",
        "coolify deploy",
        "requests.",
        "httpx.",
        "linear",
        "todoist",
        "brain",
        "create_work_unit",
        "register_approved_unit",
        "transition_unit",
        "record_adjudication",
        "dispatch_work_unit",
        "orchestrator_dispatch_enabled=true",
        "orchestrator_github_app",
    )
    matches = [value for value in forbidden if value in source]

    assert not matches


def test_ws61_observation_files_store_no_secret_literal_shapes() -> None:
    source = OBSERVATION_SERVICE.read_text(encoding="utf-8") + "".join(
        path.read_text(encoding="utf-8") for path in ROUTE_SOURCES
    )
    forbidden = (
        "Authorization: Bearer ",
        "BWS_ACCESS_TOKEN=",
        "ORCHESTRATOR_M2M_CREDENTIALS={",
    )
    matches = [value for value in forbidden if value in source]

    assert not matches
