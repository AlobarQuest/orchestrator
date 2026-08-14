"""`estate_lander` is a separate program that happens to live in this repository.

Same shape as the watcher's and the ledger's isolation tests, and for the same reason: hosting an
out-of-process program here is a packaging choice, and the moment it can import the orchestrator
it stops being one.

**The bound matters more here than for its siblings**, because this is the only scheduled program
in the estate whose pass ends in something changing a running service. What makes that acceptable
is that it composes NOTHING: every term is evaluated inside the orchestrator, in the transaction
that records the act. A program that could import the orchestrator's own admission module could
answer the question it is supposed to be asking, and the property would be gone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from estate_lander.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    is_allowed_read,
    is_allowed_write,
)

LANDER = Path("src/estate_lander")
ORCHESTRATOR = Path("src/orchestrator")

# Everything the program may import at the top level. `httpx` and the standard library, plus the
# producer's already-confined change-manager client -- reused rather than re-implemented, because
# a second read-only client for one listing would be a second place for the withheld-source trap
# to be forgotten.
ALLOWED_TOP_LEVEL = {
    "__future__",
    "argparse",
    "change_proposer",
    "dataclasses",
    "estate_lander",
    "httpx",
    "os",
    "sys",
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


def test_the_lander_imports_nothing_from_the_orchestrator() -> None:
    offenders = {name for name in _imports(LANDER) if name.split(".")[0] == "orchestrator"}
    assert offenders == set()


def test_the_orchestrator_imports_nothing_from_the_lander() -> None:
    offenders = {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "estate_lander"}
    assert offenders == set()


def test_the_landers_third_party_deps_are_confined() -> None:
    offenders = {name.split(".")[0] for name in _imports(LANDER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_the_write_surface_is_two_routes_and_no_more() -> None:
    """TWO since ADR-0019 Increment 6, and the count is in the name so the second one had to be
    written down rather than acquired. The surface GREW by a named path; it did not open. The
    addition is much the smaller act of the two -- it brings a topic branch up to date with its
    base, where the other rewrites a default branch and starts a rollout on a running service."""
    assert is_allowed_write("/api/v1/estate-pr-merge")
    assert is_allowed_write("/api/v1/estate-pr-branch-update")
    for forbidden in (
        "/api/v1/estate-pr-merge/",
        "/api/v1/estate-pr-branch-update/",
        "/api/v1/estate-pr-merge-admission",
        "/api/v1/work-units/x/pr-merge",
        "/api/v1/work-units/x/dispatch",
        "/api/v1/observations",
        "/api/v1/package-intakes",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_read_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_read("/api/v1/estate-pr-merge-admission")
    for forbidden in (
        "/api/v1/estate-pr-merge",
        # A write path is not thereby readable: the two allowlists are separate and each names
        # its own paths, so the branch-update route cannot be reached by a GET.
        "/api/v1/estate-pr-branch-update",
        "/api/v1/status-ledger",
        "/api/v1/in-flight-units",
    ):
        assert not is_allowed_read(forbidden), forbidden


def _seen_client() -> tuple[OrchestratorClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    return OrchestratorClient("t", "k", transport=httpx.MockTransport(handler)), seen


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send("POST", "/api/v1/work-units/x/dispatch", json={})
    assert seen == []


def test_the_admission_route_may_only_be_READ() -> None:
    """The method is part of the key. A template-only allowlist that permitted the read would hand
    over the write with it -- and here the two paths are one character apart."""
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send("POST", "/api/v1/estate-pr-merge-admission", json={})
    assert seen == []
