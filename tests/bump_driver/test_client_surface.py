"""The lane's surfaces: exactly what a pass asks, each anchored, and every other path refused."""

from __future__ import annotations

import pytest

from bump_driver.client import change_manager_permits, github_permits, orchestrator_permits

UNIT = "0c7a484c-1111-4111-8111-111111111111"
REVISION = "f962475a-1964-46df-81ac-2b3029dec2a7"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/change-records/170/work"),
        ("GET", f"/api/v1/package-intakes/{REVISION}"),
        ("GET", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals"),
        ("GET", f"/api/v1/work-units/{UNIT}"),
        ("GET", f"/api/v1/work-units/{UNIT}/history"),
        ("GET", f"/api/v1/work-units/{UNIT}/pr-merge-admission"),
        ("POST", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals"),
        ("POST", f"/api/v1/work-units/{UNIT}/dispatch"),
        ("POST", f"/api/v1/work-units/{UNIT}/pr-merge"),
    ],
)
def test_the_orchestrator_surface_is_what_a_pass_asks(method: str, path: str) -> None:
    assert orchestrator_permits(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Deciding is a person's or policy's; verifying is the VERIFIER lane's.
        ("POST", f"/api/v1/decomposition-proposals/{REVISION}/approve"),
        ("POST", f"/api/v1/work-units/{UNIT}/verify"),
        ("POST", f"/api/v1/work-units/{UNIT}/requeue"),
        ("POST", f"/api/v1/work-units/{UNIT}/retry-authorization"),
        ("POST", f"/api/v1/work-units/{UNIT}/commands/retry"),
        ("POST", f"/api/v1/work-units/{UNIT}/claim"),
        ("POST", f"/api/v1/work-units/{UNIT}"),
        ("POST", f"/api/v1/work-units/{UNIT}/pr-merge-admission"),
        ("POST", f"/api/v1/package-intakes/{REVISION}"),
        ("POST", "/api/v1/change-records/170/work"),
        ("POST", "/api/v1/estate-pr-merge"),
        ("POST", "/api/v1/inert-pr-merge"),
        ("GET", f"/api/v1/work-units/{UNIT}/dispatch"),
        ("GET", f"/api/v1/work-units/{UNIT}/pr-merge"),
        ("GET", f"/api/v1/work-units/{UNIT}/"),
        ("GET", f"/api/v1/work-units/{UNIT}/evidence"),
        ("GET", "/api/v1/work-units/not-a-uuid"),
        ("GET", "/api/v1/change-records/0/work"),
        ("GET", f"/api/v1/package-intakes/{REVISION}/decomposition-proposals/extra"),
        ("DELETE", f"/api/v1/work-units/{UNIT}"),
        ("GET", "/api/v1/status-ledger"),
    ],
)
def test_anything_else_on_the_orchestrator_is_refused(method: str, path: str) -> None:
    assert not orchestrator_permits(method, path)


def test_change_manager_is_read_only_and_only_its_listing() -> None:
    assert change_manager_permits("GET", "/api/items")
    assert not change_manager_permits("POST", "/api/items")
    assert not change_manager_permits("GET", "/api/items/12")
    assert not change_manager_permits("POST", "/api/items/12/status")


def test_github_is_one_read_of_one_pull_request() -> None:
    assert github_permits("GET", "/repos/AlobarQuest/orchestrator/pulls/506")
    assert not github_permits("PUT", "/repos/AlobarQuest/orchestrator/pulls/506/merge")
    assert not github_permits("POST", "/repos/AlobarQuest/orchestrator/pulls/506")
    assert not github_permits("GET", "/repos/AlobarQuest/orchestrator/pulls")
    assert not github_permits("GET", "/repos/AlobarQuest/orchestrator/pulls/506/files")
    assert not github_permits("GET", "/repos/AlobarQuest/orchestrator/pulls/0")
    assert not github_permits("GET", "/user")
