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
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from work_carrier.change_manager import ForbiddenEndpointError, HttpWorkRecordSource, is_allowed
from work_carrier.declaration import GitHubDeclarationSource
from work_carrier.declaration import is_allowed as is_allowed_declaration
from work_carrier.orchestrator_client import ForbiddenEndpointError as ForbiddenWriteError
from work_carrier.orchestrator_client import (
    OrchestratorClient,
    is_allowed_read,
    is_allowed_write,
)

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
    # For the read allowlist's anchored path template, the same shape the watcher's uses. A
    # membership test would need no regex, but the path carries an id, so what has to be
    # asserted is a TEMPLATE -- and a hand-rolled split-and-check is a parser nobody reviews.
    "re",
    "subprocess",
    "sys",
    # The declaration reader's own age report and the TOML the repository declares it in.
    "time",
    "tomllib",
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
        client=httpx.Client(
            base_url="https://example.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ForbiddenEndpointError):
        source._get("/api/items/1/approve", {})
    assert seen == []


def test_the_declaration_read_surface_is_one_route_and_no_more() -> None:
    """One route, anchored on the filename as well as on the shape of a repository name.

    The slug is interpolated from a package's own `profile_fields.target_repo`, so the
    template is what has to be asserted: a traversal segment, a query, a second path
    segment or a different file all have to fail. It may not reach the other two
    services' routes either, and neither of their allowlists may admit this one.
    """
    assert is_allowed_declaration("/repos/AlobarQuest/brain/contents/factory-target.toml")
    for forbidden in (
        "/repos/AlobarQuest/brain/contents/factory-target.toml/",
        "/repos/AlobarQuest/brain/contents/.env",
        "/repos/AlobarQuest/brain/contents/factory-target.toml?ref=main",
        "/repos/AlobarQuest/../../contents/factory-target.toml",
        "/repos/AlobarQuest/brain/actions/secrets",
        "/repos/AlobarQuest/brain",
        "/api/items",
        "/api/v1/package-intakes",
    ):
        assert not is_allowed_declaration(forbidden), forbidden
    assert not is_allowed("/repos/AlobarQuest/brain/contents/factory-target.toml")
    assert not is_allowed_read("/repos/AlobarQuest/brain/contents/factory-target.toml")
    assert not is_allowed_write("/repos/AlobarQuest/brain/contents/factory-target.toml")


def test_the_declaration_reader_asks_and_can_do_nothing_else() -> None:
    """One public read method. A second is a surface nobody decided on."""
    public = {
        name
        for name in vars(GitHubDeclarationSource)
        if not name.startswith("_") and name not in {"close"}
    }
    assert public == {"declaration"}


def test_a_repository_name_this_program_may_not_ask_about_never_reaches_the_transport() -> None:
    """The refusal is BEFORE the request, and it is returned rather than raised.

    A name is authored text in somebody's `package.yaml`, so a bad one must be a finding
    about one record rather than an exception that ends the pass. The positive case is
    here too: without it this asserts only that the reader refuses things, which a reader
    that refused everything would also satisfy.
    """
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text='factory_target = true\nfactory_target_reason = "x"\n')

    source = GitHubDeclarationSource(
        token="t",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    for refused in ("AlobarQuest/brain/../../secrets", "brain", "AlobarQuest/brain?ref=main", ""):
        answer = source.declaration(refused)
        assert answer.target is None, refused
        assert "may ask about" in answer.detail, refused
    assert seen == []

    assert source.declaration("AlobarQuest/brain").target is True
    assert seen == ["/repos/AlobarQuest/brain/contents/factory-target.toml"]
