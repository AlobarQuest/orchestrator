"""`deploy_watcher` is a separate program that happens to live in this repository.

Same shape as `test_landing_ledger_isolation.py`, and for the same reason: hosting an
out-of-process program here is a packaging choice, and the moment it can import the orchestrator
it stops being one.

**ADR-0022 MADE IT A CLIENT OF THE ORCHESTRATOR, and this docstring used to say the lane had "no
orchestrator involvement at all".** That is now false and is corrected rather than deleted: a
rollout the watcher observes may belong to a work unit, and the traceability chain's observation
hop is unit-scoped. What has NOT changed is the property this module exists for — the watcher
still imports nothing from `orchestrator.*`, still speaks HTTP from outside the process, and its
orchestrator surface is one write and one read, both bounded here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from deploy_watcher.change_manager import (
    ChangeManagerClient,
    ForbiddenEndpointError,
    is_allowed_read,
    is_allowed_write,
)
from deploy_watcher.orchestrator import (
    ForbiddenEndpointError as OrchestratorForbiddenError,
)
from deploy_watcher.orchestrator import (
    OrchestratorClient,
)
from deploy_watcher.orchestrator import (
    is_allowed_read as orchestrator_read,
)
from deploy_watcher.orchestrator import (
    is_allowed_write as orchestrator_write,
)

WATCHER = Path("src/deploy_watcher")
ORCHESTRATOR = Path("src/orchestrator")

# Everything the program may import at the top level. `httpx` and `typer` and the standard
# library, exactly as the landing ledger is confined -- and the reason it reads GitHub with a
# plain token rather than as the App: the App's JWT assertion needs `pyjwt`, which is not here.
ALLOWED_TOP_LEVEL = {
    "__future__",
    "dataclasses",
    "datetime",
    "deploy_watcher",
    "hashlib",
    "httpx",
    "json",
    "os",
    "re",
    "typer",
    "typing",
}


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
    return names


def test_the_watcher_imports_nothing_from_the_orchestrator() -> None:
    offenders = {name for name in _imports(WATCHER) if name.split(".")[0] == "orchestrator"}
    assert offenders == set()


def test_the_orchestrator_imports_nothing_from_the_watcher() -> None:
    offenders = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "deploy_watcher"}
    assert offenders == set()


def test_the_watchers_third_party_deps_are_confined() -> None:
    offenders = {name.split(".")[0] for name in _imports(WATCHER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_the_write_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_write("/api/items/44/deploy-observation")
    for forbidden in (
        "/api/items/44/deploy-observation/",
        "/api/items/44/outcome",
        "/api/items/44/claim",
        "/api/items/44/handoff",
        "/api/items/44/approve",
        "/api/items/44/resolve",
        "/api/items/44/wontfix",
        "/api/sync",
        "/api/deploy-changes",
        "/api/items/44/deploy-observations",
        "/api/items/../44/deploy-observation",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_execution_lifecycle_stays_out_of_reach_and_that_is_a_DECISION() -> None:
    """Named separately from the omission above, because it is not an omission.

    Increment 1 closed `claim`/`outcome`/`handoff` to proposed sources so a deploying-merge
    change could never be handed to the 04:00 agent holding production Coolify tools. This
    program could not reach them even if that guard were lifted.
    """
    for path in ("/api/items/44/claim", "/api/items/44/outcome", "/api/items/44/handoff"):
        assert not is_allowed_write(path) and not is_allowed_read(path)


def test_the_decision_routes_stay_out_of_reach_and_that_is_also_a_DECISION() -> None:
    """Terminating a change is a judgment. This program observes; a human decides."""
    for verb in ("approve", "defer", "wontfix", "resolve", "reactivate"):
        assert not is_allowed_write(f"/api/items/44/{verb}")


def test_the_read_surface_is_the_two_paths_the_pass_needs() -> None:
    assert is_allowed_read("/api/items")
    assert is_allowed_read("/api/items/44/deploy-observations")
    for forbidden in ("/api/items/", "/api/items/44", "/api/events", "/api/window-runs"):
        assert not is_allowed_read(forbidden), forbidden


def test_the_orchestrator_write_surface_is_one_endpoint() -> None:
    """The OBSERVER role's whole write surface, repeated in code so a second write is
    structurally unreachable rather than merely unwritten."""
    assert orchestrator_write("/api/v1/observations")
    for forbidden in (
        "/api/v1/observations/",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/dispatch",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/commands/ready",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/verify",
        "/api/v1/estate-pr-merge",
        "/api/v1/package-intakes",
    ):
        assert not orchestrator_write(forbidden), forbidden


def test_the_orchestrator_read_surface_is_the_binding_and_nothing_else() -> None:
    """One path, and it is the one that CONFIRMS a commit trailer's claim.

    Everything else about a unit — its evidence pack, its brief, its admission answer — is
    somebody else's question. `…/pr-merge-admission` in particular is a LIVE answer that
    legitimately drifts, so re-asking it later manufactures findings out of ordinary change.
    """
    unit = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
    assert orchestrator_read(f"/api/v1/work-units/{unit}/history")
    for forbidden in (
        f"/api/v1/work-units/{unit}/history/",
        f"/api/v1/work-units/{unit}/evidence-pack",
        f"/api/v1/work-units/{unit}/pr-merge-admission",
        f"/api/v1/work-units/{unit}/runner-brief",
        "/api/v1/work-units/../1/history",
        "/api/v1/observations",
        "/api/v1/status-ledger",
    ):
        assert not orchestrator_read(forbidden), forbidden


def test_a_forbidden_orchestrator_read_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    with OrchestratorClient("t", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OrchestratorForbiddenError):
            client.unit_history("not-a-unit-id")
    assert seen == []


def _seen_client() -> tuple[ChangeManagerClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    return ChangeManagerClient("t", transport=httpx.MockTransport(handler)), seen


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client.observe(-1, {})
    assert seen == []


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._get("/api/events")
    assert seen == []


def test_the_listing_always_names_the_source() -> None:
    """Increment 1's withholding guard makes an unnamed source answer with a clean empty list.

    A watcher that forgot would report "0 deploying-merge changes to watch" and exit 0 — the
    guard that protects the executor turning into the thing that hides the watcher's whole
    subject. Asserted on the wire, because that is where it is true.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    client = ChangeManagerClient("t", transport=httpx.MockTransport(handler))
    client.deploy_changes()
    assert seen == ["https://change-mgr.alobar.net/api/items?source=deploy"]
