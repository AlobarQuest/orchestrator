"""`work_carrier` is a separate program that happens to live in this repository.

Same shape as the watcher's, the ledger's, the lander's and the reconciliation runner's
isolation tests, and for the same reason: hosting an out-of-process program here is a packaging
choice, and the moment it can import the orchestrator it stops being one.

**IT IS THE FIRST OF THESE PROGRAMS THAT WANTED TO**, which is why the bound is worth stating
rather than inheriting. The carry's job is to produce a package-intake payload, and building one
is `orchestrator.package_sources.load_package_intake_payload` -- an import away. It shells out to
`orchestrator emit-intake-payload` instead, and the better of the two reasons is not this test:
the production intake path is documented as that command feeding the `/review/intakes/new` form,
so running the command is what makes the carry's output byte-identical to what a human produces
by hand. There is no second path to diverge because there is no second path.

**THE WRITE SURFACE IS ONE ROUTE, AND IT WAS EMPTY UNTIL ADR-0027.** The carry now registers the
intake it prepared, so the claim this file used to make -- that no code here could write at all
-- is no longer true and is not weakened into prose. What replaces it is narrower and checkable:
change-manager is still read-only (`HttpWorkRecordSource` has exactly one public method, and it
is a read), and the orchestrator surface is exactly `POST /api/v1/package-intakes`. So "a record
the carry could not PREPARE is left exactly as it was" survives as a property of the program's
shape -- the write happens only after a payload the emitter built and this program re-checked --
and "a record it could not prepare is never registered" is the behaviour test one directory over.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from work_carrier.change_manager import ForbiddenEndpointError, HttpWorkRecordSource, is_allowed
from work_carrier.orchestrator_client import ForbiddenEndpointError as ForbiddenWriteError
from work_carrier.orchestrator_client import OrchestratorClient, is_allowed_write

CARRIER = Path("src/work_carrier")
ORCHESTRATOR = Path("src/orchestrator")

ALLOWED_TOP_LEVEL = {
    "__future__",
    "argparse",
    "dataclasses",
    "httpx",
    "json",
    "os",
    "pathlib",
    "subprocess",
    "sys",
    "typing",
    "work_carrier",
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


def test_the_carrier_imports_nothing_from_the_orchestrator() -> None:
    offenders = {name for name in _imports(CARRIER) if name.split(".")[0] == "orchestrator"}
    assert offenders == set(), (
        "the carry builds its payload by running `orchestrator emit-intake-payload`, not by "
        "importing the function that backs it"
    )


def test_the_orchestrator_imports_nothing_from_the_carrier() -> None:
    offenders = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "work_carrier"}
    assert offenders == set()


def test_the_carriers_third_party_deps_are_confined() -> None:
    offenders = {name.split(".")[0] for name in _imports(CARRIER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_the_read_surface_is_one_route_and_no_more() -> None:
    assert is_allowed("/api/items")
    for forbidden in (
        "/api/items/",
        "/api/items/1",
        "/api/items/1/approve",
        "/api/items/1/claim",
        "/api/items/1/outcome",
        "/api/work-changes",
        "/api/sync",
        "/api/deploy-policy",
    ):
        assert not is_allowed(forbidden), forbidden


def test_the_carrier_cannot_write_to_change_manager_at_all() -> None:
    """ADR-0027 gave the carry a write to the ORCHESTRATOR and none to change-manager.

    Asserted over the client's public surface rather than by reading it, so a write added later
    has to move this test. A `post`/`put`/`patch`/`delete` here would be the first thing able to
    approve the proposal the carry is carrying -- a system asking itself for permission.
    """
    public = {name for name in vars(HttpWorkRecordSource) if not name.startswith("_")}
    assert public == {"approved_work"}, (
        "the carry reads one listing and writes nothing to change-manager; a second public "
        "method here is the first thing able to decide the work it is carrying"
    )


def test_the_orchestrator_write_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_write("/api/v1/package-intakes")
    for forbidden in (
        "/api/v1/package-intakes/",
        "/api/v1/revisions",
        "/api/v1/observations",
        "/api/v1/package-intakes/1/decomposition-proposals",
        "/api/v1/work-units/1/approvals",
        "/api/v1/work-units/1/dispatch",
        "/review/intakes",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_carrier_registers_and_can_do_nothing_else_to_the_orchestrator() -> None:
    """One public write method, named. A second would be a surface nobody decided to open."""
    public = {
        name
        for name in vars(OrchestratorClient)
        if not name.startswith("_") and name not in {"close"}
    }
    assert public == {"register_intake"}


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(201, json={})

    client = OrchestratorClient(
        "token",
        "orchestrator-system",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenWriteError):
        client._post("/api/v1/work-units/1/approvals", {})
    assert seen == []


def test_a_forbidden_path_never_reaches_the_transport() -> None:
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=[])

    source = HttpWorkRecordSource(
        base_url="https://example.invalid",
        token="t",
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ForbiddenEndpointError):
        source._get("/api/items/1/approve", {})
    assert seen == []
