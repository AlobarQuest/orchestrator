"""The shared confined transport: permit first, three exception families, nothing leaked."""

from __future__ import annotations

import httpx
import pytest

from estate_clients.confined import (
    TRANSPORT_ERRORS,
    ConfinedClient,
    TransportFailure,
    base_url_problem,
    error_code,
    error_detail,
)


class Refused(Exception):
    pass


def _client(
    handler: httpx.MockTransport | None = None,
    *,
    base_url: str = "https://service.example",
    permits: bool = True,
) -> ConfinedClient:
    return ConfinedClient(
        base_url=base_url,
        headers={"Authorization": "Bearer secret-token"},
        timeout=5.0,
        permits=lambda _method, _path: permits,
        refuse=lambda method, path: Refused(f"{method} {path}"),
        transport=handler,
    )


def test_a_refused_request_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200)

    client = _client(httpx.MockTransport(handler), permits=False)
    with pytest.raises(Refused, match="POST /x"):
        client.request("POST", "/x")
    assert seen == []


def test_a_permitted_request_is_sent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(204)

    response = _client(httpx.MockTransport(handler)).request("GET", "/x")
    assert response.status_code == 204
    assert seen == ["GET /x"]


def test_a_transport_failure_carries_only_the_exception_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom with secret-token", request=request)

    with pytest.raises(TransportFailure) as caught:
        _client(httpx.MockTransport(handler)).request("GET", "/x")
    assert caught.value.error_type == "ConnectError"
    assert "secret-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("base_url", ["https://host..example", "https://" + "a" * 64 + ".example"])
def test_a_malformed_host_fails_at_request_time_through_the_real_transport(base_url: str) -> None:
    """The third member of TRANSPORT_ERRORS, measured where it actually fires.

    A mock transport never encodes a host, so only the real one can show that these raise a
    `ValueError` that is neither of the other two families. The control proves the premise: the
    raw library escapes both of them.
    """
    with pytest.raises(ValueError) as raw:
        httpx.Client(base_url=base_url).get("/x")
    assert not isinstance(raw.value, (httpx.HTTPError, httpx.InvalidURL))

    with pytest.raises(TransportFailure):
        _client(base_url=base_url).request("GET", "/x")


def test_a_url_refused_at_construction_is_a_transport_failure() -> None:
    with pytest.raises(TransportFailure):
        _client(base_url="https://host\x00.example")


def test_the_three_families_are_exactly_these() -> None:
    assert TRANSPORT_ERRORS == (httpx.HTTPError, httpx.InvalidURL, ValueError)


@pytest.mark.parametrize(
    ("base_url", "problem"),
    [
        ("https://sds.alobar.net", None),
        ("http://sds.alobar.net", "must be https with a host"),
        ("https://", "must be https with a host"),
        ("https://host..example", "has a malformed host"),
        ("https://" + "a" * 64 + ".example", "has a malformed host"),
        ("https://" + "a" * 63 + ".example", None),
    ],
)
def test_base_url_problem(base_url: str, problem: str | None) -> None:
    assert base_url_problem(base_url) == problem


def test_error_detail_and_code_read_the_nested_domain_error() -> None:
    response = httpx.Response(409, json={"error": {"code": "x_refused", "message": "no"}})
    assert error_detail(response) == "no"
    assert error_code(response) == "x_refused"


def test_error_detail_bounds_the_domain_message() -> None:
    response = httpx.Response(409, json={"error": {"code": "x", "message": "m" * 500}})
    assert error_detail(response) == "m" * 400


def test_error_detail_reads_the_framework_shape_and_bounds_it() -> None:
    response = httpx.Response(404, json={"detail": "n" * 500})
    assert error_detail(response) == "n" * 400
    assert error_code(response) == ""


def test_an_unreadable_body_yields_the_status_and_no_code() -> None:
    response = httpx.Response(502, text="<html>proxy</html>")
    assert error_detail(response) == "HTTP 502"
    assert error_code(response) == ""
