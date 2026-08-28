"""What the watcher actually puts on the wire, and what it accepts back (ADR-0029).

The CLI tests one file over substitute both clients wholesale, which is right for the decision
logic and leaves two things unexercised: the BODY the retirement composes, and the SHAPE guard on
the orchestrator's answer. Both are places where a mistake is silent rather than loud — a wrong
observation string is refused by change-manager as a 409 that reads like a subject problem, and a
missing verdict key would otherwise be read as `False`, which is "not done" rather than "I could
not tell", i.e. a wrong answer instead of a finding.

A `MockTransport` is used rather than a hand-written double, so the request under assertion is one
`httpx` genuinely built from the client's own arguments.
"""

from __future__ import annotations

import json

import httpx
import pytest

from work_watcher.change_manager import (
    RETIREMENT_ACTOR,
    WORK_UNIT_COMPLETED,
    ChangeManagerError,
    RetirementClient,
    RetirementRefused,
)
from work_watcher.orchestrator_client import OrchestratorClient, OrchestratorError


def _retirer(handler) -> RetirementClient:
    return RetirementClient(
        base_url="https://change-mgr.example",
        token="tok",
        client=httpx.Client(
            base_url="https://change-mgr.example", transport=httpx.MockTransport(handler)
        ),
    )


def _reader(handler) -> OrchestratorClient:
    return OrchestratorClient(
        "tok",
        "orchestrator-system",
        base_url="https://sds.example",
        client=httpx.Client(base_url="https://sds.example", transport=httpx.MockTransport(handler)),
    )


# --- the retirement body -----------------------------------------------------------------------


def test_the_retirement_names_the_fact_the_locator_and_the_mechanism() -> None:
    """Every field change-manager's route reads, asserted on the real request.

    The observation is the one member of that route's vocabulary; the locator is what lets the
    server refuse a retirement about a record this program did not observe; and the actor names
    the MECHANISM rather than a person, because `actor` there is caller-declared free text and an
    invented name would attest nothing.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 61, "status": "resolved"})

    result = _retirer(handler).retire(61, package_id="pkg-eslint", package_revision=3)

    assert result == {"id": 61, "status": "resolved"}
    assert len(seen) == 1
    assert seen[0].url.path == "/api/items/61/work-retirement"
    assert json.loads(seen[0].content) == {
        "observation": WORK_UNIT_COMPLETED,
        "package_id": "pkg-eslint",
        "package_revision": 3,
        "actor": RETIREMENT_ACTOR,
    }


def test_the_observation_is_the_one_the_route_accepts() -> None:
    """Pinned as a literal, not derived. change-manager's vocabulary has exactly this member, and
    a rename on either side must break here rather than at a 409 in production."""
    assert WORK_UNIT_COMPLETED == "work_unit_completed"


def test_a_refusal_is_reported_as_a_refusal_and_carries_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "item 61 is about pkg-other revision 1"})

    with pytest.raises(RetirementRefused) as error:
        _retirer(handler).retire(61, package_id="pkg-eslint", package_revision=3)

    assert "409" in str(error.value)
    assert "pkg-other" in str(error.value)


def test_a_redirect_is_a_refusal_and_not_a_json_fault() -> None:
    """ANY non-2xx, rather than `>= 400`. This service sits behind a proxy, and a redirect waved
    through to `.json()` reports a routing problem as a response-encoding fault every morning."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://id.example/"})

    with pytest.raises(RetirementRefused) as error:
        _retirer(handler).retire(61, package_id="pkg-eslint", package_revision=3)

    assert "302" in str(error.value)


def test_an_unreachable_service_names_the_exception_type_and_no_more() -> None:
    """An httpx error carries the REQUEST, and a diagnostic that prints what it was given is how
    a bearer token reaches a log."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    with pytest.raises(ChangeManagerError) as error:
        _retirer(handler).retire(61, package_id="pkg-eslint", package_revision=3)

    assert "ConnectError" in str(error.value)
    assert "tok" not in str(error.value)


# --- the orchestrator's answer -------------------------------------------------------------------


def _answer(**overrides) -> dict:
    body = {
        "change_record_id": 61,
        "revision_ids": ["11111111-1111-1111-1111-111111111111"],
        "units": [{"unit_id": "u", "unit_key": "k", "revision_id": "r", "state": "completed"}],
        "all_units_completed": True,
    }
    body.update(overrides)
    return body


def test_the_verdict_and_its_evidence_are_relayed_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/change-records/61/work"
        return httpx.Response(200, json=_answer())

    answer = _reader(handler).work_for(61)

    assert answer.all_units_completed is True
    assert answer.unit_states == ("completed",)
    assert answer.revision_count == 1


def test_an_absent_verdict_is_a_FINDING_and_not_a_false() -> None:
    """THE guard this file exists for.

    A `response_model` drops every key it does not declare, so a field that stopped being served
    arrives as ABSENCE rather than as an error. Read as `False` that is "the work is not done" —
    a confident wrong answer that quietly stops every retirement. Refusing turns a silently
    narrowed contract into something a person sees.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = _answer()
        del body["all_units_completed"]
        return httpx.Response(200, json=body)

    with pytest.raises(OrchestratorError) as error:
        _reader(handler).work_for(61)

    assert "61" in str(error.value)


def test_a_verdict_of_the_wrong_type_is_also_refused() -> None:
    """`"true"` is not `True`. A truthiness check here would retire on a string."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_answer(all_units_completed="true"))

    with pytest.raises(OrchestratorError):
        _reader(handler).work_for(61)


def test_absent_units_are_refused_even_when_the_verdict_is_present() -> None:
    """The evidence is half the contract. A verdict with no units to justify it is a narrowed
    response, and this program must not act on one."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = _answer()
        del body["units"]
        return httpx.Response(200, json=body)

    with pytest.raises(OrchestratorError):
        _reader(handler).work_for(61)


def test_a_non_2xx_answer_is_an_error_rather_than_an_empty_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    with pytest.raises(OrchestratorError) as error:
        _reader(handler).work_for(61)

    assert "503" in str(error.value)


def test_a_body_that_is_not_an_object_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(OrchestratorError):
        _reader(handler).work_for(61)
