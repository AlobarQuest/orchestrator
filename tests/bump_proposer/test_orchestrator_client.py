"""The producer's orchestrator surface: ONE path, and the guard fires before the transport.

The server-side control is the OBSERVER bearer, whose entire write surface is this route. The
allowlist here is the statement of intent that makes a mistake in this program fail before a
request leaves it, and keeps the bound true in a development deployment where that credential is
unset.
"""

from __future__ import annotations

import httpx
import pytest

from bump_proposer.orchestrator_client import (
    ForbiddenEndpointError,
    ObservationCredentialError,
    ObservationWriteError,
    OrchestratorClient,
    UnusableEndpointError,
    is_allowed_write,
    open_client,
)


def _client(handler) -> OrchestratorClient:
    return OrchestratorClient(
        base_url="https://sds.example.net",
        credential_key_id="orchestrator-observer",
        token="t",
        transport=httpx.MockTransport(handler),
    )


def test_the_write_surface_is_the_observer_roles_whole_write_surface_and_no_more() -> None:
    assert is_allowed_write("/api/v1/observations")
    for forbidden in (
        "/api/v1/observations/",
        "/api/v1/deployment-observations",
        "/api/v1/package-intakes",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/pr-merge",
        "../api/v1/observations",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    """Refused before a request is built, so the guard cannot be satisfied by a 404 downstream."""
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.url.path)
        return httpx.Response(201, json={})

    with pytest.raises(ForbiddenEndpointError):
        _client(handler)._post("/api/v1/package-intakes", {})
    assert reached == []


def test_the_recorded_id_is_answered_back() -> None:
    row = {"id": "3f1d2a9c-0000-4000-8000-000000000001"}

    assert _client(lambda r: httpx.Response(201, json=row)).record_observation({}) == row["id"]


def test_a_body_without_an_id_is_REFUSED_rather_than_read_as_no_cause() -> None:
    """A FastAPI `response_model` DROPS every key it does not declare, so a field that stopped
    being served arrives as absence rather than as an error. Absence read as "no cause" would let
    this producer propose a record naming nothing, which is the dangling cause the contract's
    ordering exists to prevent -- so it is the answer, and it is refused rather than defaulted.
    """
    for body in ({}, {"id": None}, {"id": ""}, {"source_system": "bump_proposer"}):
        with pytest.raises(ObservationWriteError, match="did not say which observation"):
            _client(lambda r, b=body: httpx.Response(201, json=b)).record_observation({})


def test_a_rejected_write_carries_the_status_and_nothing_else() -> None:
    """A rejection body echoes the command back; printing it is how a secret reaches a log.

    409 rather than 403 deliberately: an auth status is no longer a per-bump write error at all
    (see below), so using one here would assert this property against the wrong branch.
    """
    with pytest.raises(ObservationWriteError) as raised:
        _client(lambda r: httpx.Response(409, json={"echo": "sensitive"})).record_observation({})

    assert "409" in str(raised.value)
    assert "sensitive" not in str(raised.value)


def test_a_REFUSED_CREDENTIAL_is_not_a_per_bump_failure() -> None:
    """Behaviour rather than taxonomy, and the consequence is what makes it matter.

    A per-bump error reports the bump `unobserved` and the pass exits with this lane's FINDING
    code -- which `sds-deadman.sh` pings as SUCCESS, because a declared finding code means the
    pass ran and reported. A revoked bearer is not one bad bump, it is every bump, so absorbing
    it per pull request would leave the lane proposing nothing while its check read `up`.
    """
    assert not issubclass(ObservationCredentialError, ObservationWriteError)

    for status in (401, 403):
        with pytest.raises(ObservationCredentialError, match=str(status)):
            _client(lambda r, s=status: httpx.Response(s, json={})).record_observation({})


def test_the_orchestrators_OWN_error_code_is_named_and_the_rest_of_the_body_is_not() -> None:
    """An undeployed vocabulary and a fact that moved under a frozen row are the same NUMBER and
    different acts, so the status alone cannot tell a reader which one happened."""
    body = {"error": {"code": "observation_conflict", "message": "echoed command"}}
    with pytest.raises(ObservationWriteError) as raised:
        _client(lambda r: httpx.Response(409, json=body)).record_observation({})

    assert "observation_conflict" in str(raised.value)
    assert "echoed command" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(409, text="<html/>"),
        httpx.Response(409, json=["not", "an", "object"]),
        httpx.Response(409, json={"error": "a string, not an object"}),
        httpx.Response(409, json={"error": {"code": "Not A Safe Code"}}),
        httpx.Response(409, json={"error": {"code": "x" * 200}}),
    ],
)
def test_an_unreadable_or_UNSAFE_error_body_contributes_nothing(response: httpx.Response) -> None:
    """The code is MATCHED rather than trusted, so a server answering something else cannot put
    arbitrary text into this lane's log. Each of these still raises, carrying the status alone."""
    with pytest.raises(ObservationWriteError) as raised:
        _client(lambda r, x=response: x).record_observation({})

    assert str(raised.value).endswith("409")


def test_a_redirect_is_named_as_one_rather_than_read_as_a_broken_body() -> None:
    """`>= 400` would hand a redirect body to `response.json()` and report a routing refusal as a
    response-encoding fault. This route is not behind the forward-auth chain today, but a proxy is
    a thing that gets reconfigured."""
    with pytest.raises(ObservationWriteError, match="302"):
        _client(lambda r: httpx.Response(302, text="<html/>")).record_observation({})


def test_an_unreachable_orchestrator_is_this_modules_error_not_a_bare_httpx_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ObservationWriteError, match="unreachable"):
        _client(handler).record_observation({})


@pytest.mark.parametrize(
    "url",
    ["https://host..example", "http://x.example", "https://", "x", "https://" + "a" * 70 + ".test"],
)
def test_an_unusable_url_is_refused_before_a_client_exists(url: str) -> None:
    """`httpx` refuses some malformed URLs at the CONSTRUCTOR and others at REQUEST time, so a
    guard on one half is not a guard -- a doubled dot and an over-long DNS label both construct
    cleanly and raise `UnicodeError` from IDNA encoding when a request is made. Refusing here is
    also what makes an operator's typo stop the pass as an unusable input rather than costing
    every pull request its row one at a time.
    """
    with pytest.raises(UnusableEndpointError):
        open_client(base_url=url, credential_key_id="k", token="t")


def test_a_usable_url_still_opens() -> None:
    """The control that keeps the guard above from being satisfied by refusing everything."""
    with open_client(base_url="https://sds.example.net", credential_key_id="k", token="t") as c:
        assert c is not None


def test_an_unusable_url_is_NOT_a_per_bump_failure() -> None:
    """The two error families carry different exit codes, and here that is behaviour rather than
    taxonomy: one costs a pull request its row, the other stops the pass."""
    assert not issubclass(UnusableEndpointError, ObservationWriteError)
