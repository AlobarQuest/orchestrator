"""Each identity reaches exactly its own surface, and a refused request never reaches the transport.

The import confinement -- nothing from the orchestrator, and the dependency allowlist -- is a row
of `tests/architecture/test_out_of_process_isolation.py`; what this program may WRITE is its row
of `test_external_content_observes_only.py`. This module owns the behaviour behind both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from image_release.client import (
    USER_AGENT,
    AnonymousClient,
    ForbiddenEndpointError,
    ReleaseCallError,
    SystemClient,
    UnusableEndpointError,
    VerifierClient,
    anonymous_permits,
    system_permits,
    verifier_permits,
)
from tests.architecture.import_scan import file_import_names

PROGRAM = Path("src/image_release")
ID = "0000000a-0000-4000-8000-00000000000b"
UNIT = f"/api/v1/work-units/{ID}"
BINDING_OBSERVATIONS = f"/api/v1/release-artifacts/{ID}/deployment-observations"


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"reached the transport: {request.method} {request.url}")


def test_system_may_bind_observe_and_read_only_its_three_paths() -> None:
    assert system_permits("POST", f"{UNIT}/release-artifacts")
    assert system_permits("POST", BINDING_OBSERVATIONS)
    assert system_permits("GET", BINDING_OBSERVATIONS)
    assert system_permits("GET", "/api/v1/machine-activation-candidates?repository=x")
    assert system_permits("GET", "/api/v1/dead-letter")
    for method, path in (
        ("POST", f"{UNIT}/verify"),
        ("POST", f"{UNIT}/dispatch"),
        ("POST", f"{UNIT}/release-artifacts/"),
        ("POST", f"{UNIT}/release-artifacts/extra"),
        ("POST", "/api/v1/observations"),
        ("POST", "/api/v1/machine-activation-candidates"),
        ("GET", f"{UNIT}/release-artifacts"),
        ("GET", "/health/live"),
        ("PUT", f"{UNIT}/release-artifacts"),
        ("DELETE", BINDING_OBSERVATIONS),
        ("POST", f"/api/v1/work-units/{ID.upper()}/release-artifacts"),
    ):
        assert not system_permits(method, path), (method, path)


def test_the_verifier_may_only_verify() -> None:
    assert verifier_permits("POST", f"{UNIT}/verify")
    for method, path in (
        ("GET", f"{UNIT}/verify"),
        ("POST", f"{UNIT}/verify/"),
        ("POST", f"{UNIT}/adjudications"),
        ("POST", f"{UNIT}/release-artifacts"),
        ("POST", BINDING_OBSERVATIONS),
        ("GET", "/api/v1/machine-activation-candidates"),
    ):
        assert not verifier_permits(method, path), (method, path)


def test_the_anonymous_client_reads_the_probe_paths_and_writes_nothing() -> None:
    for path in ("/health/live", "/health/ready", "/openapi.json", "/api/v1/dead-letter"):
        assert anonymous_permits("GET", path)
        assert not anonymous_permits("POST", path)
    assert not anonymous_permits("GET", "/api/v1/machine-activation-candidates")
    assert not anonymous_permits("GET", BINDING_OBSERVATIONS)


@pytest.mark.parametrize(
    ("client", "call"),
    [
        (
            lambda t: SystemClient(
                base_url="https://x", credential_key_id="k", token="t", transport=t
            ),
            lambda c: c.authenticated_probe("/health/live"),
        ),
        (
            lambda t: VerifierClient(
                base_url="https://x", credential_key_id="k", token="t", transport=t
            ),
            lambda c: c._json("POST", f"{UNIT}/release-artifacts", json={}),
        ),
        (
            lambda t: AnonymousClient(base_url="https://x", transport=t),
            lambda c: c.probe("/api/v1/machine-activation-candidates"),
        ),
    ],
    ids=["system", "verifier", "anonymous"],
)
def test_a_forbidden_request_never_reaches_the_transport(client: Any, call: Any) -> None:
    with pytest.raises(ForbiddenEndpointError):
        call(client(httpx.MockTransport(_never)))


def test_each_client_names_itself_and_only_the_credentialed_ones_carry_a_bearer() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": "completed"})

    transport = httpx.MockTransport(record)
    VerifierClient(
        base_url="https://x",
        credential_key_id="orchestrator-verifier",
        token="v",
        transport=transport,
    ).verify(ID, {})
    AnonymousClient(base_url="https://x", transport=transport).probe("/health/live")

    verify, probe = seen
    assert verify.headers["User-Agent"] == USER_AGENT == probe.headers["User-Agent"]
    assert verify.headers["Authorization"] == "Bearer v"
    assert verify.headers["X-Credential-Key-Id"] == "orchestrator-verifier"
    assert "Authorization" not in probe.headers


def test_a_refusal_reports_its_status_and_code_and_never_its_body() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "version_conflict", "message": "secret-ish echo"}}
        )

    client = VerifierClient(
        base_url="https://x",
        credential_key_id="k",
        token="t",
        transport=httpx.MockTransport(refuse),
    )
    with pytest.raises(ReleaseCallError) as raised:
        client.verify(ID, {})
    assert "409 (version_conflict)" in str(raised.value)
    assert "echo" not in str(raised.value)


def test_the_candidate_read_is_scoped_to_the_container_image_kind() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    SystemClient(
        base_url="https://x",
        credential_key_id="k",
        token="t",
        transport=httpx.MockTransport(record),
    ).candidates("AlobarQuest/orchestrator")
    assert dict(seen[0].url.params) == {
        "repository": "AlobarQuest/orchestrator",
        "kind": "container_image",
    }


@pytest.mark.parametrize("url", ["http://x", "https://a..b", "https://" + "a" * 64 + ".io"])
def test_an_unusable_base_url_is_refused_at_construction(url: str) -> None:
    with pytest.raises(UnusableEndpointError):
        AnonymousClient(base_url=url)


def test_a_transport_failure_is_a_call_error_carrying_only_its_type() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to Bearer t", request=request)

    client = AnonymousClient(base_url="https://x", transport=httpx.MockTransport(fail))
    with pytest.raises(ReleaseCallError) as raised:
        client.probe("/health/live")
    assert "ConnectError" in str(raised.value)
    assert "Bearer" not in str(raised.value)


def test_only_the_client_module_can_speak_http() -> None:
    speaks = {
        str(path.relative_to(PROGRAM))
        for path in PROGRAM.rglob("*.py")
        if {"httpx", "requests", "urllib.request", "http.client", "aiohttp", "estate_clients"}
        & file_import_names(path)
    }
    assert speaks == {"client.py"}
