"""Reading the policy block that declares which repositories may be landed into unattended.
ADR-0038 part 2.

Two things are under test and they are different questions. The PARSE must refuse every shape it
does not recognise, in the direction that refuses rather than the one that invents a permission;
and the CLIENT must never raise, because only `DomainError` and `APIAuthenticationError` have
registered handlers and an escaping exception is a bare 500 from an admission gate, which is a gate
that has stopped deciding.
"""

from __future__ import annotations

import httpx
import pytest

from orchestrator.services.inert_landing_policy import (
    RULES_UNDECLARED,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    HttpInertLandingPolicySource,
    _rules_from_body,
)
from tests.services.inert_landing_doubles import (
    DECLARED_REPOSITORIES,
    EXCLUDED_ECOSYSTEM,
    INERT_POLICY_VERSION,
    UPDATE_BOT,
    served_body,
)

ROUTE = "/api/landing-policy"
TOKEN = "cm-bearer"


def _source(handler, **kwargs) -> HttpInertLandingPolicySource:
    return HttpInertLandingPolicySource(
        base_url=kwargs.pop("base_url", "https://change-mgr.example"),
        token=kwargs.pop("token", TOKEN),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_the_served_document_parses_into_the_declared_population() -> None:
    """The wire shape, as production answers it, read end to end."""
    answer = _rules_from_body(served_body())

    assert answer.reason is None
    rules = answer.rules
    assert rules is not None
    assert rules.version == INERT_POLICY_VERSION
    assert rules.repositories == frozenset(DECLARED_REPOSITORIES)
    assert rules.permitted_authors == frozenset({UPDATE_BOT})
    assert rules.excluded_ecosystems == frozenset({EXCLUDED_ECOSYSTEM})
    assert rules.require_head_current_with_base is True


def test_the_repository_lookup_folds_case_on_both_sides() -> None:
    """GitHub names are case-insensitive and the identity key crossing this boundary is folded at
    the parse; the lookup folds what it asks, so a differently-cased declaration still matches."""
    rules = _rules_from_body(
        served_body(
            inert_landing={
                **served_body()["inert_landing"],
                "repositories": ["AlobarQuest/Orchestrator"],
            }
        )
    ).rules

    assert rules is not None
    assert rules.declares("alobarquest/orchestrator")
    assert rules.declares("ALOBARQUEST/ORCHESTRATOR")
    assert not rules.declares("alobarquest/change-manager")


def test_the_author_check_folds_case_and_refuses_a_name_the_policy_does_not_carry() -> None:
    rules = _rules_from_body(served_body()).rules
    assert rules is not None

    assert rules.permits_author(UPDATE_BOT)
    assert rules.permits_author(UPDATE_BOT.upper())
    # The OTHER spelling of the same identity. It is what `gh pr view --json author` answers, and
    # the policy deliberately declares the REST one -- so this must NOT match, or the declaration
    # would be describing a condition it does not enforce.
    assert not rules.permits_author("app/dependabot")
    assert not rules.permits_author("alobar-sds-dispatch[bot]")


def test_a_document_with_no_block_is_its_own_reason_and_not_merely_unreadable() -> None:
    """Every policy version before the sixth omits it, so this is what a reader running ahead of
    the party holding the policy meets -- and after any rollback of it. It needs a different person
    from a service that is refusing, so it gets a different reason."""
    body = served_body()
    del body["inert_landing"]

    answer = _rules_from_body(body)

    assert answer.rules is None
    assert answer.reason == RULES_UNDECLARED


@pytest.mark.parametrize(
    "field",
    ["repositories", "permitted_authors", "excluded_ecosystems", "require_head_current_with_base"],
)
def test_every_absent_field_refuses_and_none_of_them_defaults(field: str) -> None:
    """**The direction is what this asserts.** Each of these BOUNDS what may land, so a default
    for any of them invents a permission the document never granted -- and two of them would
    default in the permissive direction: an absent `excluded_ecosystems` read as empty excludes
    nothing, and an absent freshness flag read as false waives the condition."""
    block = dict(served_body()["inert_landing"])
    del block[field]

    answer = _rules_from_body(served_body(inert_landing=block))

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


def test_an_empty_list_parses_because_it_is_a_decision_a_person_can_make() -> None:
    """Absent and empty are different, and only one of them is a document this build cannot read.
    A block declaring no repositories permits nothing; one excluding no ecosystems excludes
    nothing. Both are readable."""
    block = {
        **served_body()["inert_landing"],
        "repositories": [],
        "permitted_authors": [],
        "excluded_ecosystems": [],
    }

    rules = _rules_from_body(served_body(inert_landing=block)).rules

    assert rules is not None
    assert rules.repositories == frozenset()
    assert rules.permitted_authors == frozenset()
    assert rules.excluded_ecosystems == frozenset()
    assert not rules.declares("alobarquest/orchestrator")


@pytest.mark.parametrize(
    "value",
    [
        "not-a-list",
        ["ok", 7],
        ["ok", None],
        [""],
        {"repositories": []},
    ],
)
def test_a_list_that_is_not_of_non_empty_strings_refuses(value: object) -> None:
    """`None` from the helper means UNREADABLE and never means empty, which is the whole of why it
    is a helper: read as empty, an absent `excluded_ecosystems` would say "nothing is excluded"."""
    block = {**served_body()["inert_landing"], "excluded_ecosystems": value}

    answer = _rules_from_body(served_body(inert_landing=block))

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize("version", ["6", 6.0, None, True])
def test_a_version_that_is_not_a_plain_integer_refuses(version: object) -> None:
    """`True` is in this list on purpose: `isinstance(True, int)` is true in Python, and a boolean
    version would reach the landing commit's trailer as the permission it names."""
    answer = _rules_from_body(served_body(version=version))

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize("body", [None, [], "text", 7])
def test_a_body_that_is_not_an_object_refuses(body: object) -> None:
    answer = _rules_from_body(body)

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


def test_a_block_that_is_not_an_object_refuses() -> None:
    answer = _rules_from_body(served_body(inert_landing=["repositories"]))

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize(
    ("base_url", "token"),
    [("", TOKEN), ("https://change-mgr.example", "")],
)
def test_an_unconfigured_deployment_answers_unconfigured_and_makes_no_request(
    base_url: str, token: str
) -> None:
    """Its own reason, because it needs a different person from a service that is refusing."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=served_body())

    answer = _source(handler, base_url=base_url, token=token).inert_landing_rules()

    assert answer.rules is None
    assert answer.reason == SOURCE_UNCONFIGURED
    assert calls == []


def test_the_request_names_the_route_and_carries_the_bearer() -> None:
    """The route this reader names is the SECOND projection of one document, and the credential is
    change-manager's own -- the same one the change-record reader holds."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=served_body())

    answer = _source(handler).inert_landing_rules()

    assert answer.rules is not None
    assert len(seen) == 1
    assert seen[0].url.path == ROUTE
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert "orchestrator" in seen[0].headers["user-agent"]


@pytest.mark.parametrize("status", [204, 301, 400, 401, 403, 404, 500, 502])
def test_any_status_but_200_reads_as_unreadable(status: int) -> None:
    """**The body is a VALID policy document, and that is what makes this control discriminate.**
    An empty body is refused by the PARSE whether or not the status is checked at all, so the
    obvious version of this test passes against a client that reads a 404 as a policy -- measured,
    by a mutation that loosened the status check and killed nothing. Only a body the parser would
    happily accept leaves the status as the sole thing that can refuse it."""
    answer = _source(
        lambda request: httpx.Response(status, json=served_body())
    ).inert_landing_rules()

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


def test_a_body_that_is_not_json_reads_as_unreadable() -> None:
    answer = _source(
        lambda request: httpx.Response(200, content=b"<html>nope</html>")
    ).inert_landing_rules()

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


def test_a_transport_failure_reads_as_unreadable_and_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    answer = _source(handler).inert_landing_rules()

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE


@pytest.mark.parametrize(
    "base_url",
    [
        # A TRAILING NEWLINE, which `.rstrip("/")` does not remove -- the ordinary way an
        # environment variable gets malformed. `httpx.InvalidURL` derives from `Exception` and is
        # not an `HTTPError`.
        "https://change-mgr.example\n",
        # A DOUBLED DOT and an over-long DNS label: IDNA encoding raises `UnicodeError`, which is a
        # `ValueError` and neither of the other two families. This estate has shipped a bare 500
        # from an admission gate on exactly this input, twice, in two different modules.
        "https://change-mgr..example",
        "https://" + ("a" * 64) + ".example",
        # A control character, which `urlparse` refuses inside the CLIENT CONSTRUCTOR rather than
        # at request time -- the other half of the same family, and the half a guard written for
        # one shape misses.
        "https://change-mgr\x00.example",
    ],
)
def test_a_malformed_base_url_reads_as_unreadable_rather_than_raising(base_url: str) -> None:
    """Three exception families, two raise sites, one promise: nothing here raises.

    **NO MOCK TRANSPORT, and that is the whole of why this control discriminates.** A mock
    transport never resolves a host, so IDNA encoding never runs and two of these four inputs sail
    through it into a perfectly good answer -- measured, and it is exactly the shape of control
    that reports a guard as present when it is absent. The real transport is what raises, and none
    of these four can reach a socket: each is refused while the request is being built.
    """
    answer = HttpInertLandingPolicySource(
        base_url=base_url, token=TOKEN, timeout_seconds=0.01
    ).inert_landing_rules()

    assert answer.rules is None
    assert answer.reason == SOURCE_UNREADABLE
