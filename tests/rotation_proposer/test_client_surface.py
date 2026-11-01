"""This program reaches two services through three confined clients it does not own, one small
client of its own, and alert-hub.

It reuses `bump_proposer`'s change-manager client (two paths: propose, and list the work source)
and its orchestrator client (one path: the observation ingest), and `sds_members`' read of the
member record. Reuse is only safe if the reuse is the WHOLE surface -- so this asserts that
`client.py` alone names a path, that only it and the alert annotation import an HTTP client, and
that every client it constructs, under its own user agent, still refuses every other route before
the transport is reached. `client.py` is two reads on the observer bearer: it exists so that
widening `bump_proposer`'s POST-only client never widens that program.
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
from rotation_proposer.client import CallError, Orchestrator, permits

PROGRAM = Path("src/rotation_proposer")
CLIENT = PROGRAM / "client.py"
UNIT = "0c7a484c-1111-4111-8111-111111111111"


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a forbidden request reached the transport: {request.url}")


def test_the_program_spells_no_service_path_of_its_own() -> None:
    """Every route it can reach is spelled in the reused clients or in `client.py`. A path
    appearing anywhere else is a surface no client's confinement covers."""
    text = "\n".join(path.read_text() for path in sorted(PROGRAM.rglob("*.py")) if path != CLIENT)
    assert re.findall(r"/api/[A-Za-z0-9_\-/{}]+", text) == []
    assert "sds.alobar.net" not in CLIENT.read_text() + text


def test_the_program_imports_no_http_client() -> None:
    """So nothing can leave this process except through the confined clients and the shared
    alert-hub client. `alerts.py` and `client.py` alone may `import httpx`, to annotate a test
    transport, and may use no `httpx` attribute but `BaseTransport`."""
    annotating = {"alerts.py", "client.py"}
    for path in sorted(PROGRAM.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if path.name in annotating and name == "httpx" and isinstance(node, ast.Import):
                    continue
                assert name.split(".")[0] not in {"httpx", "requests", "urllib", "http"}, path
        if path.name in annotating:
            used = {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "httpx"
            }
            assert used == {"BaseTransport"}


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


@pytest.mark.parametrize(
    "path", ["/api/v1/change-records/139/work", f"/api/v1/work-units/{UNIT}/history"]
)
def test_its_own_client_is_two_reads(path) -> None:
    assert permits("GET", path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/change-records/139/work"),
        ("POST", f"/api/v1/work-units/{UNIT}/history"),
        ("DELETE", "/api/v1/change-records/139/work"),
        ("GET", "/api/v1/change-records/0/work"),
        ("GET", "/api/v1/change-records/139/work/"),
        ("GET", "/api/v1/change-records/139/work/extra"),
        ("GET", "/api/v1/change-records/abc/work"),
        ("GET", f"/api/v1/work-units/{UNIT}"),
        ("GET", f"/api/v1/work-units/{UNIT}/history/"),
        ("GET", f"/api/v1/work-units/{UNIT}/evidence"),
        ("GET", "/api/v1/work-units/not-a-uuid/history"),
        ("GET", "/api/v1/members"),
        ("POST", "/api/v1/observations"),
    ],
)
def test_its_own_client_refuses_every_other_route(method, path) -> None:
    client = Orchestrator(
        base_url="https://sds.example.net",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(_never),
    )
    with pytest.raises(CallError, match="may not"):
        client._json(method, path)


def test_a_refusal_below_500_is_a_call_error() -> None:
    client = Orchestrator(
        base_url="https://sds.example.net",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})),
    )
    with pytest.raises(CallError, match="404"):
        client.work(139)
