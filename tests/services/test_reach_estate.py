"""A declared reach that contradicts what the estate records is a named refusal (WS-P2.28).

Both directions are proven here, and the ADMITTED direction is the one that matters: a check that
only ever refuses is a blanket refusal wearing a predicate. The two subjects are real —
`AlobarQuest/change-manager` is what App Brain records as redeploying (it is the case that started
this thread) and `AlobarQuest/orchestrator` is what it records as inert (8 of the 24 authored
packages target it).

The live bodies those two answers came from are pinned in `tests/fixtures/app_brain_landing.json`,
captured rather than hand-authored, so a shape change on App Brain's side reds this repository
instead of passing against an imagined API.
"""

import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from orchestrator.reach_vocabulary import LIVE_ESTATE, REACH_VOCABULARY
from orchestrator.services.dispatch import dispatch_work_unit
from orchestrator.services.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    SOURCE_UNREADABLE,
    EstateAnswer,
    HttpEstateLandingSource,
)
from orchestrator.services.reach_admission import (
    REACH_CONTRADICTS_ESTATE,
    REACH_ESTATE_SOURCE_UNCONFIGURED,
    REACH_ESTATE_SOURCE_UNREADABLE,
    REACH_ESTATE_UNKNOWN,
)
from tests.services.estate_doubles import FakeEstateLandingSource
from tests.services.test_change_window import OPEN, SHUT, FrozenClock
from tests.services.test_dispatch import (
    FakeGitHubDispatcher,
    dispatch_command,
    ready_unit,
    settings,
)

CHANGE_MANAGER = "AlobarQuest/change-manager"
ORCHESTRATOR = "AlobarQuest/orchestrator"
INTENT_PACKAGES = "AlobarQuest/intent-packages"

LIVE = json.loads(Path("tests/fixtures/app_brain_landing.json").read_text())["responses"]


def source(answer: EstateAnswer) -> FakeEstateLandingSource:
    return FakeEstateLandingSource(default=answer)


def admit(
    session: Session,
    unit,
    landing_source,
    *,
    repository: str,
    attempt: int = 1,
):
    return dispatch_work_unit(
        session,
        dispatch_command(unit.id, attempt=attempt),
        settings(allowed_target_repositories=frozenset({repository})),
        FakeGitHubDispatcher([]),
        landing_source,
    )


# --------------------------------------------------------------------------------------------
# The two directions.
# --------------------------------------------------------------------------------------------


def test_a_repository_the_estate_says_redeploys_refuses_a_source_repository_claim(
    migrated_session: Session,
) -> None:
    """The real case. The package said writes land in a repository and nothing else changes; App
    Brain says landing on that repository's default branch changes something already serving."""
    unit = ready_unit(
        migrated_session,
        key="estate-redeploys",
        target_repository=CHANGE_MANAGER,
        reach=["source_repository"],
    )

    record = admit(
        migrated_session,
        unit,
        source(EstateAnswer(LANDING_REDEPLOYS)),
        repository=CHANGE_MANAGER,
    )

    assert record.reason_code == REACH_CONTRADICTS_ESTATE
    # Blocked, not skipped: nothing clears on its own here. Somebody edits the package.
    assert record.status == "blocked"


def test_a_repository_the_estate_says_is_inert_admits_the_same_claim(
    migrated_session: Session,
) -> None:
    """Without this the check is a blanket refusal. Note the assertion that the estate was
    actually CONSULTED -- an admit that never asked would pass this test while the term was
    dead, which is the failure mode this repository has already shipped once."""
    unit = ready_unit(
        migrated_session,
        key="estate-inert",
        target_repository=ORCHESTRATOR,
        reach=["source_repository"],
    )
    landing_source = source(EstateAnswer(LANDING_INERT))

    record = admit(migrated_session, unit, landing_source, repository=ORCHESTRATOR)

    assert record.reason_code is None
    assert record.status == "dispatched"
    assert landing_source.asked == [ORCHESTRATOR]


# --------------------------------------------------------------------------------------------
# Unknown, and the two ways this process can have no answer. All refuse; all are distinguishable.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (EstateAnswer(LANDING_UNKNOWN, "no_app_record"), REACH_ESTATE_UNKNOWN),
        (EstateAnswer(LANDING_UNKNOWN, "not_assessed"), REACH_ESTATE_UNKNOWN),
        (EstateAnswer(None, SOURCE_UNREADABLE), REACH_ESTATE_SOURCE_UNREADABLE),
        (EstateAnswer(None, SOURCE_UNCONFIGURED), REACH_ESTATE_SOURCE_UNCONFIGURED),
        # An answer this build does not recognise. Nothing enumerates the refusing cases -- only
        # an explicit `inert` returns None -- so a fourth value shipped on the authoring side
        # cannot arrive here as permission.
        (EstateAnswer("redeploys_on_tuesdays"), REACH_ESTATE_UNKNOWN),
    ],
)
def test_anything_that_is_not_an_explicit_inert_refuses(
    migrated_session: Session, answer: EstateAnswer, expected: str
) -> None:
    unit = ready_unit(
        migrated_session,
        key=f"estate-closed-{uuid.uuid4().hex[:8]}",
        target_repository=INTENT_PACKAGES,
        reach=["source_repository"],
    )

    record = admit(migrated_session, unit, source(answer), repository=INTENT_PACKAGES)

    assert record.reason_code == expected
    assert record.status == "blocked"


def test_the_four_refusals_are_four_distinct_names() -> None:
    """Each sends somebody different: edit the package, determine and record the answer, set an
    environment variable, look at why a service is refusing."""
    assert (
        len(
            {
                REACH_CONTRADICTS_ESTATE,
                REACH_ESTATE_UNKNOWN,
                REACH_ESTATE_SOURCE_UNCONFIGURED,
                REACH_ESTATE_SOURCE_UNREADABLE,
            }
        )
        == 4
    )


# --------------------------------------------------------------------------------------------
# What the term deliberately does NOT claim.
# --------------------------------------------------------------------------------------------


def test_declaring_live_estate_is_admitted_and_the_estate_is_never_consulted(
    migrated_session: Session,
) -> None:
    """The claim under test is made by OMITTING live_estate. A package that declares it has said
    the true thing, so there is nothing to contradict and no reason to ask -- which is also what
    keeps the one network round-trip off the path of every unit that does not need it."""
    unit = ready_unit(
        migrated_session,
        key="estate-declared-live",
        target_repository=CHANGE_MANAGER,
        reach=["source_repository", "live_estate"],
    )
    landing_source = source(EstateAnswer(LANDING_REDEPLOYS))

    # An in-window clock, because declaring `live_estate` BRINGS the 02:00-06:00 window with it.
    # That is the whole point of the refusal this term exists to produce: the fix it pushes an
    # author toward is not a formality, it is the restraint the estate was owed all along.
    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=frozenset({CHANGE_MANAGER})),
        FakeGitHubDispatcher([]),
        landing_source,
        FrozenClock(OPEN),
    )

    assert record.reason_code is None
    assert landing_source.asked == []


def test_declaring_live_estate_costs_the_window_that_declaring_it_is_for(
    migrated_session: Session,
) -> None:
    """The other half of the same fact, stated so nobody reads the admit above as free. Outside
    the window the corrected package waits -- which is exactly what should have happened to the
    work that started this thread, and did not."""
    unit = ready_unit(
        migrated_session,
        key="estate-declared-live-shut",
        target_repository=CHANGE_MANAGER,
        reach=["source_repository", "live_estate"],
    )

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=frozenset({CHANGE_MANAGER})),
        FakeGitHubDispatcher([]),
        source(EstateAnswer(LANDING_REDEPLOYS)),
        FrozenClock(SHUT),
    )

    assert record.reason_code == "outside_change_window"
    assert record.status == "skipped"


def test_a_reach_nobody_declared_is_the_other_term_and_this_one_stays_silent(
    migrated_session: Session,
) -> None:
    """Reporting both would give one defect two reasons, and -- because this term sits below the
    undeclared one -- the second would be the reason nobody ever sees."""
    unit = ready_unit(
        migrated_session,
        key="estate-undeclared",
        target_repository=CHANGE_MANAGER,
        reach=[],
    )
    landing_source = source(EstateAnswer(LANDING_REDEPLOYS))

    record = admit(migrated_session, unit, landing_source, repository=CHANGE_MANAGER)

    assert record.reason_code == "reach_undeclared"
    assert landing_source.asked == []


def test_a_claim_naming_no_repository_reports_the_missing_constraint_not_a_missing_assessment(
    migrated_session: Session,
) -> None:
    unit = ready_unit(
        migrated_session,
        key="estate-no-target",
        target_repository=None,
        reach=["source_repository"],
    )
    landing_source = source(EstateAnswer(LANDING_REDEPLOYS))

    record = admit(migrated_session, unit, landing_source, repository=ORCHESTRATOR)

    assert record.reason_code == "target_repository_missing"
    assert landing_source.asked == []


# --------------------------------------------------------------------------------------------
# Ordering. The estate term must not hide a state somebody can already see.
# --------------------------------------------------------------------------------------------


def test_the_off_switch_outranks_an_estate_that_cannot_be_read(
    migrated_session: Session,
) -> None:
    """This is what makes evaluating the term in place safe. A process that cannot reach App
    Brain while routing is switched off still reports the off-switch."""
    unit = ready_unit(
        migrated_session,
        key="estate-off-switch",
        target_repository=CHANGE_MANAGER,
        reach=["source_repository"],
    )
    landing_source = source(EstateAnswer(None, SOURCE_UNREADABLE))

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(enabled=False, allowed_target_repositories=frozenset({CHANGE_MANAGER})),
        FakeGitHubDispatcher([]),
        landing_source,
    )

    assert record.reason_code == "dispatch_disabled"
    assert landing_source.asked == []


def test_the_estate_term_outranks_the_repository_allowlist(
    migrated_session: Session,
) -> None:
    """A contradicted declaration is a defect in the package; an allowlist miss is a setting on
    this deployment. Reporting the setting first would send somebody to change a list when the
    package is the thing that is wrong."""
    unit = ready_unit(
        migrated_session,
        key="estate-outranks-allowlist",
        target_repository=CHANGE_MANAGER,
        reach=["source_repository"],
    )

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(allowed_target_repositories=frozenset({ORCHESTRATOR})),
        FakeGitHubDispatcher([]),
        source(EstateAnswer(LANDING_REDEPLOYS)),
    )

    assert record.reason_code == REACH_CONTRADICTS_ESTATE


# --------------------------------------------------------------------------------------------
# The client. Observable because the transport is injectable -- a module-level httpx.get could
# only be tested by patching, which is how intent-packages' one outage test came to pass for an
# unrelated reason and never exercise the path it names.
# --------------------------------------------------------------------------------------------


def recording_source(handler) -> tuple[HttpEstateLandingSource, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return (
        HttpEstateLandingSource(
            base_url="https://app-brain.example",
            read_key="read-only-value",
            transport=httpx.MockTransport(capture),
        ),
        seen,
    )


def test_the_request_carries_the_read_credential_the_route_and_the_repository() -> None:
    client, seen = recording_source(
        lambda _: httpx.Response(200, json={"landing": LANDING_INERT, "reason": None})
    )

    assert client.landing_for(ORCHESTRATOR) == EstateAnswer(LANDING_INERT, None)
    (request,) = seen
    assert request.url.path == "/api/apps/default-branch-landing"
    assert request.url.params["github_repo"] == ORCHESTRATOR
    assert request.headers["x-brain-key"] == "read-only-value"
    assert request.method == "GET"


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda _: httpx.Response(401), id="refused"),
        pytest.param(lambda _: httpx.Response(500), id="server-error"),
        pytest.param(lambda _: httpx.Response(200, text="not json"), id="not-json"),
        pytest.param(lambda _: httpx.Response(200, json=["a", "list"]), id="not-an-object"),
        pytest.param(lambda _: httpx.Response(200, json={}), id="no-landing-key"),
        pytest.param(lambda _: httpx.Response(200, json={"landing": 7}), id="landing-not-a-string"),
        pytest.param(
            lambda _: httpx.Response(200, json={"landing": "sometimes"}), id="unrecognised-value"
        ),
    ],
)
def test_every_way_the_answer_can_fail_to_arrive_reads_as_no_answer(handler) -> None:
    client, _ = recording_source(handler)

    assert client.landing_for(ORCHESTRATOR) == EstateAnswer(None, SOURCE_UNREADABLE)


def test_a_transport_failure_reads_as_no_answer_rather_than_raising() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client, _ = recording_source(explode)

    # Not merely a convenience: only DomainError has a registered handler, so an escaping HTTP
    # exception would surface as a bare 500 from admission -- a gate that has stopped deciding.
    assert client.landing_for(ORCHESTRATOR) == EstateAnswer(None, SOURCE_UNREADABLE)


@pytest.mark.parametrize(("base_url", "read_key"), [("", "k"), ("https://x", ""), ("", "")])
def test_an_unconfigured_source_answers_without_reaching_the_network(
    base_url: str, read_key: str
) -> None:
    def never(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("an unconfigured source must not open a connection")

    client = HttpEstateLandingSource(
        base_url=base_url, read_key=read_key, transport=httpx.MockTransport(never)
    )

    assert client.landing_for(ORCHESTRATOR) == EstateAnswer(None, SOURCE_UNCONFIGURED)


# --------------------------------------------------------------------------------------------
# The captured live bodies parse to the answers this workstream was built on. The hash of a
# fixture proves only that a file is unchanged; this proves the parser still derives the same
# meaning from what App Brain actually served.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repository", "expected"),
    [
        (CHANGE_MANAGER, EstateAnswer(LANDING_REDEPLOYS, None)),
        (ORCHESTRATOR, EstateAnswer(LANDING_INERT, None)),
        (INTENT_PACKAGES, EstateAnswer(LANDING_UNKNOWN, "no_app_record")),
        ("AlobarQuest/project-standards", EstateAnswer(LANDING_UNKNOWN, "no_app_record")),
        # One repository, four app records, all agreeing. App Brain folds them; this side never
        # sees the rows and must not start reasoning about them.
        ("AlobarQuest/brain", EstateAnswer(LANDING_REDEPLOYS, None)),
    ],
)
def test_the_bodies_app_brain_actually_served_parse_to_the_expected_answers(
    repository: str, expected: EstateAnswer
) -> None:
    client, _ = recording_source(lambda _: httpx.Response(200, json=LIVE[repository]))

    assert client.landing_for(repository) == expected


def test_the_two_repositories_the_factory_cannot_yet_target_are_pinned_as_unknown() -> None:
    """Not a wish -- a record of the day-one cost of failing closed. Both are real repositories
    the factory can route to and neither is an App Brain app, so both refuse until they are
    onboarded (backlogged P1 9bb9d4c3d036). If this test starts failing because the answers
    became `inert`, the cost has been paid and this test should go.
    """
    for repository in (INTENT_PACKAGES, "AlobarQuest/project-standards"):
        assert LIVE[repository]["reason"] == "no_app_record"
        assert LIVE[repository]["matched_apps"] == 0


def test_the_named_member_and_the_vocabulary_it_belongs_to_stay_in_agreement() -> None:
    """`LIVE_ESTATE` is a second copy of a string that also appears as a key in
    `REACH_VOCABULARY`, and it is a second copy on purpose: the cross-boundary vocabulary scanner
    only recognises a collection of string LITERALS, so keying the dict by the name would have
    made the whole vocabulary invisible to it. This is the pin that makes the duplication safe.
    """
    assert LIVE_ESTATE in REACH_VOCABULARY
