"""This program reaches two services through two confined clients it does not own, and nothing else.

It reuses `bump_proposer`'s change-manager client (two paths: propose, and list the work source)
and its orchestrator client (one path: the observation ingest). Reuse is only safe if the reuse is
the WHOLE surface -- so this asserts the program names no path of its own, imports no HTTP client,
and that the clients it constructs, under its own user agent, still refuse every other route
before the transport is reached.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import httpx
import pytest

from bump_proposer.change_manager import ChangeManagerClient, ForbiddenEndpointError
from bump_proposer.orchestrator_client import ForbiddenEndpointError as ObserverForbidden
from bump_proposer.orchestrator_client import open_client
from rotation_proposer.cli import USER_AGENT

PROGRAM = Path("src/rotation_proposer")


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a forbidden request reached the transport: {request.url}")


def test_the_program_spells_no_service_path_of_its_own() -> None:
    """Every route it can reach is spelled in the reused clients. A path appearing here is a
    second surface those clients' confinement does not cover."""
    text = "\n".join(path.read_text() for path in sorted(PROGRAM.rglob("*.py")))
    assert re.findall(r"/api/[A-Za-z0-9_\-/{}]+", text) == []
    assert "sds.alobar.net" not in text


def test_the_program_imports_no_http_client() -> None:
    """So nothing can leave this process except through the two confined clients."""
    for path in sorted(PROGRAM.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in {"httpx", "requests", "urllib", "http"}, path


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/items/1/approve"),
        ("POST", "/api/items/1/transition"),
        ("POST", "/api/work-changes/"),
        ("GET", "/api/work-changes"),
        ("PATCH", "/api/items"),
    ],
)
def test_the_change_manager_client_it_builds_refuses_every_other_route(method, path) -> None:
    client = ChangeManagerClient("t", user_agent=USER_AGENT, transport=httpx.MockTransport(_never))
    with pytest.raises(ForbiddenEndpointError):
        client._send(method, path)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/package-intakes", "/api/v1/work-units/x/transition", "/api/v1/observations/"],
)
def test_the_orchestrator_client_it_builds_refuses_every_other_route(path) -> None:
    client = open_client(
        base_url="https://sds.example.net",
        credential_key_id="orchestrator-observer",
        token="t",
        user_agent=USER_AGENT,
        transport=httpx.MockTransport(_never),
    )
    with pytest.raises(ObserverForbidden):
        client._post(path, {})
