"""The ledger shares no import path with the orchestrator and writes to one endpoint."""

import ast
from pathlib import Path

import httpx
import pytest

from landing_ledger.github import ForbiddenMethodError, GitHubReader
from landing_ledger.orchestrator_client import (
    ForbiddenEndpointError,
    ForbiddenReadError,
    LedgerWriteError,
    OrchestratorClient,
    is_allowed_read,
    is_allowed_write,
)

LEDGER = Path("src/landing_ledger")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "landing_ledger",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "os",
    "re",
    "typing",
    "__future__",
}


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
    return names


def test_the_ledger_imports_nothing_from_the_orchestrator() -> None:
    assert {name for name in _imports(LEDGER) if name.split(".")[0] == "orchestrator"} == set()


def test_the_orchestrator_imports_nothing_from_the_ledger() -> None:
    assert {name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "landing_ledger"} == (
        set()
    )


def test_the_ledgers_third_party_deps_are_confined() -> None:
    assert {name.split(".")[0] for name in _imports(LEDGER)} - ALLOWED_TOP_LEVEL == set()


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    assert not is_allowed_write("/api/v1/observations/")
    assert not is_allowed_write("/api/v1/deployment-observations")
    unit = "/api/v1/work-units/00000000-0000-0000-0000-000000000000"
    assert not is_allowed_write(f"{unit}/commands/ready")
    assert not is_allowed_write(f"{unit}/evidence")
    assert not is_allowed_write(f"{unit}/adjudications")
    assert not is_allowed_write(f"{unit}/dispatch")


def test_the_read_surface_is_the_paths_the_audit_needs_and_no_more() -> None:
    """The audit re-evaluates what the LEDGER recorded, so it reads -- and that changed this
    client's contract in WS-P3.6 Increment 3, and again in WS-P3.7 Increment 5, each time as a
    decision rather than by drifting. Server-side, OBSERVER reads are deliberately unconfined, so
    this bound is the only one there is.

    THREE paths, and the two additions are ADR-0020's: a landing the factory made says a work
    unit's criteria were met, and read from GitHub alone that is the runner's own assertion
    re-recorded. The evidence pack answers who decided and on what evidence; the unit history
    answers which pull request this unit actually landed. Neither answers the other's half.
    """
    unit = "/api/v1/work-units/0c0002c6-9869-59bc-84c6-654e6fc57d9e"
    assert is_allowed_read("/api/v1/observations")
    assert is_allowed_read(f"{unit}/evidence-pack")
    assert is_allowed_read(f"{unit}/history")

    assert not is_allowed_read("/api/v1/observations/")
    assert not is_allowed_read("/api/v1/work-units")
    assert not is_allowed_read("/api/v1/in-flight-units")
    assert not is_allowed_read("/api/v1/status-ledger")
    # The bound is a bound: the unit paths are anchored and enumerated, so neither a sibling read
    # on the same unit nor a prefix of an allowed one gets in on the strength of the two that do.
    assert not is_allowed_read(f"{unit}/evidence-pack/markdown")
    assert not is_allowed_read(f"{unit}/pr-merge-admission")
    assert not is_allowed_read(f"{unit}/runner-brief")
    assert not is_allowed_read(f"{unit}/readiness")
    assert not is_allowed_read(f"{unit}/evidence")
    assert not is_allowed_read(f"{unit}/history/")
    assert not is_allowed_read("/api/v1/work-units/not-a-unit-id/history")


def test_the_admission_surface_stays_out_of_reach_of_the_audit() -> None:
    """Named separately from the list above because it is a DECISION, not an omission.

    `…/pr-merge-admission` composes exactly the answer the factory audit wants, in one call. It is
    excluded because it evaluates whether the landing may happen NOW, and re-asking it about a
    landing that already happened turns ordinary change -- a superseded approval, a re-classified
    repository -- into findings. The audit reads the durable record instead, which does not drift.
    """
    unit = "/api/v1/work-units/0c0002c6-9869-59bc-84c6-654e6fc57d9e"

    assert not is_allowed_read(f"{unit}/pr-merge-admission")
    assert not is_allowed_write(f"{unit}/pr-merge")


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=[])

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ForbiddenReadError):
        client.get("/api/v1/status-ledger")
    with pytest.raises(ForbiddenReadError):
        client.read_evidence_pack("../../status-ledger")
    assert seen == []
    assert client.get("/api/v1/observations") == []
    assert seen == ["/api/v1/observations"]


def test_a_unit_the_orchestrator_does_not_hold_is_an_ANSWER_rather_than_an_error() -> None:
    """The factory audit turns None into a finding about the landing and an exception into an
    incomplete pass, so the two must not arrive as the same thing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "work_unit_not_found"})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )
    unit = "0c0002c6-9869-59bc-84c6-654e6fc57d9e"

    assert client.read_evidence_pack(unit) is None
    assert client.read_unit_history(unit) is None


def test_a_ledger_that_is_not_a_LIST_is_refused_rather_than_iterated() -> None:
    """A proxy error page, or a route that started answering an object, would otherwise become an
    empty audit -- zero landings, zero findings, and no way to tell that from a clean estate."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"detail": "not a list"})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LedgerWriteError):
        client.read_landings("AlobarQuest/orchestrator")


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        base_url="https://x",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/dispatch", {})
    assert seen == []


def test_the_github_half_only_ever_reads() -> None:
    """It is a reader. The one call site takes a path and issues GET; there is no other verb."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={})

    reader = GitHubReader(token="t", transport=httpx.MockTransport(handler))
    reader.get("/repos/AlobarQuest/orchestrator")

    assert seen == ["GET"]
    with pytest.raises(ForbiddenMethodError):
        reader.get("repos/AlobarQuest/orchestrator")
