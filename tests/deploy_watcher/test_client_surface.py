"""`deploy_watcher` is a separate program that happens to live in this repository.

Same shape as the landing ledger's surface tests, and for the same reason: hosting an
out-of-process program here is a packaging choice, and the moment it can import the orchestrator
it stops being one.

**ADR-0022 MADE IT A CLIENT OF THE ORCHESTRATOR, and this docstring used to say the lane had "no
orchestrator involvement at all".** That is now false and is corrected rather than deleted: a
rollout the watcher observes may belong to a work unit, and the traceability chain's observation
hop is unit-scoped. What has NOT changed is the property this module exists for — the watcher
still imports nothing from `orchestrator.*`, still speaks HTTP from outside the process, and its
orchestrator surface is one write and one read, both bounded here.

The import confinement this module used to assert -- nothing from the orchestrator, the
orchestrator nothing from here, and the dependency allowlist -- is now a row of
`tests/architecture/test_out_of_process_isolation.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from deploy_watcher.change_manager import (
    SUPERSEDING_ACTOR,
    ChangeManagerClient,
    ChangeManagerError,
    ForbiddenEndpointError,
    RefusedError,
    is_allowed_read,
    is_allowed_write,
)
from deploy_watcher.orchestrator import (
    ForbiddenEndpointError as OrchestratorForbiddenError,
)
from deploy_watcher.orchestrator import (
    OrchestratorClient,
    OrchestratorError,
)
from deploy_watcher.orchestrator import (
    is_allowed_read as orchestrator_read,
)
from deploy_watcher.orchestrator import (
    is_allowed_write as orchestrator_write,
)


def test_only_the_two_writes_are_allowed() -> None:
    assert is_allowed_write("/api/items/44/deploy-observation")
    assert is_allowed_write("/api/items/44/deploy-supersession")
    for forbidden in (
        "/api/items/44/deploy-observation/",
        "/api/items/44/deploy-supersession/",
        "/api/items/44/deploy-supersessions",
        "/api/items/44/deploy-retirement",
        "/api/items/../44/deploy-supersession",
        "/api/items/44/outcome",
        "/api/items/44/claim",
        "/api/items/44/handoff",
        "/api/items/44/approve",
        "/api/items/44/resolve",
        "/api/items/44/wontfix",
        "/api/sync",
        "/api/deploy-changes",
        "/api/items/44/deploy-observations",
        "/api/items/../44/deploy-observation",
    ):
        assert not is_allowed_write(forbidden), forbidden


def test_the_execution_lifecycle_stays_out_of_reach_and_that_is_a_DECISION() -> None:
    """Named separately from the omission above, because it is not an omission.

    Increment 1 closed `claim`/`outcome`/`handoff` to proposed sources so a deploying-merge
    change could never be handed to the 04:00 agent holding production Coolify tools. This
    program could not reach them even if that guard were lifted.
    """
    for path in ("/api/items/44/claim", "/api/items/44/outcome", "/api/items/44/handoff"):
        assert not is_allowed_write(path) and not is_allowed_read(path)


def test_the_decision_routes_stay_out_of_reach_and_that_is_also_a_DECISION() -> None:
    """Choosing a change's status is a judgment. This program states a fact (ADR-0022 amendment 1);
    change-manager's supersession route chooses the one outcome, and these stay a person's."""
    for verb in ("approve", "defer", "wontfix", "resolve", "reactivate"):
        assert not is_allowed_write(f"/api/items/44/{verb}")


def test_a_supersession_states_coordinates_and_no_status() -> None:
    """The wire body is the whole of what the watcher asserts, and the server forbids extras."""
    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.read())))
        return httpx.Response(200, json={"id": 44, "status": "resolved"})

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        answer = client.supersede(
            44, pull_request_number=46, superseding_run_id=7, superseding_commit="a" * 40
        )

    assert answer["status"] == "resolved"
    assert sent == [
        (
            "/api/items/44/deploy-supersession",
            {
                "pull_request_number": 46,
                "superseding_run_id": 7,
                "superseding_commit": "a" * 40,
                "actor": SUPERSEDING_ACTOR,
            },
        )
    ]


def test_the_read_surface_is_the_two_paths_the_pass_needs() -> None:
    assert is_allowed_read("/api/items")
    assert is_allowed_read("/api/items/44/deploy-observations")
    for forbidden in ("/api/items/", "/api/items/44", "/api/events", "/api/window-runs"):
        assert not is_allowed_read(forbidden), forbidden


def test_the_orchestrator_write_surface_is_one_endpoint() -> None:
    """The OBSERVER role's whole write surface, repeated in code so a second write is
    structurally unreachable rather than merely unwritten."""
    assert orchestrator_write("/api/v1/observations")
    for forbidden in (
        "/api/v1/observations/",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/dispatch",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/commands/ready",
        "/api/v1/work-units/1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f/verify",
        "/api/v1/estate-pr-merge",
        "/api/v1/package-intakes",
    ):
        assert not orchestrator_write(forbidden), forbidden


def test_the_orchestrator_read_surface_is_the_binding_and_nothing_else() -> None:
    """One path, and it is the one that CONFIRMS a commit trailer's claim.

    Everything else about a unit — its evidence pack, its brief, its admission answer — is
    somebody else's question. `…/pr-merge-admission` in particular is a LIVE answer that
    legitimately drifts, so re-asking it later manufactures findings out of ordinary change.
    """
    unit = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
    assert orchestrator_read(f"/api/v1/work-units/{unit}/history")
    for forbidden in (
        f"/api/v1/work-units/{unit}/history/",
        f"/api/v1/work-units/{unit}/evidence-pack",
        f"/api/v1/work-units/{unit}/pr-merge-admission",
        f"/api/v1/work-units/{unit}/runner-brief",
        "/api/v1/work-units/../1/history",
        "/api/v1/observations",
        "/api/v1/status-ledger",
    ):
        assert not orchestrator_read(forbidden), forbidden


def test_a_forbidden_orchestrator_read_never_reaches_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    with OrchestratorClient("t", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OrchestratorForbiddenError):
            client.unit_history("not-a-unit-id")
    assert seen == []


def _seen_client() -> tuple[ChangeManagerClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    return ChangeManagerClient("t", transport=httpx.MockTransport(handler)), seen


def test_a_forbidden_write_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client.observe(-1, {})
    assert seen == []


def test_a_forbidden_supersession_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client.supersede(
            -1, pull_request_number=46, superseding_run_id=7, superseding_commit="a" * 40
        )
    assert seen == []


def test_a_forbidden_read_never_reaches_the_transport() -> None:
    client, seen = _seen_client()
    with pytest.raises(ForbiddenEndpointError):
        client._get("/api/events")
    assert seen == []


def test_the_listing_always_names_the_source() -> None:
    """Increment 1's withholding guard makes an unnamed source answer with a clean empty list.

    A watcher that forgot would report "0 deploying-merge changes to watch" and exit 0 — the
    guard that protects the executor turning into the thing that hides the watcher's whole
    subject. Asserted on the wire, because that is where it is true.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    client = ChangeManagerClient("t", transport=httpx.MockTransport(handler))
    client.deploy_changes()
    assert seen == ["https://change-mgr.alobar.net/api/items?source=deploy"]


def test_the_listing_carries_each_records_status() -> None:
    """A terminal record is quiet, or finished, only because the listing says what it is."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": 78, "status": "resolved", "decided_by": SUPERSEDING_ACTOR}]
        )

    with ChangeManagerClient("t", transport=httpx.MockTransport(handler)) as client:
        [record] = client.deploy_changes()

    assert record.status == "resolved"
    assert record.decided_by == SUPERSEDING_ACTOR


@pytest.mark.parametrize("base_url", ["https://sds..alobar.net", "https://" + "a" * 64 + ".net"])
def test_a_malformed_address_is_reported_as_the_address(base_url: str) -> None:
    """A typo that only fails at request time must not read as the orchestrator being down."""
    with pytest.raises(OrchestratorError, match="malformed host"):
        OrchestratorClient("t", base_url=base_url)


# ---------------------------------------------------------------------------
# A request that gets no answer is repeated, three attempts in all
# ---------------------------------------------------------------------------

OBSERVATION_PATH = "/api/items/70/deploy-observation"


def _flaky_changes(
    answer: httpx.Response, *, failures: int
) -> tuple[ChangeManagerClient, list[str], list[float]]:
    """A client whose first `failures` requests are reset before an answer, then answer."""
    seen: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if len(seen) <= failures:
            raise httpx.ReadError("connection reset", request=request)
        return answer

    client = ChangeManagerClient("t", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return client, seen, sleeps


def test_a_read_that_got_no_answer_is_repeated_and_answers() -> None:
    client, seen, sleeps = _flaky_changes(httpx.Response(200, json=[{"id": 70}]), failures=1)
    [record] = client.deploy_changes()
    assert record.item_id == 70
    assert seen == ["GET /api/items", "GET /api/items"]
    assert len(sleeps) == 1


def test_an_observation_that_got_no_answer_is_repeated_and_answers() -> None:
    """change-manager answers a replayed observation 200 with the row it already holds."""
    client, seen, _ = _flaky_changes(
        httpx.Response(200, json={"id": 9, "item_status": "resolved"}), failures=2
    )
    assert client.observe(70, {"run_id": 1})["item_status"] == "resolved"
    assert seen == [f"POST {OBSERVATION_PATH}"] * 3


def test_a_supersession_that_got_no_answer_is_repeated_and_answers() -> None:
    """A supersession of a record already terminal answers 200 unchanged, so it replays too."""
    client, seen, _ = _flaky_changes(httpx.Response(200, json={"id": 70}), failures=1)
    item = client.supersede(
        70, pull_request_number=46, superseding_run_id=2, superseding_commit="b" * 40
    )
    assert item == {"id": 70}
    assert seen == ["POST /api/items/70/deploy-supersession"] * 2


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda client: client.deploy_changes(),
            "change-manager is unreachable for GET /api/items: ReadError",
        ),
        (
            lambda client: client.observe(70, {"run_id": 1}),
            f"change-manager is unreachable for POST {OBSERVATION_PATH}: ReadError",
        ),
    ],
)
def test_three_requests_without_an_answer_raise_the_unreachable_error(call, message) -> None:
    client, seen, sleeps = _flaky_changes(httpx.Response(200, json={}), failures=3)
    with pytest.raises(ChangeManagerError) as raised:
        call(client)
    assert str(raised.value) == message
    assert len(seen) == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize(("status", "error"), [(409, RefusedError), (422, ChangeManagerError)])
def test_an_answered_refusal_is_never_repeated(status: int, error: type[Exception]) -> None:
    client, seen, sleeps = _flaky_changes(httpx.Response(status, json={"detail": "no"}), failures=0)
    with pytest.raises(error):
        client.observe(70, {"run_id": 1})
    assert seen == [f"POST {OBSERVATION_PATH}"]
    assert sleeps == []


def test_a_gateway_answer_to_a_write_is_not_repeated() -> None:
    """Only a request with NO answer repeats; a 502 is an answer and keeps its handling."""
    client, seen, _ = _flaky_changes(httpx.Response(502, text="bad gateway"), failures=0)
    with pytest.raises(ChangeManagerError, match="rejected POST .*: 502"):
        client.observe(70, {"run_id": 1})
    assert len(seen) == 1
