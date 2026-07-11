import ast
from pathlib import Path

from fastapi.routing import APIRoute

from orchestrator.main import create_app
from orchestrator.web import router as web_router


def test_application_has_no_external_mutation_integrations() -> None:
    forbidden = ("infraops", "linear", "todoist", "github.actions", "factory_events.store")
    imports: set[str] = set()
    for path in Path("src/orchestrator").rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imports.update(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )

    assert not any(any(name.startswith(value) for value in forbidden) for name in imports)


def test_operations_require_separate_infrastructure_change_package() -> None:
    migrations = Path("docs/operations/migrations.md").read_text()
    local = Path("docs/operations/local-development.md").read_text()

    assert "infrastructure-change" in migrations
    assert "Coolify" in migrations
    assert "alembic upgrade head" in local


def test_production_post_route_inventory_is_explicit() -> None:
    paths = create_app().openapi()["paths"]
    observed = {path for path, operations in paths.items() if "post" in operations}
    observed.update(
        route.path
        for route in web_router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    )
    assert observed == {
        "/api/v1/package-intakes",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/decomposition-proposals/{proposal_id}/approve",
        "/api/v1/decomposition-proposals/{proposal_id}/reject",
        "/api/v1/decomposition-proposals/{proposal_id}/require-revision",
        "/api/v1/revisions",
        "/api/v1/revisions/{revision_id}/work-units",
        "/api/v1/work-units/{unit_id}/claim",
        "/api/v1/work-units/{unit_id}/renew",
        "/api/v1/work-units/{unit_id}/reclaim-expired-claim",
        "/api/v1/work-units/{unit_id}/preflight",
        "/api/v1/work-units/{unit_id}/dispatch",
        "/api/v1/work-units/{unit_id}/verify",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/observations",
        "/api/v1/reconciliation/detect",
        "/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        "/api/v1/knowledge-promotion-proposals",
        "/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
        "/api/v1/work-units/{unit_id}/commands/{command}",
        "/api/v1/work-units/{unit_id}/approvals",
        "/api/v1/work-units/{unit_id}/adjudications",
        "/api/v1/work-units/{unit_id}/retry-authorization",
        "/api/v1/work-units/{unit_id}/dependencies",
        "/api/v1/dependencies/{dependency_id}/resolve",
        "/api/v1/work-units/{unit_id}/evidence",
        "/api/v1/event-publications/queue",
        "/api/v1/event-publications/export",
        "/api/v1/event-publications/{publication_id}/retry",
        "/review/units/{unit_id}/approval",
        "/review/units/{unit_id}/review",
        "/review/units/{unit_id}/cancel",
        "/review/units/{unit_id}/retry",
        "/review/reconciliation/conditions/{condition_id}/resolution",
        "/review/decomposition-proposals/{proposal_id}/approve",
        "/review/decomposition-proposals/{proposal_id}/reject",
        "/review/decomposition-proposals/{proposal_id}/require-revision",
    }


def test_application_defines_no_event_publisher_or_dispatch_objects() -> None:
    forbidden = {"eventpublisher", "publisher", "dispatchclient", "productionmutation"}
    identifiers: set[str] = set()
    for path in Path("src/orchestrator").rglob("*.py"):
        tree = ast.parse(path.read_text())
        identifiers.update(node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name))
        identifiers.update(
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )
        identifiers.update(
            node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )

    assert not forbidden.intersection(identifiers)
