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
    ChangeRecordAnswer,
    HttpChangeRecordSource,
    _answer_from_body,
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
    """The token and the pipeline; the base URL has its own test above, which additionally asserts
    that nothing reaches the network. All three are required to ask the question at all, so any one
    of them absent is the same fault."""
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


@pytest.mark.parametrize(
    "base_url",
    [
        "https://change-mgr.example\n",
        "https://change-mgr..example",
        "https://" + "a" * 64 + ".example",
        "https://change-mgr.example.",
        "not-a-url-at-all",
    ],
    ids=["trailing-newline", "doubled-dot", "over-long-label", "trailing-dot", "not-a-url"],
)
def test_a_malformed_base_url_is_an_answer_rather_than_a_raise(base_url: str) -> None:
    """FIVE shapes, because one shape is how this stayed broken.

    `InvalidURL` is not an `HTTPError`, and a trailing newline is the ordinary way an environment
    variable gets malformed -- that was the whole original case, and it is covered by `InvalidURL`.
    The HOST shapes are not: IDNA encoding raises `UnicodeError`, which is a `ValueError` and
    neither of the other two, so it escaped the module and surfaced as a bare 500 from a route.
    Adversarial review found it by probing; the mutation guarding this `except` was killed by a
    control that shared the same incomplete model of what httpx raises.
    """
    source = HttpChangeRecordSource(base_url=base_url, token="t", pipeline=PIPELINE)

    answer = source.record_for(REPOSITORY, 42)

    assert answer.answered is False
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


def test_a_row_from_another_pipeline_is_not_a_match() -> None:
    """The pipeline is re-checked on the ROW, not only asked of the server.

    change-manager scopes the listing correctly today, and FastAPI ignores an unknown query
    parameter silently -- so a renamed parameter, or a listing route that stopped scoping, would
    otherwise hand admission a record belonging to a pipeline this term knows nothing about.
    """
    answer = _source(_ok([_row(source="drift")])).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is None


def test_a_malformed_duplicate_still_counts_toward_ambiguity() -> None:
    """The guard is on the MATCH KEY, so a twin that is malformed in some other field cannot drop
    the tally below two and hand the surviving row through as unambiguous."""
    answer = _source(_ok([_row(), _row(status=None)])).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == RECORD_AMBIGUOUS


def test_a_matching_row_whose_status_does_not_read_is_unreadable() -> None:
    """Not `change_record_not_approved`, which would assert something about a record nobody could
    read, and not absent, which would be a claim about the estate."""
    answer = _source(_ok([_row(status=None)])).record_for(REPOSITORY, 42)

    assert answer.answered is False
    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_repository": None},
        {"target_repository": ""},
        {"pull_request_number": "42"},
        {"pull_request_number": True},
    ],
    ids=["no-repo", "empty-repo", "number-as-string", "number-as-bool"],
)
def test_a_row_missing_what_a_match_is_made_of_cannot_match(overrides: dict[str, object]) -> None:
    """Skipping such a row is the same answer as reading it and finding it does not match -- and
    `True` is an `int` in Python, so a boolean number is rejected rather than read as 1."""
    answer = _source(_ok([_row(**overrides)])).record_for(REPOSITORY, 42)

    assert answer.answered is True
    assert answer.record is None


# ---------------------------------------------------------------------------
# ADR-0019 increment 5b: what QUALIFIES `status`.
# ---------------------------------------------------------------------------


def _served_row(**overrides: object) -> dict:
    """One row as change-manager serves it, measured against production 2026-08-12."""
    row: dict = {
        "id": 52,
        "source": "deploy",
        "target_repository": "alobarquest/change-manager",
        "pull_request_number": 49,
        "status": "approved",
        "decided_by": "deploy-policy",
        "policy_version": 2,
        "policy_objections": [],
        "landing_policy_version": 2,
        "landing_conditions": {
            "update_types": ["semver-minor", "semver-patch"],
            "require_head_current_with_base": True,
            "rationale": "…",
            "rollout_workflows": {
                "alobarquest/change-manager": {
                    "path": ".github/workflows/deploy.yml",
                    "blob_sha": "a47d4b187c93971a5b5915ce87a963bd4ef35e30",
                }
            },
        },
    }
    row.update(overrides)
    return row


def _read(rows: list[dict]) -> ChangeRecordAnswer:
    return _answer_from_body(rows, "deploy", "alobarquest/change-manager", 49)


def test_the_qualifiers_that_tell_three_approved_rows_apart_are_read() -> None:
    record = _read([_served_row()]).record

    assert record is not None
    assert record.approved and record.record_id == 52
    assert record.policy_version == 2 and record.policy_objections == ()
    assert record.decided_by == "deploy-policy"


def test_a_record_a_human_approved_carries_no_version_rather_than_a_weaker_one() -> None:
    """Production item 44's shape. `None` is a different BASIS, not a lower number."""
    record = _read([_served_row(policy_version=None, decided_by="hq-correction")]).record

    assert record is not None and record.approved
    assert record.policy_version is None


def test_live_objections_are_carried_even_on_a_stored_approval() -> None:
    record = _read([_served_row(policy_objections=["risk_not_in_policy"])]).record

    assert record is not None
    assert record.approved
    assert record.policy_objections == ("risk_not_in_policy",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"policy_version": "2"},
        {"policy_version": True},
        {"id": "52"},
        {"policy_objections": "risk_not_in_policy"},
        {"policy_objections": [1]},
        {"decided_by": 7},
    ],
    ids=[
        "version-string",
        "version-bool",
        "id-string",
        "objections-string",
        "objections-int",
        "actor-int",
    ],
)
def test_a_qualifier_of_the_wrong_type_is_no_answer_rather_than_an_absent_one(
    overrides: dict,
) -> None:
    """`policy_version: "2"` read as None would present a policy-approved record as a
    human-approved one -- degrading to the nearest recognisable shape, in the one field that
    decides whether an unattended act may proceed."""
    answer = _read([_served_row(**overrides)])

    assert not answer.answered
    assert answer.reason == SOURCE_UNREADABLE


def test_the_landing_conditions_are_read_from_the_served_row() -> None:
    conditions = _read([_served_row()]).record.conditions  # type: ignore[union-attr]

    assert conditions is not None
    assert conditions.version == 2
    assert conditions.update_types == frozenset({"semver-minor", "semver-patch"})
    assert conditions.require_head_current_with_base is True
    pin = conditions.pin_for("AlobarQuest/change-manager")
    assert pin is not None and pin.path == ".github/workflows/deploy.yml"


@pytest.mark.parametrize(
    "overrides",
    [
        {"landing_conditions": None},
        {"landing_policy_version": None},
        {"landing_policy_version": "2"},
        {"landing_conditions": {"update_types": "semver-minor"}},
        {"landing_conditions": {"update_types": [], "require_head_current_with_base": "yes"}},
        {
            "landing_conditions": {
                "update_types": [],
                "require_head_current_with_base": True,
                "rollout_workflows": {"x": {"path": "", "blob_sha": "a"}},
            }
        },
    ],
    ids=["absent", "no-version", "version-string", "no-flag", "flag-string", "empty-path"],
)
def test_unreadable_conditions_are_None_and_do_not_poison_the_record(overrides: dict) -> None:
    """Deliberately NOT fatal to the record. The factory lane's term needs only the stored
    decision, and refusing every record because a field a newer service adds is missing would
    refuse work that has its own per-unit human approval. The landing that needs them refuses.
    """
    answer = _read([_served_row(**overrides)])

    assert answer.answered
    assert answer.record is not None
    assert answer.record.approved
    assert answer.record.conditions is None
