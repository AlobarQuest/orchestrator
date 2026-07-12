"""AC-007: the matrix cannot lie.

Gated both ways. Every ingress POST route must have a row (so a new ingress cannot land
uncovered), and every row must name a test that actually exists (so a row cannot claim coverage
that was never written). A coverage matrix nobody checks is a coverage claim, not coverage.
"""

from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from orchestrator.main import create_app
from orchestrator.web import router as web_router
from tests.idempotency.matrix import COVERAGE_MATRIX

# Routes that persist no event/evidence/observation of their own. They are governed by the
# ingress they delegate to, or they are human-gate/render surfaces whose idempotency is the
# underlying service's. Each entry is a deliberate exclusion, not an oversight.
NON_INGRESS_POST_ROUTES = frozenset(
    {
        # Human decision surfaces -- idempotency belongs to the service they call.
        "/api/v1/decomposition-proposals/{proposal_id}/approve",
        "/api/v1/decomposition-proposals/{proposal_id}/reject",
        "/api/v1/decomposition-proposals/{proposal_id}/require-revision",
        "/review/decomposition-proposals/{proposal_id}/approve",
        "/review/decomposition-proposals/{proposal_id}/reject",
        "/review/decomposition-proposals/{proposal_id}/require-revision",
        "/review/units/{unit_id}/approval",
        "/review/units/{unit_id}/cancel",
        "/review/units/{unit_id}/retry",
        "/review/units/{unit_id}/review",
        # Read-model / export plumbing over already-persisted events.
        "/api/v1/event-publications/queue",
        "/api/v1/event-publications/export",
        "/api/v1/event-publications/{publication_id}/retry",
        "/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
        # Derives a context snapshot; carries no independent ingress key.
        "/api/v1/work-units/{unit_id}/preflight",
    }
)


def _post_routes() -> set[str]:
    paths = create_app().openapi()["paths"]
    routes = {path for path, operations in paths.items() if "post" in operations}
    routes.update(
        route.path
        for route in web_router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    )
    return routes


def test_every_ingress_post_route_has_a_matrix_row() -> None:
    """A new ingress cannot land without a duplicate-delivery story."""
    covered = {row.route for row in COVERAGE_MATRIX if row.route is not None}

    uncovered = _post_routes() - covered - NON_INGRESS_POST_ROUTES

    assert uncovered == set()


def test_every_excluded_route_still_exists() -> None:
    """An exclusion for a route that no longer exists is stale cover -- it would silently keep
    excluding a DIFFERENT route if the path were ever reused."""
    stale = NON_INGRESS_POST_ROUTES - _post_routes()

    assert stale == set()


@pytest.mark.parametrize("row", COVERAGE_MATRIX, ids=lambda row: row.ingress)
def test_every_matrix_row_names_a_test_that_exists(row) -> None:
    """A row cannot claim coverage that was never written."""
    file_path, _, test_name = row.test.partition("::")
    source = Path(file_path)

    assert source.exists(), f"{row.ingress}: {file_path} does not exist"
    assert f"def {test_name}(" in source.read_text(), f"{row.ingress}: {test_name} not found"
