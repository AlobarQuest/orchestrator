"""The producer's change-manager surface: two paths, and the guard fires before the transport.

The server-side scope is the control -- change-manager refuses this credential every
status-moving route whatever is sent. The allowlist here is the statement of intent that makes
a mistake in this program fail before a request leaves it, and keeps the bound true in a
development deployment where the narrow credential is unset.
"""

from __future__ import annotations

import httpx
import pytest

from bump_proposer.change_manager import (
    ChangeManagerClient,
    ChangeManagerError,
    ForbiddenEndpointError,
    ProposalRefused,
    is_allowed_read,
    is_allowed_write,
)


def test_the_write_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_write("/api/work-changes")
    for forbidden in (
        "/api/work-changes/",
        "/api/work-changes/1",
        "/api/deploy-changes",
        "/api/items/1/approve",
        "/api/items/1/claim",
        "/api/items/1/deploy-retirement",
        "/api/sync",
        "../api/work-changes",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_read_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_read("/api/items")
    for forbidden in ("/api/items/", "/api/items/1", "/api/work-changes", "/api/deploy-policy"):
        assert not is_allowed_read(forbidden), forbidden


def _seen_client():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    return ChangeManagerClient("t", transport=httpx.MockTransport(handler)), seen


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send("POST", "/api/items/1/approve", json={})
    assert seen == []


def test_the_proposal_route_may_not_be_read() -> None:
    """The method is part of the key: a template-only allowlist that permitted the read would
    hand over the write with it."""
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send("GET", "/api/work-changes")
    assert seen == []


def test_a_created_record_is_distinguished_from_a_replayed_one() -> None:
    """201 means this pass proposed it; 200 means an identical proposal already stood. The
    difference is the whole of what makes a scheduled re-run safe."""
    codes = iter([201, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes), json={"id": 7, "status": "pending"})

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        _record, created = client.propose({})
        assert created is True
        _record, created = client.propose({})
        assert created is False


def test_a_conflicting_proposal_is_a_named_refusal_carrying_the_detail() -> None:
    """A 409 on a write-once record is permanent, so an operator who cannot see WHICH fact
    differs has no way to act on it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "it differs in: reasoning"})

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProposalRefused, match="it differs in: reasoning"):
            client.propose({})


def test_the_listing_names_the_work_source() -> None:
    """change-manager WITHHOLDS a proposed source from a caller that does not name one, so a
    read that forgot this answers a clean empty list and reports that nothing was proposed."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        client.work_records()
    assert "source=work" in seen[0]


def test_a_listing_that_is_not_a_list_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"detail": "nope"})

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ChangeManagerError, match="did not answer a list"):
            client.work_records()


@pytest.mark.parametrize("base_url", ["https://host..example", "https://" + "a" * 70 + ".test"])
def test_a_malformed_base_url_is_a_named_refusal_not_a_traceback(base_url) -> None:
    """Construction raises for some malformed URLs and request time for others, and the split
    is not obvious. Both are guarded, because an environment-variable typo must report a
    finding rather than kill a scheduled pass with a traceback."""
    try:
        client = ChangeManagerClient("t", base_url=base_url)
    except ChangeManagerError:
        return
    with client:
        with pytest.raises(ChangeManagerError):
            client.work_records()


def test_a_403_names_the_credential_rather_than_the_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ChangeManagerError, match="not scoped for this route"):
            client.work_records()
