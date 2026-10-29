"""`inert_lander` is a separate program that happens to live in this repository.

Same shape as the estate lander's, the watcher's and the ledger's isolation tests, and for the
same reason: hosting an out-of-process program here is a packaging choice, and the moment it can
import the orchestrator it stops being one.

**The bound is what makes a scheduled caller acceptable.** This program merges pull requests into
six repositories with nobody watching. What makes that acceptable is that it composes NOTHING:
every term is evaluated inside the orchestrator, in the transaction that records the act. A
program that could import the orchestrator's own admission module could answer the question it is
supposed to be asking, and the property would be gone.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from inert_lander.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    is_allowed_read,
    is_allowed_write,
)

# not-a-vocabulary: this lander's write routes, the cases the verb test runs over.
WRITE_PATHS = frozenset(
    {
        "/api/v1/inert-pr-merge",
        "/api/v1/inert-pr-branch-update",
        "/api/v1/held-pr-close",
    }
)


def test_the_write_surface_is_three_routes_and_no_more() -> None:
    """THREE, and the count is in the name so a fourth has to be written down rather than acquired.
    The third (bridge layer Part 4) closes a held bump with a comment.

    The second is much the smaller act: it brings a topic branch up to date with its base, where
    the first rewrites a default branch. It exists because this lane REQUIRES freshness, which is
    a tightening over the workflow it replaces -- and a requirement with nothing to satisfy it is
    a stall rather than a gate.
    """
    assert is_allowed_write("/api/v1/inert-pr-merge")
    assert is_allowed_write("/api/v1/inert-pr-branch-update")
    assert is_allowed_write("/api/v1/held-pr-close")
    for forbidden in (
        "/api/v1/inert-pr-merge/",
        "/api/v1/held-pr-close/",
        "/api/v1/inert-pr-branch-update/",
        "/api/v1/inert-pr-merge-admission",
        # THE SIBLING LANE'S ROUTES. Those land into repositories where landing changes something
        # already serving, under terms this program knows nothing about -- a change record, a
        # change window and a daily pace. Reaching them from here would be this lane acting on
        # the other lane's population with none of the other lane's conditions.
        "/api/v1/estate-pr-merge",
        "/api/v1/estate-pr-branch-update",
        "/api/v1/work-units/x/pr-merge",
        "/api/v1/work-units/x/dispatch",
        "/api/v1/observations",
        "/api/v1/package-intakes",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_read_surface_is_one_route_and_no_more() -> None:
    assert is_allowed_read("/api/v1/inert-pr-merge-admission")
    for forbidden in (
        "/api/v1/inert-pr-merge",
        # A write path is not thereby readable: the two allowlists are separate and each names
        # its own paths, so the branch-update route cannot be reached by a GET.
        "/api/v1/inert-pr-branch-update",
        "/api/v1/estate-pr-merge-admission",
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


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", sorted(WRITE_PATHS))
def test_put_patch_and_delete_on_a_write_path_are_refused(method: str, path: str) -> None:
    """The write allowlist is a POST allowlist: the method is part of the key, so another verb on
    a listed path never reaches the transport."""
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send(method, path, json={})
    assert seen == []


def test_the_admission_route_may_only_be_READ() -> None:
    """The method is part of the key. A template-only allowlist that permitted the read would hand
    over the write with it -- and here the two paths are one word apart."""
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._send("POST", "/api/v1/inert-pr-merge-admission", json={})
    assert seen == []


def test_a_close_names_this_lanes_own_literal_and_the_callers_key() -> None:
    """The lane is the program's own, so the orchestrator judges the pull request by this lane's
    admission and population and never by the other's."""
    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((f"{request.method} {request.url.path}", json.loads(request.content)))
        return httpx.Response(200, json={"commented": True})

    client = OrchestratorClient("t", "k", transport=httpx.MockTransport(handler))
    client.close_held(
        "alobarquest/x", 7, head_sha="h" * 40, idempotency_key="held-pr-close:alobarquest/x#7"
    )

    assert sent == [
        (
            "POST /api/v1/held-pr-close",
            {
                "lane": "inert",
                "repository": "alobarquest/x",
                "pull_request_number": 7,
                "expected_head_sha": "h" * 40,
                "idempotency_key": "held-pr-close:alobarquest/x#7",
            },
        )
    ]
