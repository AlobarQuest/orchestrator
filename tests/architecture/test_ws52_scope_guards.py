import ast
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.api.routes import router as api_router

RELEASE_SERVICE = Path("src/orchestrator/services/release_artifacts.py")
ROUTES = Path("src/orchestrator/api/routes.py")


def _called_names(function: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_ws52_release_routes_do_not_call_lifecycle_or_worker_mutators() -> None:
    route_functions = {
        route.endpoint.__name__
        for route in api_router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1/work-units/")
        and route.path.endswith("/release-artifacts")
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
