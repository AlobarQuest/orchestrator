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
        "/api/v1/work-units/{unit_id}/recover-expired-claim",
        "/api/v1/work-units/{unit_id}/preflight",
        "/api/v1/work-units/{unit_id}/dispatch",
        # ADR-0020: the factory lands its own pull request. SYSTEM-only, one caller (whoever
        # drives verification, immediately after), no scheduler.
        "/api/v1/work-units/{unit_id}/pr-merge",
        # ADR-0019 Increment 5b: the orchestrator lands a pull request that has no work
        # unit, into a repository where landing changes something already serving.
        "/api/v1/estate-pr-merge",
        "/api/v1/work-units/{unit_id}/verify",
        "/api/v1/work-units/{unit_id}/verifier-evidence/named-check",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/observations",
        "/api/v1/reconciliation/detect",
        "/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        "/api/v1/work-units/{unit_id}/requeue",
        # WS-P2.1: the worker's report of the PR it opened. This is the orchestrator's
        # EXPECTATION of the pull request -- written by our own side, never derived from an
        # observation, because an expectation written by observed reality can never diverge
        # from it.
        "/api/v1/work-units/{unit_id}/pr-binding",
        # WS-P2.4: the worker's report of realized cost/token actuals for a claimed attempt.
        "/api/v1/work-units/{unit_id}/cost-actuals",
        # WS-P2.7: the tracker-projection adapter's report of the external item a unit is
        # mirrored onto. SYSTEM-written, same reasoning as pr-binding above.
        "/api/v1/work-units/{unit_id}/tracker-binding",
        # WS-P2.7 Inc-2: inbound tracker reconciliation. SYSTEM-only, report-only -- records
        # append-only divergence conditions, never a transition.
        "/api/v1/reconciliation/tracker-detect",
        # WS-P2.8: mints the work units whose package-declared follow-up reviews have come due.
        # SYSTEM-only, externally invoked; a database read plus an append-only write, with no
        # outbound call and no loop.
        "/api/v1/follow-ups/mint",
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
        # ADR-0006: package intake is a human gate, so its only production-reachable surface is
        # a /review form. The /api route it delegates to is human-only and machine-only-routed.
        "/review/intakes",
        "/review/units/{unit_id}/approval",
        "/review/units/{unit_id}/authority-approval",
        "/review/units/{unit_id}/review",
        "/review/units/{unit_id}/cancel",
        "/review/units/{unit_id}/retry",
        "/review/units/{unit_id}/adjudication",
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


def test_production_get_route_inventory_is_explicit() -> None:
    """The POST inventory has always been pinned; the GET surface was not.

    WS-P2.1 adds read surfaces that enumerate failures and divergence, so a new GET must be added
    here DELIBERATELY -- the same discipline the POST set has always had.
    """
    paths = create_app().openapi()["paths"]
    observed = {
        path
        for path, operations in paths.items()
        if "get" in operations and path.startswith("/api/v1/")
    }

    assert observed == {
        "/api/v1/consistency-check",
        "/api/v1/dead-letter",
        "/api/v1/in-flight-units",
        "/api/v1/decomposition-proposals/{proposal_id}",
        "/api/v1/event-publications",
        "/api/v1/factory-policy",
        "/api/v1/knowledge-promotion-proposals",
        "/api/v1/observations",
        "/api/v1/package-intakes/{revision_id}",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/revisions/{revision_id}/evidence-pack",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/slo-report",
        "/api/v1/status-ledger",
        "/api/v1/traceability",
        "/api/v1/tracker-bindings",
        "/api/v1/work-units/{unit_id}/context-snapshots",
        "/api/v1/work-units/{unit_id}/evidence",
        "/api/v1/work-units/{unit_id}/evidence-pack",
        "/api/v1/work-units/{unit_id}/evidence-pack/markdown",
        "/api/v1/work-units/{unit_id}/history",
        # ADR-0020 Increment 4a: the composed, REPORT-ONLY answer to whether the factory may land
        # this unit's pull request. A GET because it acts on nothing; it exists so the answer can
        # be read against real completed units before anything obeys it.
        "/api/v1/work-units/{unit_id}/pr-merge-admission",
        # ADR-0019 Increment 5b: report-only, and the surface that makes a night which
        # lands nothing legible -- every held pull request names the condition it misses.
        "/api/v1/estate-pr-merge-admission",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        "/api/v1/work-units/{unit_id}/readiness",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        "/api/v1/work-units/{unit_id}/runner-brief",
    }
