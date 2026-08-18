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

**THE WRITE SURFACE IS EMPTY, AND THAT IS THE POINT.** Every sibling here asserts which routes it
may write; this one asserts that it may write NONE, to either system. That is what makes "a
record the carry cannot prepare is left exactly as it was" a property of the program's shape
rather than of a branch that has to be reached correctly -- there is no code that could write,
so no ordering in which a refusal leaves something half done.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from work_carrier.change_manager import ForbiddenEndpointError, HttpWorkRecordSource, is_allowed

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


def test_the_carrier_has_no_write_method_at_all() -> None:
    """Not "its writes are allowlisted" -- there are none.

    Asserted over the client's public surface rather than by reading it, so a write added later
    has to move this test. A `post`/`put`/`patch`/`delete` here would be the first thing able to
    change a record the carry decided it could not prepare.
    """
    public = {name for name in vars(HttpWorkRecordSource) if not name.startswith("_")}
    assert public == {"approved_work"}, (
        "the carry reads one listing and writes nothing; a second public method here is the "
        "first thing able to change a record it decided it could not prepare"
    )


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
