"""The executor reaches the orchestrator through one confined client, and nothing else leaves.

Every path it may send is matched by an anchored pattern; anything else is refused before the
transport sees a byte. The bearer and key id ride on every request and never in a failure.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from rotation_worker.orchestrator_client import (
    USER_AGENT,
    Lease,
    OrchestratorClient,
    OrchestratorError,
    permits,
)
from rotation_worker.values import Value

PROGRAM = Path("src/rotation_worker")
UNIT = "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"
BEARER = "throwaway-bearer-value-for-tests"

ALLOWED = [
    ("GET", "/api/v1/status-ledger"),
    ("GET", "/api/v1/status-ledger?state=ready"),
    ("GET", "/api/v1/held-credentials"),
    ("GET", "/api/v1/members"),
    ("GET", f"/api/v1/work-units/{UNIT}"),
    ("GET", f"/api/v1/work-units/{UNIT}/history"),
    ("GET", f"/api/v1/work-units/{UNIT}/evidence"),
    ("GET", f"/api/v1/package-intakes/{UNIT}"),
    ("GET", "/api/v1/observations?source_system=recovery_floor&subject_reference=backup%3Ax"),
    ("POST", f"/api/v1/work-units/{UNIT}/claim"),
    ("POST", f"/api/v1/work-units/{UNIT}/renew"),
    ("POST", f"/api/v1/work-units/{UNIT}/evidence"),
    ("POST", f"/api/v1/work-units/{UNIT}/commands/start"),
    ("POST", f"/api/v1/work-units/{UNIT}/commands/submit"),
    ("POST", f"/api/v1/work-units/{UNIT}/commands/fail"),
]
REFUSED = [
    ("POST", "/api/v1/status-ledger"),
    ("POST", "/api/v1/members"),
    ("GET", "/api/v1/members/x"),
    ("POST", "/api/v1/member-proposals"),
    ("GET", f"/api/v1/work-units/{UNIT}/claim"),
    ("POST", f"/api/v1/work-units/{UNIT}/commands/complete"),
    ("POST", f"/api/v1/work-units/{UNIT}/commands/cancel"),
    ("POST", f"/api/v1/work-units/{UNIT}/adjudications"),
    ("POST", f"/api/v1/work-units/{UNIT}/reclaim-expired-claim"),
    ("POST", f"/api/v1/work-units/{UNIT}/release-expired-claim"),
    ("POST", f"/api/v1/work-units/{UNIT}/approvals"),
    ("POST", f"/api/v1/work-units/{UNIT}/dependencies"),
    ("POST", "/api/v1/dependencies/x/resolve"),
    ("POST", "/api/v1/observations"),
    ("GET", "/api/v1/observations/x"),
    ("GET", f"/api/v1/work-units/{UNIT}/runner-brief"),
    ("GET", f"/api/v1/work-units/{UNIT}/history/extra"),
    ("GET", f"/api/v1/work-units/{UNIT.upper()}"),
    ("GET", "/api/v1/work-units/not-a-uuid"),
    ("GET", f"/api/v1/work-units/{UNIT}/"),
    ("PUT", f"/api/v1/work-units/{UNIT}/evidence"),
    ("DELETE", f"/api/v1/work-units/{UNIT}"),
    ("GET", "/review"),
]


@pytest.mark.parametrize(("method", "path"), ALLOWED)
def test_each_route_the_executor_needs_is_permitted(method: str, path: str) -> None:
    assert permits(method, path)


@pytest.mark.parametrize(("method", "path"), REFUSED)
def test_every_other_route_is_refused(method: str, path: str) -> None:
    assert not permits(method, path)


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a forbidden request reached the transport: {request.url}")


def test_a_forbidden_request_never_reaches_the_transport() -> None:
    client = OrchestratorClient(
        "https://sds.example", "key", Value(BEARER), transport=httpx.MockTransport(_never)
    )
    with pytest.raises(OrchestratorError, match="may not"):
        client._send("POST", f"/api/v1/work-units/{UNIT}/commands/complete")


def test_every_request_names_itself_and_carries_the_bearer_and_key() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = OrchestratorClient(
        "https://sds.example", "rotation-key", Value(BEARER), transport=httpx.MockTransport(answer)
    )
    assert client.ready_units() == []
    assert seen[0].headers["Authorization"] == f"Bearer {BEARER}"
    assert seen[0].headers["X-Credential-Key-Id"] == "rotation-key"
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].url.params["state"] == "ready"


def test_a_refusal_carries_the_code_and_message_never_the_bearer() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "outside_change_window", "message": "shut"}}
        )

    client = OrchestratorClient(
        "https://sds.example", "key", Value(BEARER), transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(OrchestratorError) as raised:
        client.claim(UNIT, 3, "k")
    assert raised.value.code == "outside_change_window"
    assert "shut" in str(raised.value) and BEARER not in str(raised.value)


def test_no_answer_and_a_non_json_answer_are_errors() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    for transport, match in ((down, "got no answer: ConnectError"), (html, "isn't JSON")):
        client = OrchestratorClient(
            "https://sds.example", "key", Value(BEARER), transport=httpx.MockTransport(transport)
        )
        with pytest.raises(OrchestratorError, match=match):
            client.unit(UNIT)


def test_commands_send_the_lease_and_return_the_new_version() -> None:
    bodies: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"version": 7})

    client = OrchestratorClient(
        "https://sds.example", "key", Value(BEARER), transport=httpx.MockTransport(answer)
    )
    lease = Lease(2, "lease-token")
    assert client.fail(UNIT, 6, lease, "k", "why") == 7
    assert bodies[0] == {
        "idempotency_key": "k",
        "expected_version": 6,
        "attempt": 2,
        "lease_token": "lease-token",
        "reason": "why",
    }


@pytest.mark.parametrize("url", ["http://sds.example", "https://", "https://a..b"])
def test_an_unusable_url_is_refused_at_construction(url: str) -> None:
    with pytest.raises(OrchestratorError, match="URL"):
        OrchestratorClient(url, "key", Value(BEARER))


def test_only_the_client_spells_an_orchestrator_path() -> None:
    for source in sorted(PROGRAM.rglob("*.py")):
        # coolify.py spells Coolify's API paths, which share the `/api/v1/` prefix.
        if source.name in ("orchestrator_client.py", "coolify.py"):
            continue
        assert re.findall(r"/api/v1/", source.read_text()) == [], source


# The modules that may reach a network service: the orchestrator's client, the Coolify client,
# and each adapter whose provider is reached over HTTP (its own path allowlist confines what it may
# send). The orchestrator-bearer adapter probes the orchestrator with the bearer being rotated, and
# the change-manager adapter probes change-manager with its. The rotation App's module writes and
# reads Actions secrets, and the GitHub PAT and Bitbucket adapters probe their providers. The
# notifier posts to alert-hub's `/v1/alerts` alone.
HTTP_CLIENTS = frozenset(
    {
        "orchestrator_client.py",
        "coolify.py",
        "github_actions.py",
        "adapters/openrouter.py",
        "adapters/orchestrator_bearer.py",
        "adapters/change_manager.py",
        "adapters/github_pat.py",
        "adapters/bitbucket.py",
        "readers.py",
        "notify.py",
        # The one health URL its registry row names, with no credential.
        "adapters/postgres_role.py",
    }
)


# The modules that may start a process, each passing values only on stdin, which the tests of
# each prove (`test_keychain_store.py`, `test_bws_machine_token.py`).
PROCESS_RUNNERS = frozenset(
    {
        # git, with no value at all.
        "git_mirror.py",
        # `/usr/bin/security`, the only way to the Keychain (ADR-0055 amendment 12).
        "secret_store.py",
        # A fresh Bitwarden login in a child process with an empty HOME (amendment 1).
        "adapters/bws_machine_token.py",
        # `ssh` to the VPS Postgres helper, the password on stdin only (`test_helper_client.py`).
        "helper_client.py",
    }
)


def test_only_the_clients_import_an_http_client_and_only_named_modules_run_a_process() -> None:
    for source in sorted(PROGRAM.rglob("*.py")):
        imported = set()
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        if source.relative_to(PROGRAM).as_posix() not in HTTP_CLIENTS:
            assert not imported & {"httpx", "estate_clients", "requests", "urllib"}, source
        if source.relative_to(PROGRAM).as_posix() not in PROCESS_RUNNERS:
            # Values never enter an argument list (ADR-0055 decision 3): no other module can
            # start a process at all.
            assert "subprocess" not in imported, source


def test_observations_are_asked_by_source_subject_and_start() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"observed_at": "2026-10-16T06:00:00+00:00"}])

    client = OrchestratorClient(
        "https://sds.example", "key", Value(BEARER), transport=httpx.MockTransport(answer)
    )
    since = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
    rows = client.observations("recovery_floor", "backup:vps-production", since)
    assert rows == [{"observed_at": "2026-10-16T06:00:00+00:00"}]
    [request] = seen
    assert (request.method, request.url.path) == ("GET", "/api/v1/observations")
    assert dict(request.url.params) == {
        "source_system": "recovery_floor",
        "subject_reference": "backup:vps-production",
        "observed_from": "2026-10-08T06:00:00+00:00",
    }
