"""`work_carrier` is a separate program that happens to live in this repository.

Same shape as the watcher's, the ledger's, the lander's and the retired reconciliation runner's
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
is a read), and the orchestrator WRITE surface is exactly `POST /api/v1/package-intakes`. So "a
record the carry could not PREPARE is left exactly as it was" survives as a property of the
program's shape -- the write happens only after a payload the emitter built and this program
re-checked -- and "a record it could not prepare is never registered" is the behaviour test one
directory over.

**AND THE ORCHESTRATOR SURFACE IS NO LONGER WRITE-ONLY.** Since 2026-08-21 the carry also READS
`GET /api/v1/change-records/{id}/work`, to find out whether the record it is about to carry has
already been carried. That is a second route and a second allowlist, pinned here the same way:
two predicates, neither of which can satisfy the other, so a read allowlist that admitted the
write route -- or the reverse -- reddens rather than quietly opening a surface nobody decided on.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`.
"""

from __future__ import annotations

import pytest

from work_carrier.change_manager import ForbiddenEndpointError, HttpWorkRecordSource, is_allowed
from work_carrier.orchestrator_client import ForbiddenEndpointError as ForbiddenWriteError
from work_carrier.orchestrator_client import (
    OrchestratorClient,
    is_allowed_read,
    is_allowed_write,
)


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


def test_the_orchestrator_read_surface_is_one_route_and_no_more() -> None:
    """The read's own allowlist, and it may not admit the write route either.

    Anchored, so a record id cannot compose a path to somewhere else: a traversal segment, a
    trailing path, or a sibling route under the same prefix all have to fail.
    """
    assert is_allowed_read("/api/v1/change-records/62/work")
    for forbidden in (
        "/api/v1/change-records/62/work/",
        "/api/v1/change-records/62/work/units",
        "/api/v1/change-records/62",
        "/api/v1/change-records/62/../../work-units",
        "/api/v1/change-records//work",
        "/api/v1/package-intakes",
        "/api/v1/work-units/1/evidence-pack",
    ):
        assert not is_allowed_read(forbidden), forbidden


def test_neither_allowlist_can_satisfy_the_other() -> None:
    """Two predicates rather than one path check, asserted rather than described.

    A single allowlist shared by both verbs would let the read's route be POSTed to and the
    write's be GOT -- surfaces nobody decided to open, and each invisible from the other side.
    """
    assert not is_allowed_write("/api/v1/change-records/62/work")
    assert not is_allowed_read("/api/v1/package-intakes")


def test_the_carrier_asks_and_registers_and_can_do_nothing_else_to_the_orchestrator() -> None:
    """Two public methods, named: one read, one write. A third is a surface nobody decided on."""
    public = {
        name
        for name in vars(OrchestratorClient)
        if not name.startswith("_") and name not in {"close"}
    }
    assert public == {"carried_revisions", "register_intake"}


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


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    """The guard is BEFORE the request, not a check on what came back."""
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        "token",
        "orchestrator-system",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenWriteError):
        client._get("/api/v1/work-units/1/evidence-pack")
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
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenEndpointError):
        source._get("/api/items/1/approve", {})
    assert seen == []
