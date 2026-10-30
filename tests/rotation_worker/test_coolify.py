"""The Coolify client's writes: an application's two rows, a service's one."""

from __future__ import annotations

import json
from typing import Literal

import httpx
import pytest

from rotation_worker.contract import ContractError
from rotation_worker.coolify import CoolifyClient
from rotation_worker.values import Value

URL = "https://coolify.example.test"
UUID = "svc00000000000000000000"


def build(kind: Literal["application", "service"], seen: list[httpx.Request]) -> CoolifyClient:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    return CoolifyClient(
        URL,
        Value("throwaway-coolify-token"),
        kind,
        UUID,
        httpx.MockTransport(answer),
    )


def test_service_write_sends_one_row() -> None:
    seen: list[httpx.Request] = []
    build("service", seen).set_variable("POSTGRES_PASSWORD", "v")
    assert [r.url.path for r in seen] == [f"/api/v1/services/{UUID}/envs"]
    assert json.loads(seen[0].content) == {"key": "POSTGRES_PASSWORD", "value": "v"}


def test_application_write_still_sends_two_rows() -> None:
    seen: list[httpx.Request] = []
    build("application", seen).set_variable("K", "v")
    assert [json.loads(r.content)["is_preview"] for r in seen] == [True, False]
    assert {r.url.path for r in seen} == {f"/api/v1/applications/{UUID}/envs"}


def test_an_unknown_resource_type_is_refused() -> None:
    with pytest.raises(ContractError, match="application or a service"):
        CoolifyClient(URL, Value("throwaway-coolify-token"), "database", UUID)  # type: ignore[arg-type]


def test_permits_is_exact_for_a_service() -> None:
    client = build("service", [])
    assert client.permits("PATCH", f"/api/v1/services/{UUID}/envs")
    assert client.permits("POST", f"/api/v1/services/{UUID}/restart")
    for method, path in [
        ("PATCH", f"/api/v1/applications/{UUID}/envs"),
        ("PATCH", "/api/v1/services/other/envs"),
        ("GET", f"/api/v1/services/{UUID}/envs"),
        ("DELETE", f"/api/v1/services/{UUID}/envs"),
        ("POST", f"/api/v1/services/{UUID}/stop"),
    ]:
        assert not client.permits(method, path)
    with pytest.raises(ContractError, match="may not"):
        client._send("DELETE", f"/api/v1/services/{UUID}/envs")
