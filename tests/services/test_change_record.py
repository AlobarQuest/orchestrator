"""Reading the estate's change record over HTTP (ADR-0019 Increment 3).

Two properties this file exists to hold, both of which the admission tests structurally cannot see
because they inject a double.

**Nothing raises.** Every failure -- an unset setting, a refusal, a timeout, a malformed URL, a
body that is not what it should be -- has to come back as an answer that says it has none. An
escaping HTTP exception would surface as a bare 500 from a route, because only `DomainError` and
`APIAuthenticationError` have registered handlers, and a gate that 500s is one that has stopped
deciding.

**What goes on the wire is asserted, not assumed.** The transport is injectable for exactly this
reason: the pipeline filter is mandatory (change-manager returns nothing from that pipeline unless
it is named), and a lookup that forgot it would answer "there is no record" about every record
there is -- silently, and in the permissive-looking direction.
"""

from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.services.change_record import (
    RECORD_AMBIGUOUS,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    HttpChangeRecordSource,
)

PIPELINE = "deploy"
REPOSITORY = "AlobarQuest/change-manager"


def _row(**overrides: object) -> dict[str, object]:
    return {
        "id": 44,
        "status": "approved",
        "source": PIPELINE,
        "target_repository": REPOSITORY,
        "pull_request_number": 42,
    } | overrides


def _source(handler) -> HttpChangeRecordSource:
    return HttpChangeRecordSource(
        base_url="https://change-mgr.example",
        token="a-bearer",
        pipeline=PIPELINE,
        transport=httpx.MockTransport(handler),
    )


def _ok(body: object):
    def handler(request: httpx.Request) -> httpx.Response:
        handler.requests.append(request)
        return httpx.Response(200, content=json.dumps(body), headers={"content-type": "text/json"})

    handler.requests = []
    return handler


def test_a_matching_approved_record_is_returned() -> None:
    handler = _ok([_row()])

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is not None
    assert answer.record.approved is True
    assert answer.record.identifier == 44


def test_the_request_names_the_pipeline_and_carries_the_bearer() -> None:
    """The pipeline filter is MANDATORY: change-manager's listing route withholds proposed sources
    from any caller that does not name one, so a lookup missing it reads every record as absent.
    Asserted on the wire because that failure is silent and points the permissive way."""
    handler = _ok([_row()])

    _source(handler).record_for(REPOSITORY, 42)

    request = handler.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/items"
    assert request.url.params["source"] == PIPELINE
    assert request.headers["authorization"] == "Bearer a-bearer"
    # Never a status filter. A `pending` record is the ordinary state of one awaiting a person,
    # and filtering it out server-side would report "there is no record" about a record there is.
    assert "status" not in request.url.params


def test_a_record_awaiting_approval_is_returned_rather_than_hidden() -> None:
    handler = _ok([_row(status="pending")])

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is not None
    assert answer.record.approved is False


def test_the_repository_is_matched_case_insensitively() -> None:
    """change-manager folds the repository into its own identity key, so a record proposed as
    `alobarquest/change-manager` is the SAME record as one proposed with the account's casing."""
    handler = _ok([_row(target_repository=REPOSITORY.lower())])

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.record is not None


def test_a_record_for_another_pull_request_is_not_a_match() -> None:
    handler = _ok([_row(pull_request_number=41)])

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is None


def test_two_records_for_one_pull_request_are_ambiguous_not_resolved() -> None:
    """Reported as ambiguity rather than resolved to whichever row came first."""
    handler = _ok([_row(), _row(id=45)])

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == RECORD_AMBIGUOUS


def test_an_unconfigured_source_answers_without_reaching_the_network() -> None:
    handler = _ok([_row()])
    source = HttpChangeRecordSource(
        base_url="", token="a-bearer", pipeline=PIPELINE, transport=httpx.MockTransport(handler)
    )

    answer = source.record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == SOURCE_UNCONFIGURED
    assert handler.requests == []


@pytest.mark.parametrize("missing", ["token", "pipeline"])
def test_every_missing_setting_is_unconfigured(missing: str) -> None:
    """All three are required to ask the question, so any one of them absent is the same fault."""
    handler = _ok([_row()])
    settings: dict[str, str] = {
        "base_url": "https://change-mgr.example",
        "token": "t",
        "pipeline": PIPELINE,
    }
    settings[missing] = ""

    answer = HttpChangeRecordSource(
        base_url=settings["base_url"],
        token=settings["token"],
        pipeline=settings["pipeline"],
        transport=httpx.MockTransport(handler),
    ).record_for(REPOSITORY, 42)

    assert answer.reason == SOURCE_UNCONFIGURED
    assert handler.requests == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 404, 500, 503])
def test_any_status_but_200_is_unreadable(status: int) -> None:
    """The REDIRECTS are the ones worth naming. `httpx` does not follow them by default, and a
    3xx can carry a JSON body -- which is how a renamed repository became a permanently invisible
    landing one increment ago. A reader that treats "below 400" as a body parses that body and
    reports "there is no record" about an estate it never reached."""
    answer = _source(lambda request: httpx.Response(status)).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == SOURCE_UNREADABLE


def test_a_redirect_carrying_a_json_body_is_unreadable_not_empty() -> None:
    """The discriminating form of the case above, and the one the estate has already been bitten
    by: a 3xx with a JSON body. `httpx` does not follow redirects by default, so a reader that
    treats "below 400" as a body PARSES the redirect -- and an empty listing in it reads as "the
    estate holds no record for this pull request", which is a confident answer about a service
    that was never reached. An empty-bodied redirect cannot show this: it fails at the JSON parse
    for an unrelated reason and the mutant looks correct."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, content=b"[]", headers={"location": "/api/items"})

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == SOURCE_UNREADABLE


def test_a_transport_failure_is_an_answer_rather_than_a_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("change-manager did not answer")

    answer = _source(handler).record_for(REPOSITORY, 42)

    assert answer.reason == SOURCE_UNREADABLE


def test_a_malformed_base_url_is_an_answer_rather_than_a_raise() -> None:
    """`InvalidURL` is not an `HTTPError`, and a TRAILING NEWLINE on a configured URL is the
    ordinary way an environment variable gets malformed -- `.rstrip("/")` does not remove it."""
    source = HttpChangeRecordSource(
        base_url="https://change-mgr.example\n", token="t", pipeline=PIPELINE
    )

    answer = source.record_for(REPOSITORY, 42)

    assert answer.reason == SOURCE_UNREADABLE


def test_a_body_that_is_not_a_listing_is_unreadable_not_empty() -> None:
    """Never "there is no record", which is a claim about the estate rather than about a reading."""
    answer = _source(_ok({"items": []})).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == SOURCE_UNREADABLE


def test_a_body_that_is_not_json_is_unreadable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>a proxy said something</html>")

    assert _source(handler).record_for(REPOSITORY, 42).reason == SOURCE_UNREADABLE


def test_a_boolean_pull_request_number_never_matches_pull_request_one() -> None:
    """`True` is an `int` in Python AND equals 1, so without the boolean guard a row carrying
    `true` matches pull request 1 and hands admission somebody else's record. Asked about 1
    specifically: against any other number the comparison fails anyway and proves nothing."""
    answer = _source(_ok([_row(pull_request_number=True)])).record_for(REPOSITORY, 1)

    assert answer.answered is True
    assert answer.record is None


def test_a_row_that_is_not_an_object_makes_the_whole_listing_unreadable() -> None:
    answer = _source(_ok([_row(), "not-a-row"])).record_for(REPOSITORY, 42)

    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_repository": None},
        {"target_repository": ""},
        {"pull_request_number": "42"},
        {"pull_request_number": True},
        {"id": None},
        {"status": None},
    ],
    ids=["no-repo", "empty-repo", "number-as-string", "number-as-bool", "no-id", "no-status"],
)
def test_a_row_missing_what_a_match_is_made_of_cannot_match(overrides: dict[str, object]) -> None:
    """Skipping such a row is the same answer as reading it and finding it does not match -- and
    `True` is an `int` in Python, so a boolean number is rejected rather than read as 1."""
    answer = _source(_ok([_row(**overrides)])).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is None
