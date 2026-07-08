import ast
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.api.routes import router as api_router

SOURCE_ROOT = Path("src/orchestrator")
PUBLICATION_SERVICE = Path("src/orchestrator/services/event_publications.py")


def _source_files() -> tuple[Path, ...]:
    return tuple(sorted(SOURCE_ROOT.rglob("*.py")))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_ws34_adds_no_factory_runner_or_workflow_dispatch_code() -> None:
    forbidden = ("workflow_dispatch", "factory_runner", "github.actions")
    ws42_dispatch_paths = {
        Path("src/orchestrator/services/dispatch.py"),
        Path("src/orchestrator/api/routes.py"),
        Path("src/orchestrator/api/schemas.py"),
        Path("src/orchestrator/config.py"),
    }
    matches = [
        f"{path}:{value}"
        for path in _source_files()
        if path not in ws42_dispatch_paths
        for value in forbidden
        if value in path.read_text(encoding="utf-8").lower()
    ]

    assert not matches


def test_ws34_adds_no_production_deploy_coolify_or_automatic_merge_path() -> None:
    forbidden = ("coolify", "gh pr merge", "git push origin main", "merge_to_main")
    matches = [
        f"{path}:{value}"
        for path in _source_files()
        for value in forbidden
        if value in path.read_text(encoding="utf-8").lower()
    ]

    assert not matches


def test_ws34_publication_service_does_not_import_live_event_store() -> None:
    modules = _imported_modules(PUBLICATION_SERVICE)

    assert "factory_events.store" not in modules
    assert not any(module.startswith("factory_events.store.") for module in modules)


def test_ws34_publication_service_does_not_reference_default_live_store_path() -> None:
    source = PUBLICATION_SERVICE.read_text(encoding="utf-8")

    assert "FACTORY_EVENTS_HOME" not in source
    assert "~/.factory" not in source
    assert "/.factory" not in source
    assert "events.jsonl" not in source


def test_ws34_event_publication_routes_do_not_call_lifecycle_mutators() -> None:
    route_functions = {
        route.endpoint.__name__
        for route in api_router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/event-publications")
    }
    source = Path("src/orchestrator/api/routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {
        "transition_unit",
        "append_evidence",
        "record_adjudication",
        "record_approval",
        "claim_unit",
        "renew_claim",
        "reclaim_expired_claim",
        "authorize_retry",
    }
    matches: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in route_functions:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in forbidden_calls:
                matches.append(f"{node.name}:{child.id}")

    assert not matches
