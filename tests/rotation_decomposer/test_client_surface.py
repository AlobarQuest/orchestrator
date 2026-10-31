"""The lane's surfaces: change-manager's record listing, read; the orchestrator, four reads and the
one proposal write, each anchored."""

from __future__ import annotations

import pytest

from rotation_decomposer.client import change_manager_permits, orchestrator_permits

REVISION = "0c7a484c-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/change-records/139/work"),
        ("GET", f"/api/v1/package-intakes/{REVISION}"),
        ("GET", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals"),
        ("POST", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals"),
        ("GET", "/api/v1/members"),
    ],
)
def test_the_orchestrator_surface_is_four_reads_and_one_proposal(method: str, path: str) -> None:
    assert orchestrator_permits(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Deciding a proposal is a person's, on /review; the lane never reaches it.
        ("POST", "/api/v1/decomposition-proposals/x/approve"),
        ("POST", f"/api/v1/decomposition-proposals/{REVISION}/require-revision"),
        ("POST", f"/api/v1/package-intakes/{REVISION}"),
        ("POST", "/api/v1/change-records/139/work"),
        ("GET", "/api/v1/change-records/0/work"),
        ("GET", f"/api/v1/package-intakes/{REVISION}/"),
        ("GET", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals/extra"),
        ("GET", "/api/v1/package-intakes/not-a-uuid"),
        ("DELETE", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals"),
        ("GET", "/api/v1/status-ledger"),
        ("POST", "/api/v1/members"),
        ("GET", "/api/v1/members/"),
        ("GET", "/api/v1/member-proposals"),
    ],
)
def test_anything_else_on_the_orchestrator_is_refused(method: str, path: str) -> None:
    assert not orchestrator_permits(method, path)


def test_change_manager_is_read_only_and_only_its_listing() -> None:
    assert change_manager_permits("GET", "/api/items")
    assert not change_manager_permits("POST", "/api/items")
    assert not change_manager_permits("GET", "/api/items/12")
    assert not change_manager_permits("POST", "/api/items/12/status")
