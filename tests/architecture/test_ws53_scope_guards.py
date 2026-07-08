import ast
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.api.routes import router as api_router

OBSERVATION_SERVICE = Path("src/orchestrator/services/deployment_observations.py")
ROUTES = Path("src/orchestrator/api/routes.py")


def _called_names(function: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_ws53_observation_routes_do_not_call_lifecycle_or_worker_mutators() -> None:
    route_functions = {
        route.endpoint.__name__
        for route in api_router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1/release-artifacts/")
        and route.path.endswith("/deployment-observations")
    }
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
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
    matches: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in route_functions:
            calls = _called_names(node)
            matches.extend(f"{node.name}:{name}" for name in sorted(calls & forbidden_calls))

    assert not matches


def test_ws53_observation_service_does_not_merge_deploy_or_call_external_tools() -> None:
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
        "orchestrator_dispatch_enabled=true",
        "orchestrator_github_dispatch_token",
    )
    matches = [value for value in forbidden if value in source]

    assert not matches


def test_ws53_observation_service_stores_no_secret_literal_shapes() -> None:
    source = OBSERVATION_SERVICE.read_text(encoding="utf-8")
    forbidden = (
        "Authorization: Bearer ",
        "BWS_ACCESS_TOKEN=",
        "ORCHESTRATOR_M2M_CREDENTIALS={",
    )
    matches = [value for value in forbidden if value in source]

    assert not matches
