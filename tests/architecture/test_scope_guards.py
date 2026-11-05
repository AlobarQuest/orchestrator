import ast
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Route

from orchestrator.api.health import router as health_router
from orchestrator.main import API_ROUTERS, create_app
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


def test_the_migration_runbook_carries_the_measured_production_order() -> None:
    """The runbook once required a separate infrastructure-only session and said the image does
    nothing to the database. The second half was the dangerous one to lose: the image does NOT
    self-migrate, so a runbook that stops saying so invites a swap onto an old schema."""
    migrations = Path("docs/operations/migrations.md").read_text()
    local = Path("docs/operations/local-development.md").read_text()

    assert "does not migrate itself" in migrations
    assert "build, then migrate, then swap" in migrations
    assert "--network coolify" in migrations
    assert ".venv/bin/alembic current; .venv/bin/alembic heads" in migrations
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
        # ADR-0006 amendment 1: SYSTEM stages, and only the /review confirm below registers.
        "/api/v1/staged-intakes",
        "/api/v1/member-proposals",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/decomposition-proposals/{proposal_id}/require-revision",
        "/api/v1/work-units/{unit_id}/claim",
        "/api/v1/work-units/{unit_id}/renew",
        "/api/v1/work-units/{unit_id}/reclaim-expired-claim",
        # ADR-0055 amendment 2: SYSTEM releases a lapsed claim with no new grant.
        "/api/v1/work-units/{unit_id}/release-expired-claim",
        "/api/v1/work-units/{unit_id}/preflight",
        "/api/v1/work-units/{unit_id}/dispatch",
        # ADR-0020: the factory lands its own pull request. SYSTEM-only, one caller (whoever
        # drives verification, immediately after), no scheduler.
        "/api/v1/work-units/{unit_id}/pr-merge",
        # ADR-0019 Increment 5b: the orchestrator lands a pull request that has no work
        # unit, into a repository where landing changes something already serving.
        "/api/v1/estate-pr-merge",
        # ADR-0019 Increment 6: the lane brings a pull request's head up to date with the base
        # it has itself moved. The lane's second act, and much the smaller: it writes to a topic
        # branch that nothing serves, and only where being behind is the sole remaining obstacle.
        "/api/v1/estate-pr-branch-update",
        # ADR-0038 part 2: the orchestrator lands a pull request the update bot opened into a
        # repository where landing changes NOTHING already serving -- the population the removed
        # GitHub-native cascade used to land. No change record, no change window, no pace.
        "/api/v1/inert-pr-merge",
        # ADR-0038 part 2: the same lane's second act. It clears the staleness the lane's own
        # landings create, which is what makes requiring freshness survivable at all.
        "/api/v1/inert-pr-branch-update",
        # Bridge layer Part 4: either Dependabot lane closes, with a comment, an update-bot pull
        # request its own re-composed admission refuses for a hold. SYSTEM-only, like the landings.
        "/api/v1/held-pr-close",
        "/api/v1/landing-revert",
        "/api/v1/landing-repoint",
        "/api/v1/landing-pinned",
        "/api/v1/landing-restored",
        "/api/v1/work-units/{unit_id}/verify",
        "/api/v1/work-units/{unit_id}/verifier-evidence/named-check",
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
        # WS-P2.8: mints the work units whose package-declared follow-up reviews have come due.
        # SYSTEM-only, externally invoked; a database read plus an append-only write, with no
        # outbound call and no loop.
        "/api/v1/follow-ups/mint",
        "/api/v1/work-units/{unit_id}/commands/{command}",
        "/api/v1/work-units/{unit_id}/approvals",
        "/api/v1/work-units/{unit_id}/adjudications",
        "/api/v1/work-units/{unit_id}/retry-authorization",
        "/api/v1/work-units/{unit_id}/dependencies",
        "/api/v1/dependencies/{dependency_id}/resolve",
        "/api/v1/work-units/{unit_id}/evidence",
        # ADR-0006: package intake is a human gate, so its only production-reachable surface is
        # a /review form. The /api route it delegates to is human-only and machine-only-routed.
        "/review/intakes",
        "/review/staged-intakes/{staged_id}/confirm",
        "/review/staged-intakes/{staged_id}/withdraw",
        "/review/member-proposals/{proposal_id}/confirm",
        "/review/member-proposals/{proposal_id}/withdraw",
        "/review/units/{unit_id}/approval",
        "/review/units/{unit_id}/authority-approval",
        "/review/units/{unit_id}/review",
        "/review/units/{unit_id}/cancel",
        "/review/units/{unit_id}/retry",
        "/review/units/{unit_id}/adjudication",
        "/review/reconciliation/conditions/{condition_id}/resolution",
        # ADR-0055 decision 3: Devon resolves a human-act dependency with its fingerprints.
        "/review/dependencies/{dependency_id}/resolution",
        "/review/decomposition-proposals/{proposal_id}/approve",
        "/review/decomposition-proposals/{proposal_id}/reject",
        "/review/decomposition-proposals/{proposal_id}/require-revision",
        "/review/decomposition-proposals/{proposal_id}/supersede",
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


def _domain_routes(
    routers: tuple[APIRouter, ...] = (*API_ROUTERS, health_router),
) -> list[APIRoute]:
    """The domain routers' own routes, so a route with `include_in_schema=False` is seen too."""
    return [route for router in routers for route in router.routes if isinstance(route, APIRoute)]


def _observed_gets(domain_routes: list[APIRoute]) -> set[str]:
    paths = create_app().openapi()["paths"]
    observed = {
        path
        for path, operations in paths.items()
        if "get" in operations and path.startswith(("/api/v1/", "/health/"))
    }
    observed.update(
        route.path
        for route in (*web_router.routes, *domain_routes)
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    )
    return observed


def test_production_get_route_inventory_is_explicit() -> None:
    """The POST inventory has always been pinned; the GET surface was not.

    WS-P2.1 adds read surfaces that enumerate failures and divergence, so a new GET must be added
    here DELIBERATELY -- the same discipline the POST set has always had.
    """
    observed = _observed_gets(_domain_routes())

    assert observed == {
        "/api/v1/change-records/{change_record_id}/work",
        "/api/v1/consistency-check",
        "/api/v1/dead-letter",
        "/api/v1/execution-status",
        "/api/v1/in-flight-units",
        "/api/v1/decomposition-proposals/{proposal_id}",
        "/api/v1/factory-policy",
        # ADR-0055 amendment 9: the machine identities the process loaded, readable only by the
        # rotation executor, which rewrites the whole credentials setting when it rotates one.
        "/api/v1/m2m-identities",
        "/api/v1/members",
        "/api/v1/held-credentials",
        "/api/v1/observations",
        "/api/v1/package-intakes/{revision_id}",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/revisions/{revision_id}/evidence-pack",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/slo-report",
        "/api/v1/status-ledger",
        "/api/v1/traceability",
        "/api/v1/work-units/{unit_id}/context-snapshots",
        "/api/v1/work-units/{unit_id}/evidence",
        "/api/v1/work-units/{unit_id}/evidence-pack",
        "/api/v1/work-units/{unit_id}/evidence-pack/markdown",
        "/api/v1/work-units/{unit_id}",
        "/api/v1/work-units/{unit_id}/history",
        # ADR-0020 Increment 4a: the composed, REPORT-ONLY answer to whether the factory may land
        # this unit's pull request. A GET because it acts on nothing; it exists so the answer can
        # be read against real completed units before anything obeys it.
        "/api/v1/work-units/{unit_id}/pr-merge-admission",
        # ADR-0019 Increment 5b: report-only, and the surface that makes a night which
        # lands nothing legible -- every held pull request names the condition it misses.
        "/api/v1/estate-pr-merge-admission",
        # ADR-0038 part 2: report-only, and the surface a caller enumerating the inert
        # population reads before it asks for anything. It also carries the branch-update
        # permission, which is what the freshening pass acts on.
        "/api/v1/inert-pr-merge-admission",
        "/api/v1/landing-unknown-outcomes",
        "/api/v1/landing-watch",
        # Landing Part 3b: the reverts the SDS opened, from its own records, readable by the
        # observer so the change producer can recognise a revert pull request. Reads no App.
        "/api/v1/opened-reverts",
        "/api/v1/work-units/{unit_id}/readiness",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        # ADR-0030: which completed units a machine-local working copy could bind a release
        # artifact for. Read-only, and it asserts nothing about any machine -- it reports the
        # orchestrator's half of an answer whose other half only the operator machine has.
        "/api/v1/machine-activation-candidates",
        "/api/v1/work-units/{unit_id}/runner-brief",
        # The two probes; liveness and readiness. They sit outside /api/v1.
        "/health/live",
        "/health/ready",
        # The /review pages, read from the web router (not in the schema): each renders state a
        # person decides on and acts on nothing.
        "/review",
        "/review/units/{unit_id}",
        "/review/intakes/new",
        "/review/intakes/{revision_id}",
        "/review/staged-intakes/{staged_id}",
        "/review/member-proposals/{proposal_id}",
        "/review/decomposition-proposals/{proposal_id}",
        "/review/units/{unit_id}/evidence-pack",
        "/review/revisions/{revision_id}/evidence-pack",
    }


SERVED_METHODS = frozenset({"GET", "HEAD", "POST"})


def _served_methods(
    app: FastAPI, web_routes: list, domain_routes: list[APIRoute] | None = None
) -> dict[str, set[str]]:
    """Every method the application answers, by path.

    Never walks `app.routes`: under the pinned FastAPI it holds nested `_IncludedRouter` objects.
    The schema carries the domain routes and `domain_routes` (the routers' own, default all of them)
    the ones hidden from it, `web_routes` the `/review` pages (schema-excluded), and
    the four documentation routes are the plain `Route`s at the top of the router, found by the
    URLs the application reports for them.
    """
    served: dict[str, set[str]] = {}
    for path, operations in app.openapi()["paths"].items():
        served.setdefault(path, set()).update(method.upper() for method in operations)
    for route in [*web_routes, *(_domain_routes() if domain_routes is None else domain_routes)]:
        if isinstance(route, APIRoute):
            served.setdefault(route.path, set()).update(route.methods or set())
    documentation = {
        app.openapi_url,
        app.docs_url,
        app.swagger_ui_oauth2_redirect_url,
        app.redoc_url,
    }
    found = {
        route.path: set(route.methods or set())
        for route in app.router.routes
        if type(route) is Route and route.path in documentation
    }
    assert set(found) == documentation, "a documentation route is missing or no longer a Route"
    for path, methods in found.items():
        served.setdefault(path, set()).update(methods)
    return served


def _unserved(served: dict[str, set[str]]) -> dict[str, set[str]]:
    return {
        path: methods - SERVED_METHODS
        for path, methods in served.items()
        if methods - SERVED_METHODS
    }


def test_only_get_head_and_post_are_served() -> None:
    assert _unserved(_served_methods(create_app(), list(web_router.routes))) == {}


def test_the_method_guard_fails_on_a_put_route_on_the_web_router_or_the_app() -> None:
    app = create_app()
    copy = APIRouter()
    copy.routes.extend(web_router.routes)
    copy.add_api_route("/review/throwaway", lambda: None, methods=["PUT"])
    assert _unserved(_served_methods(app, list(copy.routes))) == {"/review/throwaway": {"PUT"}}

    app.add_api_route("/api/v1/throwaway", lambda: None, methods=["PUT"])
    assert _unserved(_served_methods(app, list(web_router.routes))) == {
        "/api/v1/throwaway": {"PUT"}
    }


def test_schema_hidden_domain_routes_are_seen_by_the_method_guard_and_the_get_inventory() -> None:
    hidden = APIRouter()
    hidden.add_api_route("/api/v1/hidden", lambda: None, methods=["PUT"], include_in_schema=False)
    hidden.add_api_route(
        "/api/v1/hidden-read", lambda: None, methods=["GET"], include_in_schema=False
    )
    routes = _domain_routes((*API_ROUTERS, health_router, hidden))

    assert _unserved(_served_methods(create_app(), list(web_router.routes), routes)) == {
        "/api/v1/hidden": {"PUT"}
    }
    assert "/api/v1/hidden-read" in _observed_gets(routes)
    assert "/api/v1/hidden-read" not in _observed_gets(_domain_routes())
