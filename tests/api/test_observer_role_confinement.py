"""WS-P3.6 Increment 1: an OBSERVER may record an observation and nothing else.

Phase 3's exit criterion 3 asks for this **by negative test, not by source scan**, and the
distinction is the whole point: a scan proves a producer's CODE does not call a route, and can
never prove its CREDENTIAL could not. So every refusal here is driven through the production
authentication chain -- a real bearer token, `authenticate_m2m` (which returns WORKER for every
machine credential), then the `m2m_roles` promotion that is where the role actually comes from.

The surfaces were not taken from a list. They are DERIVED from the application's own POST route
inventory, so the set under test cannot fall behind the routes that exist: a route added tomorrow
joins this test automatically, and if it were somehow reachable by an observer this test reds.

Two layers refuse an observer, and they are tested separately on purpose:

  1. `api/dependencies.py::_confine_observer` -- the route-level choke point. Total, positive,
     and the reason the parametrized test below can cover every route at once.
  2. the service-level role allowlists (`observations`, `decomposition`, `web._human`, the
     kernel's `EDGE_ROLES`) -- unchanged by this increment, and each still independently correct.

Because they are layered, disabling either one alone leaves the other refusing. A test that only
drove HTTP would therefore stay green under a broken service guard, and vice versa -- which is
why the service-level guards are also exercised directly, below the HTTP layer.
"""

import uuid

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from orchestrator.api.dependencies import OBSERVER_WRITE_ROUTES, _confine_observer
from orchestrator.api.routes import router as api_router
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition
from orchestrator.services.decomposition import _require_submission_actor
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import _authorize_actor
from orchestrator.web import _human
from orchestrator.web import router as web_router
from tests.api.test_observations_api import observation_body

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

# Any syntactically plausible value. The refusal under test happens in the actor dependency, so
# these never have to identify a real row -- and a test that started 422ing here would be telling
# us the choke point had moved BELOW request validation, which is a real regression, not noise.
_PATH_VALUES = {
    "attempt": "1",
    "command": "ready",
}


def _post_routes() -> set[str]:
    """The application's own POST inventory, derived the way `tests/idempotency/test_matrix.py`
    derives it. Never a second copy of the literal in `tests/architecture/test_scope_guards.py`:
    a hand-maintained duplicate is one more thing that can silently fall behind."""
    routes = {path for path, operations in _openapi()["paths"].items() if "post" in operations}
    routes.update(
        route.path
        for route in (*api_router.routes, *web_router.routes)
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    )
    return routes


def _openapi() -> dict:
    from orchestrator.main import create_app

    return create_app().openapi()


def _concrete(path: str) -> str:
    out = path
    while "{" in out:
        head, _, rest = out.partition("{")
        name, _, tail = rest.partition("}")
        out = head + _PATH_VALUES.get(name, str(uuid.uuid4())) + tail
    return out


CONFINED_ROUTES = sorted(_post_routes() - OBSERVER_WRITE_ROUTES)


# --------------------------------------------------------------------------------------------
# The confinement, stated over the whole route surface.
# --------------------------------------------------------------------------------------------


def test_the_write_allowlist_is_a_real_and_complete_slice_of_the_route_inventory() -> None:
    """Gated both ways.

    Every allowlisted route must actually exist (an allowlist entry for a route that has been
    renamed silently widens nothing but reads as though it permits something), and the confined
    set must be exactly the rest -- so a new POST route cannot appear without landing in
    `CONFINED_ROUTES` and being tested below.
    """
    inventory = _post_routes()

    assert OBSERVER_WRITE_ROUTES <= inventory
    assert set(CONFINED_ROUTES) | OBSERVER_WRITE_ROUTES == inventory
    assert not set(CONFINED_ROUTES) & OBSERVER_WRITE_ROUTES
    # The one thing an observer may write is the thing this role exists for.
    assert OBSERVER_WRITE_ROUTES == {"/api/v1/observations"}


@pytest.mark.parametrize("path", CONFINED_ROUTES)
def test_an_observer_is_refused_at_every_post_route_but_observations(
    db_client: TestClient, path: str
) -> None:
    """The deliverable. Not four hand-picked surfaces -- every POST route the application serves.

    The body is empty deliberately: the refusal must happen in the actor dependency, ABOVE request
    validation, or an observer would be distinguishable from a malformed request only by luck. A
    422 here would mean the guard sits below body validation and the assertion would fail loudly.
    """
    response = db_client.post(_concrete(path), headers=OBSERVER, json={})

    assert response.status_code == 403, (path, response.status_code, response.text)
    assert response.json()["error"]["code"] == "role_forbidden"


def test_an_observer_records_an_observation(db_client: TestClient) -> None:
    """The positive half. Without it the test above is satisfied by a role that can do nothing at
    all, which would pass while the increment had shipped a useless credential."""
    response = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=observation_body(key="observer-records")
    )

    assert response.status_code == 201
    assert response.json()["source_system"] == "github"


def test_the_observation_is_attributed_to_the_observer_identity(db_client: TestClient) -> None:
    """`agent_id` attribution is permanent, so the row must carry the observer -- not the system
    actor it used to have to borrow. This is the whole reason the role exists (WS-P3.6)."""
    created = db_client.post(
        "/api/v1/observations", headers=OBSERVER, json=observation_body(key="observer-attributed")
    )
    listed = db_client.get("/api/v1/observations", headers=OBSERVER)

    assert created.status_code == 201
    assert created.json()["recorded_by"] == "observer"
    assert listed.status_code == 200


def test_an_observer_still_reads_a_route_it_may_not_write(db_client: TestClient) -> None:
    """Pins the deliberate decision recorded in `OBSERVER_WRITE_ROUTES`: reads are NOT confined.

    The route matters, and an earlier version of this test got it wrong. Reading
    `/api/v1/observations` proves nothing: that path is in the write allowlist, so it is permitted
    whether or not the method check exists, and the mutation that deletes the method check
    SURVIVED against it. The read has to be of a route the observer may NOT post to, which is the
    only case the method check is what permits.

    The route must also be one with no role gate of its OWN, or the read is refused for an
    unrelated reason and the test again proves nothing. `/api/v1/in-flight-units` is the trap
    here: it is operator-only (`services/lifecycle.py` admits SYSTEM and HUMAN alone), so an
    observer is refused there exactly as a worker or verifier is -- by a pre-existing guard, not
    by this one. `/api/v1/status-ledger` carries no role gate, which is what makes it a reading
    of the method check and nothing else.

    If a later change narrows observer reads, that is a policy decision and belongs to whoever
    makes it -- not to a producer discovering it in production.
    """
    assert "/api/v1/status-ledger" not in OBSERVER_WRITE_ROUTES

    response = db_client.get("/api/v1/status-ledger", headers=OBSERVER)

    assert response.status_code == 200


# --------------------------------------------------------------------------------------------
# The four surfaces the increment names, driven with WELL-FORMED bodies.
#
# The parametrized test above sends `{}`, so on its own it could not distinguish "refused for
# being an observer" from "refused for being malformed". These send bodies a system, verifier or
# human actor would have had accepted.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/work-units/{unit_id}/commands/ready", {"expected_version": 0}),
        ("/api/v1/work-units/{unit_id}/dispatch", {"expected_version": 0}),
        (
            "/api/v1/work-units/{unit_id}/approvals",
            {
                "subject_type": "authority",
                "subject_revision_or_fingerprint": "a" * 64,
                "expected_version": 0,
                "idempotency_key": "observer-approval",
            },
        ),
        ("/api/v1/work-units/{unit_id}/verify", {"expected_version": 0}),
    ],
    ids=["commands-ready", "workflow-handoff", "approval", "verify"],
)
def test_an_observer_is_refused_at_the_named_surfaces_with_a_well_formed_body(
    db_client: TestClient, path: str, body: dict
) -> None:
    response = db_client.post(
        path.replace("{unit_id}", str(uuid.uuid4())), headers=OBSERVER, json=body
    )

    assert response.status_code == 403, (path, response.status_code, response.text)
    assert response.json()["error"]["code"] == "role_forbidden"


# --------------------------------------------------------------------------------------------
# The service-level guards, exercised BELOW the HTTP choke point.
#
# These are what refuse an observer if the choke point is ever removed or bypassed -- an
# in-process caller, a future internal route, a service invoked by another service. Testing them
# only through HTTP would mean testing the choke point twice and these not at all.
# --------------------------------------------------------------------------------------------


def _actor(role: ActorRole) -> ActorContext:
    return ActorContext("observer", role)


def test_the_observation_service_admits_exactly_the_observer_and_the_system_actor() -> None:
    _authorize_actor(_actor(ActorRole.OBSERVER))
    _authorize_actor(_actor(ActorRole.SYSTEM))

    for refused in (ActorRole.WORKER, ActorRole.VERIFIER, ActorRole.HUMAN):
        with pytest.raises(DomainError) as error:
            _authorize_actor(_actor(refused))
        assert error.value.code == "role_forbidden"


def test_an_observer_holds_no_lifecycle_edge_whatsoever() -> None:
    """The strongest single statement of the confinement, and it costs one assertion.

    Every lifecycle command in the system routes through `authorize_transition`. OBSERVER appears
    in none of the four edge sets, so it is refused on all of them -- `commands/ready` is simply
    the instance of this that the increment happens to name.
    """
    guards = TransitionGuards(
        approval_recorded=True, completion_satisfied=True, submission_binding_recorded=True
    )

    assert not any(ActorRole.OBSERVER in roles for roles in EDGE_ROLES.values())
    for edge in EDGE_ROLES:
        with pytest.raises(DomainError) as error:
            authorize_transition(*edge, ActorRole.OBSERVER, guards)
        assert error.value.code == "role_forbidden"


def test_an_observer_may_not_author_work_by_proposing_a_breakdown() -> None:
    """A surface the increment's brief does not name, found by reading every role gate rather
    than the handoff: `_require_submission_actor` was authored holding EVERY member of
    `ActorRole`, so it read as "the role does not matter here". That made it the one gate a new
    role could have joined by assumption. Proposing a breakdown authors work units, and an
    observe-and-report identity must not be able to introduce work on the strength of what it saw.
    """
    _require_submission_actor(_actor(ActorRole.SYSTEM))

    with pytest.raises(DomainError) as error:
        _require_submission_actor(_actor(ActorRole.OBSERVER))
    assert error.value.code == "role_forbidden"


def test_an_observer_is_not_a_human_reviewer() -> None:
    """`/review` is gated by `_human`, not by the choke point's allowlist. Both refuse; this pins
    the one that would still refuse if the other were removed."""
    with pytest.raises(DomainError) as error:
        _human(_actor(ActorRole.OBSERVER))
    assert error.value.code == "human_actor_required"


def test_a_request_with_no_matched_route_is_refused() -> None:
    """The unknown case refuses, which nothing else in this file pins.

    `_confine_observer` reads the matched route off `request.scope` and compares the
    template against the allowlist. Its docstring claims an unmatched route "yields None,
    which is not in the allowlist -- the unknown case refuses", and that claim survived a
    mutation: rewriting the comparison as `matched is not None and matched not in ...`
    left all 61 tests in this file green (found in review, 2026-08-07).

    The branch is probably unreachable over HTTP -- FastAPI resolves dependencies only
    after routing, so a request that matches nothing 404s before this runs. That is
    exactly why no route-driven test reaches it, and exactly why the assertion belongs
    here instead: a defensive branch nobody can execute still has to mean what it says, or
    it is decoration that reads as protection. Asserted directly against the function, so
    reachability is not the question.
    """
    request = Request({"type": "http", "method": "POST", "headers": []})
    with pytest.raises(DomainError) as raised:
        _confine_observer(request)
    assert raised.value.code == "role_forbidden"


def test_a_read_with_no_matched_route_is_still_allowed() -> None:
    """Reads are exempt from the route check, and that exemption must survive.

    The obvious wrong way to kill the mutation above is to delete the safe-method
    short-circuit so every request consults the allowlist. That refuses reads this role is
    meant to have, and reads are deliberately unconfined (see OBSERVER_WRITE_ROUTES).

    NOTE on what this does NOT pin: merely reordering the `matched = ...` assignment
    relative to the short-circuit is not a behaviour change, because the lookup is pure.
    A mutation that only moves it survives, correctly. The behaviour worth pinning is the
    exemption itself, which is what this asserts.
    """
    _confine_observer(Request({"type": "http", "method": "GET", "headers": []}))
