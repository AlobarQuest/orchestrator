"""`work_watcher` is a separate program that happens to live in this repository (ADR-0029).

Same shape as the carry's, the rollout watcher's, the ledger's and the lander's isolation tests,
and for the same reason: hosting an out-of-process program here is a packaging choice, and the
moment it can import the orchestrator it stops being one.

**THIS ONE MATTERS MORE THAN THE OTHERS, because the scope it holds is wider than the surface it
asserts.** The retirement route joins `propose`, which also reaches `POST /api/deploy-changes` --
the ingress that runs the pinned policy and can therefore write `approved`, producing a record the
estate will land unattended. Nothing at the service stops this program calling it. What stops it
is the allowlist in `work_watcher/change_manager.py`, and these tests are what keep that allowlist
honest, so they are the control for the widening ADR-0029 records rather than a tidiness check.

**THE SURFACE IS TWO ROUTES ACROSS TWO SERVICES, and each is asserted from a different angle** --
the path predicate, the client's public method set, and a transport that proves a refused path
never becomes a request. A guard is only worth having if it fires before anything leaves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from work_watcher.change_manager import (
    ForbiddenEndpointError,
    RetirementClient,
    is_allowed_write,
)
from work_watcher.orchestrator_client import (
    ForbiddenEndpointError as ForbiddenReadError,
)
from work_watcher.orchestrator_client import (
    OrchestratorClient,
    is_allowed_read,
)

WATCHER = Path("src/work_watcher")
ORCHESTRATOR = Path("src/orchestrator")

ALLOWED_TOP_LEVEL = {
    "__future__",
    "argparse",
    "httpx",
    "os",
    "re",
    "sys",
    "typing",
    # The listing is the carry's, deliberately: one question, one parse, and one place where the
    # pipeline is named in the query. See `work_watcher/change_manager.py`.
    "work_carrier",
    "work_watcher",
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
    assert offenders == set(), (
        "the watcher asks the orchestrator over HTTP for the completion verdict; importing the "
        "function that derives it would make this program a second implementation of the rule"
    )


def test_the_orchestrator_imports_nothing_from_the_watcher() -> None:
    offenders = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "work_watcher"}
    assert offenders == set()


def test_the_watchers_third_party_deps_are_confined() -> None:
    offenders = {name.split(".")[0] for name in _imports(WATCHER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_the_change_manager_write_surface_is_one_route_and_no_more() -> None:
    """The listed forbidden paths are not decoration: every one is reachable by this program's
    SCOPE. `deploy-changes` can write `approved` through the pinned policy, and the decision verbs
    move a status a human owns. The allowlist is the only thing between them and this process.
    """
    assert is_allowed_write("/api/items/61/work-retirement")
    for forbidden in (
        "/api/deploy-changes",
        "/api/work-changes",
        "/api/items/61/approve",
        "/api/items/61/resolve",
        "/api/items/61/reactivate",
        "/api/items/61/claim",
        "/api/items/61/outcome",
        "/api/items/61/handoff",
        "/api/items/61/deploy-retirement",
        "/api/items/61/deploy-observation",
        "/api/items/61/work-retirement/",
        "/api/items/61/work-retirementX",
        "/api/items/../items/61/work-retirement",
        "/api/sync",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_watcher_retires_and_can_do_nothing_else_to_change_manager() -> None:
    """One public method, named. A second would be a surface nobody decided to open."""
    public = {name for name in vars(RetirementClient) if not name.startswith("_")}
    assert public == {"retire"}


def test_the_orchestrator_read_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_read("/api/v1/change-records/61/work")
    for forbidden in (
        "/api/v1/change-records/61/work/",
        "/api/v1/change-records/61",
        "/api/v1/package-intakes",
        "/api/v1/work-units/1/dispatch",
        "/api/v1/work-units/1/commands/ready",
        "/api/v1/observations",
        "/api/v1/traceability",
    ):
        assert not is_allowed_read(forbidden), forbidden


def test_the_watcher_reads_and_can_do_nothing_else_to_the_orchestrator() -> None:
    public = {name for name in vars(OrchestratorClient) if not name.startswith("_")}
    assert public == {"work_for"}


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    """The guard runs before the request is built, so a mistake fails inside this process.

    Asserted by a transport that RECORDS what it was asked to send: a refusal that happened after
    the request left would be indistinguishable from this one by exception type alone.
    """
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    client = RetirementClient(
        base_url="https://change-mgr.example",
        token="x",
        client=httpx.Client(
            base_url="https://change-mgr.example", transport=httpx.MockTransport(record)
        ),
    )
    with pytest.raises(ForbiddenEndpointError):
        client._post("/api/items/61/approve", {"actor": "x"})
    assert seen == []


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        "x",
        "orchestrator-system",
        base_url="https://sds.example",
        client=httpx.Client(base_url="https://sds.example", transport=httpx.MockTransport(record)),
    )
    with pytest.raises(ForbiddenReadError):
        client._get("/api/v1/work-units/1/dispatch")
    assert seen == []
