"""May a pull request with no work unit be landed where landing changes something serving?

ADR-0019 increment 5b. Everything here runs with no network: the estate answer, the change record
and GitHub are all injected.

**Each term gets a firing test AND the composed satisfied case is asserted**, because a cascade of
refusals is exactly the shape that can be green while permitting everything -- an affirmative
answer nobody exercises is a `satisfied` nobody has seen be True.

Every clock-dependent assertion is pinned to a PAIR whose answers must differ. `live_estate`'s
window is four hours, so a single out-of-window assertion agrees with a term that ignores its
clock for 83% of the day, and this repository has already shipped that mistake twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from orchestrator.persistence.models import EstatePrMerge
from orchestrator.services.change_record import ChangeRecordAnswer, WorkflowPin
from orchestrator.services.estate_landing_admission import (
    DELIBERATE_REFUSALS,
    LANDING_APP_CREDENTIALS_MISSING,
    LANDING_AUTHOR_NOT_THE_UPDATE_BOT,
    LANDING_BASE_NOT_DEFAULT_BRANCH,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_CHECKS_VERDICT_UNREADABLE,
    LANDING_CONDITIONS_UNREADABLE,
    LANDING_ECOSYSTEM_EXCLUDED,
    LANDING_ECOSYSTEM_UNREADABLE,
    LANDING_ESTATE_SOURCE_UNCONFIGURED,
    LANDING_ESTATE_UNKNOWN,
    LANDING_FRESHNESS_UNREADABLE,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_MERGEABILITY_UNKNOWN,
    LANDING_MERGEABILITY_UNRECOGNISED,
    LANDING_NOT_ENABLED,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    LANDING_POLICY_VERSION_SUPERSEDED,
    LANDING_PULL_REQUEST_CONFLICTED,
    LANDING_PULL_REQUEST_NOT_OPEN,
    LANDING_PULL_REQUEST_UNREADABLE,
    LANDING_RECORD_ABSENT,
    LANDING_RECORD_HAS_LIVE_OBJECTIONS,
    LANDING_RECORD_NOT_APPROVED,
    LANDING_RECORD_NOT_POLICY_APPROVED,
    LANDING_RECORD_SOURCE_UNREADABLE,
    LANDING_RECORD_UNIDENTIFIED,
    LANDING_ROLLOUT_MOVED,
    LANDING_ROLLOUT_UNPINNED,
    LANDING_ROLLOUT_UNREADABLE,
    LANDING_TARGET_NOT_ROUTED,
    LANDING_UPDATE_TYPE_NOT_PERMITTED,
    LANDING_UPDATE_TYPE_UNPARSEABLE,
    EstateGatewayError,
    EstateLandingAdmission,
    ecosystem_of,
    estate_landing_admission,
    freshness_derived_refusals,
    gateway_failure_detail,
    qualifies_for_branch_update,
    update_type_of,
)
from tests.services.change_record_doubles import (
    SOURCE_UNREADABLE,
    FakeChangeRecordSource,
)
from tests.services.estate_doubles import (
    LANDING_UNKNOWN,
    SOURCE_UNCONFIGURED,
    EstateAnswer,
    FakeEstateLandingSource,
    inert_source,
    redeploying_source,
)
from tests.services.estate_landing_doubles import (
    HEAD,
    POLICY_VERSION,
    REPOSITORY,
    ROLLOUT_BLOB,
    ROLLOUT_PATH,
    WORKFLOW_AUTOMATION,
    FakeEstateGateway,
    approved,
    conditions,
    outcome_conditions,
    pull_request,
    run,
)

PR = 49
IN_WINDOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 8, 11, 19, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


def _ask(
    session: Session,
    *,
    record: ChangeRecordAnswer | None = None,
    gateway: FakeEstateGateway | None = None,
    landing: FakeEstateLandingSource | None = None,
    enabled: bool = True,
    credentials: bool = True,
    moment: datetime = IN_WINDOW,
):
    return estate_landing_admission(
        session,
        REPOSITORY,
        PR,
        landing or redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, PR): record or approved()}),
        gateway or FakeEstateGateway(),
        enabled=enabled,
        credentials_configured=credentials,
        clock=FixedClock(moment),
    )


# ---------------------------------------------------------------------------
# The affirmative case, first. A cascade nobody has seen say yes is not a gate.
# ---------------------------------------------------------------------------


def test_a_conformant_pull_request_in_the_window_is_admitted(migrated_session: Session) -> None:
    answer = _ask(migrated_session)

    assert answer.satisfied, answer.refusals
    assert answer.refusals == ()
    assert answer.head_sha == HEAD
    assert answer.change_record_id == 52
    assert answer.policy_version == POLICY_VERSION


def test_the_rollout_pin_is_read_at_the_base_AND_at_the_head(migrated_session: Session) -> None:
    """BOTH, and an earlier version of this test asserted base-only with a docstring arguing for
    it. The base is what the rollout runs from today and is unchanged until the landing, which is
    exactly why reading it alone cannot see a pull request that is about to replace it.
    """
    gateway = FakeEstateGateway()
    _ask(migrated_session, gateway=gateway)

    assert gateway.blobs == [
        (REPOSITORY, ROLLOUT_PATH, "main"),
        (REPOSITORY, ROLLOUT_PATH, HEAD),
    ]


# ---------------------------------------------------------------------------
# Configuration and the estate.
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_refuses(migrated_session: Session) -> None:
    assert LANDING_NOT_ENABLED in _ask(migrated_session, enabled=False).refusals


def test_missing_app_credentials_refuse(migrated_session: Session) -> None:
    assert LANDING_APP_CREDENTIALS_MISSING in _ask(migrated_session, credentials=False).refusals


def test_an_inert_repository_is_refused_by_this_lane(migrated_session: Session) -> None:
    """The OPPOSITE direction from the work-unit landing's term, and both are deliberate: that
    one exists only where a landed pull request is inert, this one only where it is not."""
    answer = _ask(migrated_session, landing=inert_source())

    assert not answer.satisfied
    assert LANDING_TARGET_NOT_ROUTED in answer.refusals


def test_an_unassessed_repository_is_not_permission(migrated_session: Session) -> None:
    source = FakeEstateLandingSource({REPOSITORY: EstateAnswer(LANDING_UNKNOWN, "not_assessed")})

    assert LANDING_ESTATE_UNKNOWN in _ask(migrated_session, landing=source).refusals


def test_an_unconfigured_estate_source_is_named_apart_from_an_unreadable_one(
    migrated_session: Session,
) -> None:
    """One sets an environment variable; the other looks at why a service is refusing."""
    source = FakeEstateLandingSource({REPOSITORY: EstateAnswer(None, SOURCE_UNCONFIGURED)})

    assert LANDING_ESTATE_SOURCE_UNCONFIGURED in _ask(migrated_session, landing=source).refusals


# ---------------------------------------------------------------------------
# The record, and the three shapes of `approved` this increment tells apart.
# ---------------------------------------------------------------------------


def test_no_record_refuses(migrated_session: Session) -> None:
    assert LANDING_RECORD_ABSENT in _ask(migrated_session, record=ChangeRecordAnswer(True)).refusals


def test_an_unreadable_record_service_is_not_an_absent_record(migrated_session: Session) -> None:
    answer = _ask(migrated_session, record=ChangeRecordAnswer(False, reason=SOURCE_UNREADABLE))

    assert LANDING_RECORD_SOURCE_UNREADABLE in answer.refusals
    assert LANDING_RECORD_ABSENT not in answer.refusals


def test_a_pending_record_refuses(migrated_session: Session) -> None:
    assert (
        LANDING_RECORD_NOT_APPROVED
        in _ask(migrated_session, record=approved(status="pending")).refusals
    )


def test_a_record_a_human_approved_with_no_policy_behind_it_refuses(
    migrated_session: Session,
) -> None:
    """Production item 44's shape. It is a valid basis for a person and not for this lane: an
    unattended act binds itself to a standing rule, and "somebody approved this once" is not one.
    Keyed on `policy_version`, which is exactly the field a reader of `status` alone cannot see.
    """
    answer = _ask(migrated_session, record=approved(policy_version=None))

    assert not answer.satisfied
    assert LANDING_RECORD_NOT_POLICY_APPROVED in answer.refusals


def test_a_record_approved_under_a_superseded_version_refuses(migrated_session: Session) -> None:
    """THE term that makes narrowing the policy mean anything.

    Nothing re-evaluates a stored record except a fresh proposal of the same pull request, so
    without this the estate could move its policy and every approval already granted would go on
    being honoured.
    """
    answer = _ask(migrated_session, record=approved(policy_version=1))

    assert not answer.satisfied
    assert LANDING_POLICY_VERSION_SUPERSEDED in answer.refusals


def test_a_stored_approval_the_policy_has_since_overtaken_refuses(
    migrated_session: Session,
) -> None:
    """The third indistinguishable row: stored `approved`, live objections non-empty."""
    answer = _ask(migrated_session, record=approved(objections=("risk_not_in_policy",)))

    assert not answer.satisfied
    assert LANDING_RECORD_HAS_LIVE_OBJECTIONS in answer.refusals


def test_conditions_this_build_could_not_read_refuse(migrated_session: Session) -> None:
    """A record service that predates them, or a shape this build does not recognise. Proceeding
    would mean acting under conditions nobody stated."""
    answer = _ask(migrated_session, record=approved(landing_conditions=None))

    assert not answer.satisfied
    assert LANDING_CONDITIONS_UNREADABLE in answer.refusals


# ---------------------------------------------------------------------------
# The window, pinned to a PAIR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "admitted"), [(IN_WINDOW, True), (OUT_OF_WINDOW, False)], ids=["inside", "outside"]
)
def test_the_window_admits_inside_and_refuses_outside(
    migrated_session: Session, moment: datetime, admitted: bool
) -> None:
    """A PAIR, because a single out-of-window assertion cannot kill a term that ignores its clock:
    whenever the real clock is also outside the window the mutant and the original agree."""
    answer = _ask(migrated_session, moment=moment)

    assert answer.satisfied is admitted
    assert (LANDING_OUTSIDE_CHANGE_WINDOW in answer.refusals) is not admitted


# ---------------------------------------------------------------------------
# Pace.
# ---------------------------------------------------------------------------


def _landed_row(session: Session, *, moment: datetime, repository: str = REPOSITORY) -> None:
    row = EstatePrMerge(
        repository=repository,
        pr_number=999,
        head_sha=HEAD,
        status="merged",
        idempotency_key=f"pace-{repository}-{moment.isoformat()}",
        created_at=moment,
    )
    session.add(row)
    session.commit()


def test_a_second_landing_in_the_same_window_is_refused(migrated_session: Session) -> None:
    # 02:30 New York on the same night as IN_WINDOW (06:30 UTC), i.e. inside this occurrence.
    _landed_row(migrated_session, moment=datetime(2026, 8, 11, 6, 30, tzinfo=UTC))

    answer = _ask(migrated_session)

    assert not answer.satisfied
    assert LANDING_PACE_EXHAUSTED in answer.refusals


def test_a_landing_from_a_previous_window_does_not_exhaust_this_one(
    migrated_session: Session,
) -> None:
    """The control. Without it the term would pass for a rule that simply refuses forever."""
    _landed_row(migrated_session, moment=datetime(2026, 8, 10, 6, 30, tzinfo=UTC))

    assert _ask(migrated_session).satisfied


def test_a_landing_into_another_repository_does_not_exhaust_this_one(
    migrated_session: Session,
) -> None:
    """One landing per REPOSITORY per window, not one across the estate."""
    _landed_row(
        migrated_session,
        moment=datetime(2026, 8, 11, 6, 30, tzinfo=UTC),
        repository="alobarquest/brain",
    )

    assert _ask(migrated_session).satisfied


# ---------------------------------------------------------------------------
# What only GitHub knows.
# ---------------------------------------------------------------------------


def test_an_unreadable_pull_request_refuses_and_asks_nothing_further(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(read_error=EstateGatewayError("read_status", 502))
    answer = _ask(migrated_session, gateway=gateway)

    assert LANDING_PULL_REQUEST_UNREADABLE in answer.refusals
    assert gateway.compares == [] and gateway.blobs == []


def test_a_closed_pull_request_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(is_open=False))

    assert LANDING_PULL_REQUEST_NOT_OPEN in _ask(migrated_session, gateway=gateway).refusals


def test_a_pull_request_against_another_base_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(pull=pull_request(base_ref="release"))

    assert LANDING_BASE_NOT_DEFAULT_BRANCH in _ask(migrated_session, gateway=gateway).refusals


@pytest.mark.parametrize(
    ("login", "is_bot"),
    [
        ("alobar-sds-dispatch[bot]", True),
        ("AlobarQuest", False),
        ("dependabot[bot]", False),
    ],
    ids=["another-app", "a-person", "the-right-login-wrong-type"],
)
def test_only_the_update_bot_itself_is_admitted(
    migrated_session: Session, login: str, is_bot: bool
) -> None:
    """NOT "any account of type Bot". That admits every GitHub App -- including this estate's own,
    which holds a write on every repository in the account."""
    gateway = FakeEstateGateway(pull=pull_request(author_login=login, author_is_bot=is_bot))

    assert LANDING_AUTHOR_NOT_THE_UPDATE_BOT in _ask(migrated_session, gateway=gateway).refusals


def test_a_pull_request_the_remote_will_not_land_refuses(migrated_session: Session) -> None:
    """A required check that FAILED. The composite says `blocked`, and so it does for three other
    causes -- which is why the runs are read before this refusal is raised."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"), runs=(run(conclusion="failure"),)
    )

    assert LANDING_CHECKS_NOT_CLEAN in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# No verdict is not a failed verdict.
#
# Measured 2026-08-22 against one repository with one required check: a genuinely failing gate, a
# gate abandoned mid-run and a gate still running ALL answer `mergeable_state: blocked`, and only
# a green gate answers `clean`. Three live pull requests in this estate's ledger repositories were
# `blocked` with every run at their head abandoned. So the composite cannot separate them and the
# runs at the head are read.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "stale"])
def test_an_abandoned_check_is_an_absent_verdict_not_a_failed_one(
    migrated_session: Session, conclusion: str
) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"), runs=(run(conclusion=conclusion),)
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_AWAITING_VERDICT in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals


def test_a_head_with_no_runs_at_all_has_reached_no_verdict(migrated_session: Session) -> None:
    """A required context nothing published. Blocked, and nothing has said no."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="blocked"), runs=())

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_AWAITING_VERDICT in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals


def test_a_green_run_beside_an_abandoned_one_is_still_an_absent_verdict(
    migrated_session: Session,
) -> None:
    """`brain#35`'s shape, measured: one workflow succeeded and the required one was abandoned. A
    run that passed cannot be what holds the landing, so it neither refuses nor excuses."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"),
        runs=(run(), run(conclusion="cancelled")),
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_AWAITING_VERDICT in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals


@pytest.mark.parametrize(
    "conclusion", ["failure", "timed_out", "action_required", "startup_failure", "invented_by_a"]
)
def test_any_conclusion_nobody_enumerated_reads_as_a_verdict_this_lane_may_not_land_on(
    migrated_session: Session, conclusion: str
) -> None:
    """THE POLARITY, and it is the whole safety of the split. The absent set is closed and small;
    everything else -- including a word the platform has not invented yet -- fails toward refusing.
    A conclusion that fell through to `awaiting_verdict` would invite the branch to be freshened on
    the strength of a verdict nobody read."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"), runs=(run(conclusion=conclusion),)
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_NOT_CLEAN in refusals
    assert LANDING_CHECKS_AWAITING_VERDICT not in refusals


def test_a_run_still_going_is_neither_a_verdict_nor_its_absence(migrated_session: Session) -> None:
    """Waiting answers it for free; freshening would abandon the very run being waited on."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"),
        runs=(run(status="in_progress", conclusion=None),),
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_IN_FLIGHT in refusals
    assert LANDING_CHECKS_AWAITING_VERDICT not in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals


def test_a_failing_run_outranks_one_still_going(migrated_session: Session) -> None:
    """The head has said no, whatever else is pending. Reading these in the other order would let
    an in-flight sibling excuse a failure."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"),
        runs=(run(status="queued", conclusion=None), run(conclusion="failure")),
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_NOT_CLEAN in refusals
    assert LANDING_CHECKS_IN_FLIGHT not in refusals


def test_runs_that_cannot_be_read_refuse_rather_than_assuming_which_case_holds(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"),
        runs_error=EstateGatewayError("read_status", 503),
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_CHECKS_VERDICT_UNREADABLE in refusals
    assert LANDING_CHECKS_AWAITING_VERDICT not in refusals


def test_a_clean_pull_request_is_never_asked_about_its_runs(migrated_session: Session) -> None:
    """The second read costs a remote call, and a permitted composite has nothing to separate."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="clean"))

    _ask(migrated_session, gateway=gateway)

    assert gateway.run_reads == []


def test_a_conflicted_branch_says_so_rather_than_blaming_a_check(
    migrated_session: Session,
) -> None:
    """THE case this replaced, and it was live: `alobarquest/factory-runner#71` carried two
    `Quality` runs at `success` on 2026-09-05 while diverged from its base, and the lane reported
    that its checks were not clean. A reader following that report goes and stares at green CI.

    The predecessor of this test asserted exactly that behaviour, so the defect was pinned rather
    than merely present -- which is why it survived the narrowing that took `blocked` apart.
    """
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="dirty"))

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_PULL_REQUEST_CONFLICTED in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals
    assert gateway.run_reads == [], "a conflict is a fact about the branch; the runs say nothing"


@pytest.mark.parametrize("state", ["draft", "has_hooks", "behind", "a-word-github-invents-later"])
def test_a_state_this_lane_cannot_name_says_that_rather_than_naming_a_check(
    migrated_session: Session, state: str
) -> None:
    """The general half of the fix. The defect was not that `dirty` lacked a name -- it was that an
    unrecognised word was given somebody else's, so every future state inherited the same wrong
    cause. The unnamed case is parametrized deliberately: it is the one that keeps this honest."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state=state))

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_MERGEABILITY_UNRECOGNISED in refusals
    assert LANDING_CHECKS_NOT_CLEAN not in refusals
    assert gateway.run_reads == []


def test_an_unrecognised_state_still_refuses(migrated_session: Session) -> None:
    """The control that stops the rename becoming a permission: the NAME moved, the answer did
    not. Without this, a state renamed out of the failing-check refusal could quietly land."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="a-word-github-invents-later"))

    assert not _ask(migrated_session, gateway=gateway).satisfied


def test_a_conflicted_branch_still_refuses(migrated_session: Session) -> None:
    """Same control, for the state that occurs. A conflict must never become landable by being
    named more precisely."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="dirty"))

    assert not _ask(migrated_session, gateway=gateway).satisfied


@pytest.mark.parametrize(
    ("conclusion", "expected"),
    [
        ("failure", LANDING_CHECKS_NOT_CLEAN),
        ("cancelled", LANDING_CHECKS_AWAITING_VERDICT),
    ],
)
def test_a_non_required_check_gets_the_SAME_second_read_as_a_required_one(
    migrated_session: Session, conclusion: str, expected: str
) -> None:
    """`unstable` means a non-required run has not said yes, which is the same question `blocked`
    asks and wants the same answer. Giving it a cruder answer of its own would rebuild, one state
    over, the collapse this module already paid to take apart -- so it is inquired into, and the
    two causes separate exactly as they do for `blocked`."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="unstable"), runs=(run(conclusion=conclusion),)
    )

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert expected in refusals
    assert gateway.run_reads != [], "the second read is what separates the causes"


def test_a_mergeability_the_remote_has_not_computed_is_not_asked_about_its_runs(
    migrated_session: Session,
) -> None:
    """`unknown` already has its own refusal and its own remedy: ask again. Spending the second
    read on it would classify a pull request whose composite is not yet an answer."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="unknown"))

    refusals = _ask(migrated_session, gateway=gateway).refusals

    assert LANDING_MERGEABILITY_UNKNOWN in refusals
    assert LANDING_CHECKS_AWAITING_VERDICT not in refusals
    assert gateway.run_reads == []


def test_the_runs_are_read_at_the_pull_requests_own_head(migrated_session: Session) -> None:
    """Not at the base, and not at a head carried from anywhere else. A verdict read at the wrong
    commit is a verdict about a different tree."""
    gateway = FakeEstateGateway(
        pull=pull_request(mergeable_state="blocked"), runs=(run(conclusion="cancelled"),)
    )

    _ask(migrated_session, gateway=gateway)

    assert gateway.run_reads == [(REPOSITORY, HEAD)]


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ((run(conclusion="failure"),), LANDING_CHECKS_NOT_CLEAN),
        ((run(conclusion="cancelled"),), LANDING_CHECKS_AWAITING_VERDICT),
        ((run(status="in_progress", conclusion=None),), LANDING_CHECKS_IN_FLIGHT),
    ],
)
def test_no_reading_of_the_checks_ADMITS_a_pull_request_the_remote_has_blocked(
    migrated_session: Session, runs: tuple[object, ...], expected: str
) -> None:
    """SEPARATING THE CAUSES DOES NOT SOFTEN THE ANSWER. Every one of the three still refuses; only
    the name and the remedy differ. Without this, a term left out of the composed conjunction would
    let a `blocked` pull request through while each refusal test above still passed -- the answer
    naming a condition and permitting anyway, which is the fail-open shape this module argues
    against in its own opening."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="blocked"), runs=runs)  # type: ignore[arg-type]

    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert expected in answer.refusals


# ---------------------------------------------------------------------------
# Freshness -- the term the whole increment turns on.
# ---------------------------------------------------------------------------


def test_a_head_behind_its_base_refuses_even_when_the_remote_calls_it_clean(
    migrated_session: Session,
) -> None:
    """THE case, and the pairing is the point: `mergeable_state` is stale-tolerant, so a pull
    request answers `clean` while being two commits behind -- which is exactly what all four
    waiting pull requests were on 2026-08-12. The squash would produce a tree no check has run.
    """
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="clean"), behind=2)
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert LANDING_CHECKS_NOT_CLEAN not in answer.refusals


@pytest.mark.parametrize(("behind", "admitted"), [(0, True), (1, False), (2, False)])
def test_one_commit_behind_is_behind(
    migrated_session: Session, behind: int, admitted: bool
) -> None:
    """The boundary, pinned. A test that only ever measured two commits behind cannot tell this
    term from one that tolerates a single commit -- and a single commit is the ordinary state of
    every sibling pull request the moment one of them lands."""
    answer = _ask(migrated_session, gateway=FakeEstateGateway(behind=behind))

    assert answer.satisfied is admitted
    assert (LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals) is not admitted


def test_an_unreadable_comparison_refuses_rather_than_assuming_current(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(compare_error=EstateGatewayError("read_status", 500))

    assert LANDING_FRESHNESS_UNREADABLE in _ask(migrated_session, gateway=gateway).refusals


def test_a_policy_that_does_not_require_freshness_does_not_ask(
    migrated_session: Session,
) -> None:
    """The condition is the policy's to state. A version that does not declare it is not
    second-guessed, and the round trip is not spent."""
    gateway = FakeEstateGateway(behind=5)
    answer = _ask(
        migrated_session,
        record=approved(landing_conditions=conditions(require_fresh=False)),
        gateway=gateway,
    )

    assert answer.satisfied, answer.refusals
    assert gateway.compares == []


# ---------------------------------------------------------------------------
# The update type, parsed from the title at the moment of the act.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("build(deps): bump alembic from 1.18.5 to 1.19.0", "semver-minor"),
        ("build(deps-dev): bump ruff from 0.15.20 to 0.15.21", "semver-patch"),
        ("build(deps-dev): bump ruff from 0.15.20 to 0.16.2", "semver-minor"),
        ("build(deps): bump zod from 3.25.76 to 4.4.3", "semver-major"),
        # Single-component versions, how the workflow-automation ecosystem is numbered.
        ("build(deps): bump actions/checkout from 4 to 7", "semver-major"),
        # A requirement RANGE carries no delta any rule could be applied to.
        ("build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1", None),
        ("chore(deps): update fastmcp requirement from <4,>=3.4.2 to >=3.4.4,<4", None),
        # A GROUPED bump names one dependency and changes several.
        ("build(deps-dev): bump tsx from 4.23.5 to 4.23.9 in the minor-and-patch group", None),
        ("build(deps): bump the minor-and-patch group across 1 directory with 5 updates", None),
        # No movement, and a downgrade: neither is an update this lane can classify.
        ("build(deps): bump x from 1.2.3 to 1.2.3", None),
        ("build(deps): bump x from 1.3.0 to 1.2.9", None),
        ("build(deps): bump x from 1.2.3 to 1.2.3.4.5", None),
    ],
)
def test_the_update_type_is_read_from_the_title(title: str, expected: str | None) -> None:
    """Every case here is a real title measured across the estate on 2026-08-12, except the last
    three, which are the shapes a parser must refuse rather than guess at.

    **The title, and measurably not the alternatives.** The update bot rewrites a pull request in
    place; on `intent-packages` #50 the branch still read `ruff-0.16.0` while the title read
    `0.16.1` -- and so did the bot's own machine-readable `dependency-version` trailer in the head
    commit, whose diff installs 0.16.1. The two identifiers that look more structured are the two
    that went stale.
    """
    assert update_type_of(title) == expected


def test_an_unparseable_title_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(
            title="build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1"
        )
    )

    assert LANDING_UPDATE_TYPE_UNPARSEABLE in _ask(migrated_session, gateway=gateway).refusals


def test_an_update_type_the_policy_does_not_permit_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(
        pull=pull_request(title="build(deps): bump actions/checkout from 4 to 7")
    )

    assert LANDING_UPDATE_TYPE_NOT_PERMITTED in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# ADR-0036: the version that decides on the OUTCOME.
#
# THE FIXTURE IS THE POPULATION. The five pull requests below are the ones this estate held
# unlandable, read from the two repositories on 2026-08-30 -- title and branch as the update bot
# wrote them. Every one refuses under the rule in force before this change and is admitted under
# the one after it, and BOTH directions are asserted here: the refused direction is what makes the
# admitted direction mean something, because a change that admitted everything would satisfy the
# admitted direction alone.
# ---------------------------------------------------------------------------

# (title, branch), measured 2026-08-30 from AlobarQuest/change-manager and AlobarQuest/brain.
STUCK = [
    (
        "build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.4",
        "dependabot/uv/uvicorn-standard--gte-0.52.4",
    ),
    (
        "build(deps-dev): update setuptools requirement from >=83.0.0 to >=84.0.0",
        "dependabot/uv/setuptools-gte-84.0.0",
    ),
    (
        "chore(deps): update pydantic-settings requirement from <3,>=2.14.2 to >=2.15.0,<3",
        "dependabot/pip/pydantic-settings-gte-2.15.0-and-lt-3",
    ),
    (
        "chore(deps-dev): update greenlet requirement from >=3.0 to >=3.5.5",
        "dependabot/pip/greenlet-gte-3.5.5",
    ),
    (
        "chore(deps): update fastmcp requirement from <4,>=3.4.2 to >=3.4.7,<4",
        "dependabot/pip/fastmcp-gte-3.4.7-and-lt-4",
    ),
]
STUCK_IDS = ["uvicorn", "setuptools", "pydantic-settings", "greenlet", "fastmcp"]

# A branch the update bot opens for the one ecosystem version 5 excludes. Its title parses as a
# major, so it is the case that separates "excluded because of what it touches" from "refused for
# being too large a jump" -- under version 5 nothing is refused for the latter.
ACTIONS_TITLE = "build(deps): bump actions/checkout from 4 to 5"
ACTIONS_REF = "dependabot/github_actions/actions/checkout-5"


def _outcome_record(*, policy_version: int | None = 5, excluded: frozenset[str] | None = None):
    """A record approved under version 5, with version 5 in force."""
    return approved(
        policy_version=policy_version,
        landing_conditions=(
            outcome_conditions()
            if excluded is None
            else outcome_conditions(excluded_ecosystems=excluded)
        ),
    )


@pytest.mark.parametrize(("title", "head_ref"), STUCK, ids=STUCK_IDS)
def test_a_held_pull_request_is_REFUSED_under_the_update_type_rule(
    migrated_session: Session, title: str, head_ref: str
) -> None:
    """The before-state, asserted rather than recalled. Each of the five states a requirement
    RANGE, so no delta parses and the refusal is raised before any policy value is consulted --
    which is why widening the permitted set could never have reached one of them."""
    gateway = FakeEstateGateway(pull=pull_request(title=title, head_ref=head_ref))
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_UPDATE_TYPE_UNPARSEABLE in answer.refusals


@pytest.mark.parametrize(("title", "head_ref"), STUCK, ids=STUCK_IDS)
def test_a_held_pull_request_is_ADMITTED_under_the_outcome_rule(
    migrated_session: Session, title: str, head_ref: str
) -> None:
    """The whole answer, not the term alone: `satisfied` with no refusals at all."""
    gateway = FakeEstateGateway(pull=pull_request(title=title, head_ref=head_ref))
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert answer.satisfied, answer.refusals
    assert answer.refusals == ()


def test_a_major_package_bump_is_ADMITTED_under_the_outcome_rule(
    migrated_session: Session,
) -> None:
    """The direction that distinguishes the outcome rule from a wider set of update types.

    A first draft of ADR-0036 admitted a bump stating NO delta while still refusing one that
    states the largest delta there is -- treating the less classifiable change more permissively
    than the more classifiable one. This is that incoherence asserted away: what decides is the
    checks, and a major whose checks pass is admitted like any other.
    """
    gateway = FakeEstateGateway(
        pull=pull_request(
            title="build(deps): bump httpx from 1.2.3 to 2.0.0",
            head_ref="dependabot/uv/httpx-2.0.0",
        )
    )
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert answer.satisfied, answer.refusals


def test_the_same_major_is_REFUSED_under_the_update_type_rule(migrated_session: Session) -> None:
    """The control for the case above, on the same subject: one variable, the rule in force."""
    gateway = FakeEstateGateway(
        pull=pull_request(
            title="build(deps): bump httpx from 1.2.3 to 2.0.0",
            head_ref="dependabot/uv/httpx-2.0.0",
        )
    )
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_UPDATE_TYPE_NOT_PERMITTED in answer.refusals


def test_a_workflow_automation_bump_is_REFUSED_under_the_outcome_rule(
    migrated_session: Session,
) -> None:
    """The exclusion, which is the reason the outcome rule is not "admit everything".

    On these repositories the rollout job is gated on a push to the default branch, so it runs on
    no pull request -- visible on each of them as a skipped job beside the passing ones. A change
    reaching it would be exercised for the first time by the rollout it is supposed to gate, so
    the outcome says nothing about it and the outcome rule declines to decide.
    """
    gateway = FakeEstateGateway(pull=pull_request(title=ACTIONS_TITLE, head_ref=ACTIONS_REF))
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ECOSYSTEM_EXCLUDED in answer.refusals
    # Not refused for its DELTA. The update-type codes belong to the other rule and must not
    # appear here, or the two rules are running at once and the report names the wrong cause.
    assert LANDING_UPDATE_TYPE_UNPARSEABLE not in answer.refusals
    assert LANDING_UPDATE_TYPE_NOT_PERMITTED not in answer.refusals


def test_an_ecosystem_the_version_does_not_exclude_is_admitted(migrated_session: Session) -> None:
    """The exclusion refuses what it NAMES and nothing else -- otherwise the term is an
    always-refuse wearing a set, and the five above would move for the wrong reason."""
    gateway = FakeEstateGateway(pull=pull_request(title=ACTIONS_TITLE, head_ref=ACTIONS_REF))
    answer = _ask(
        migrated_session,
        record=_outcome_record(excluded=frozenset({"docker"})),
        gateway=gateway,
    )

    assert answer.satisfied, answer.refusals


def test_the_outcome_rule_does_not_bypass_the_rollout_pin(migrated_session: Session) -> None:
    """The second guard, and the one that is byte-precise rather than keyed on a name.

    The exclusion reads the branch the update bot wrote; the pin reads the workflow's bytes at the
    head. A pull request whose own diff edits the pinned workflow is refused by the pin whatever
    ecosystem it claims and whoever authored it, and widening the delta rule must not reach it.
    """
    gateway = FakeEstateGateway(blob="0000000000000000000000000000000000000000")
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ROLLOUT_MOVED in answer.refusals


@pytest.mark.parametrize(
    "head_ref",
    ["main", "dependabot", "dependabot/", "dependabot/uv", "dependabot/uv/", "dependabot//x"],
    ids=["plain", "prefix-only", "prefix-slash", "no-remainder", "empty-remainder", "empty-eco"],
)
def test_a_branch_that_names_no_ecosystem_REFUSES(migrated_session: Session, head_ref: str) -> None:
    """Fail closed, and the reason is the ledger's own: the update bot always names an ecosystem,
    so a name this cannot read never means "there was none". It means this program could not read
    what the exclusion is about, and permitting on that lands a change whose exclusion nobody can
    re-check."""
    gateway = FakeEstateGateway(pull=pull_request(head_ref=head_ref))
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ECOSYSTEM_UNREADABLE in answer.refusals


@pytest.mark.parametrize(
    ("head_ref", "expected"),
    [
        ("dependabot/uv/uvicorn-standard--gte-0.52.4", "uv"),
        ("dependabot/pip/fastmcp-gte-3.4.7-and-lt-4", "pip"),
        ("dependabot/github_actions/actions/checkout-5", "github_actions"),
        ("dependabot/npm_and_yarn/zod-4.4.3", "npm_and_yarn"),
        ("dependabot/docker/python-3.14-slim", "docker"),
        ("main", None),
        ("feature/dependabot/uv/x", None),
        ("dependabot/uv", None),
    ],
)
def test_the_ecosystem_is_read_from_the_branch(head_ref: str, expected: str | None) -> None:
    """The second segment, which is the same fact the estate's landing ledger reads.

    **From the BRANCH, where the version delta is read from the TITLE, and the two are not in
    tension.** The bot rewrites a pull request in place when a newer version appears, which is
    what makes the branch stale about the VERSION; it cannot go stale about the ecosystem, because
    an update never moves between them. Every branch above except the last three is a real one.
    """
    assert ecosystem_of(head_ref) == expected


def test_the_excluded_ecosystem_is_spelled_the_way_a_BRANCH_spells_it() -> None:
    """An underscore, and this assertion is not pedantry.

    The estate's other lane carries a gate revision that compared the HYPHENATED spelling against
    this exact value, so it permitted nothing while reading as though it permitted more -- and the
    registry transcribes that literal rather than correcting it, precisely so the defect stays
    visible. A term keyed on the wrong spelling here would over-refuse rather than over-permit,
    which is the safe direction and therefore the one nobody notices.
    """
    assert WORKFLOW_AUTOMATION == "github_actions"
    assert ecosystem_of(ACTIONS_REF) == WORKFLOW_AUTOMATION


@pytest.mark.parametrize(
    ("served", "branch_segment"),
    [
        ("GitHub_Actions", "github_actions"),
        ("github_actions", "GitHub_Actions"),
        ("GITHUB_ACTIONS", "github_actions"),
    ],
    ids=["served-cased", "branch-cased", "served-upper"],
)
def test_the_exclusion_folds_case_on_BOTH_sides(
    migrated_session: Session, served: str, branch_segment: str
) -> None:
    """Every other identity key crossing this boundary folds case, and this one must too.

    The repository is folded on entry to the admission, and the rollout pin's key is folded when it
    is parsed and again when it is looked up. This key was compared raw on both sides, and the
    direction of that failure is PERMISSIVE: a member differing only in case is not `in` the set,
    and not-in means admitted. It is the hyphen/underscore near-miss one character over, in the
    lane where the ecosystem sits on the excluding side rather than the permitting one.
    """
    gateway = FakeEstateGateway(
        pull=pull_request(
            title=ACTIONS_TITLE, head_ref=f"dependabot/{branch_segment}/actions/checkout-5"
        )
    )
    answer = _ask(
        migrated_session,
        record=_outcome_record(excluded=frozenset({served})),
        gateway=gateway,
    )

    assert not answer.satisfied
    assert LANDING_ECOSYSTEM_EXCLUDED in answer.refusals


def test_a_GROUPED_bump_is_admitted_under_the_outcome_rule(migrated_session: Session) -> None:
    """The widening beyond the population ADR-0036 measured, asserted so it is not a surprise.

    `update_type_of` answers None for FOUR populations, not one: a requirement range, a grouped
    bump, a downgrade, and a version string it cannot parse. The five subjects were requirement
    ranges; a grouped bump changes several packages at once and is a materially wider act. It is
    admitted on the same ground as the rest -- what decides is whether the required checks passed,
    and they exercised every package in the group -- but it is stated here and in the decision
    rather than arriving as a consequence nobody wrote down.
    """
    gateway = FakeEstateGateway(
        pull=pull_request(
            title="build(deps): bump the minor-and-patch group across 1 directory with 5 updates",
            head_ref="dependabot/pip/minor-and-patch-a1b2c3d4e5",
        )
    )
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert answer.satisfied, answer.refusals


def test_a_record_approved_under_the_previous_version_is_refused_until_re_approved(
    migrated_session: Session,
) -> None:
    """Expected on any version bump, and asserted rather than discovered on the night.

    The landing binds an approval to the version IN FORCE, which is the only mechanism by which
    moving the policy binds an approval that already exists. For an open pull request the producer
    re-approves a still-conforming record on its next pass, so the binding lifts without anyone
    looking -- this is a widening, so every held record still conforms.
    """
    gateway = FakeEstateGateway(pull=pull_request(title=STUCK[0][0], head_ref=STUCK[0][1]))

    stale = _ask(migrated_session, record=_outcome_record(policy_version=4), gateway=gateway)
    assert not stale.satisfied
    assert LANDING_POLICY_VERSION_SUPERSEDED in stale.refusals

    fresh = _ask(migrated_session, record=_outcome_record(), gateway=gateway)
    assert fresh.satisfied, fresh.refusals


def test_the_outcome_rule_still_asks_every_other_condition(migrated_session: Session) -> None:
    """A grant on one term is not a grant on the composition. Freshness and the checks are
    unchanged by ADR-0036, and a version-5 record misses them exactly as a version-4 one does."""
    gateway = FakeEstateGateway(
        pull=pull_request(title=STUCK[0][0], head_ref=STUCK[0][1], mergeable_state="blocked"),
        behind=3,
        runs=(run("completed", "failure"),),
    )
    answer = _ask(migrated_session, record=_outcome_record(), gateway=gateway)

    assert not answer.satisfied
    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert LANDING_CHECKS_NOT_CLEAN in answer.refusals


# ---------------------------------------------------------------------------
# The rollout pin.
# ---------------------------------------------------------------------------


def test_a_moved_rollout_workflow_refuses(migrated_session: Session) -> None:
    """What a green rollout attests is what the record's criteria say. If the bytes producing it
    have changed, the criteria describe something that no longer runs."""
    gateway = FakeEstateGateway(blob="0000000000000000000000000000000000000000")
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ROLLOUT_MOVED in answer.refusals


def test_the_composed_answer_CARRIES_the_base_comparison_that_produced_the_refusal(
    migrated_session: Session,
) -> None:
    """ADR-0024. One refusal, two causes, and only the term that read the blobs can tell them
    apart -- so the comparison travels with the answer rather than being re-derived by whoever
    reads it. The reporting agent cannot derive it at all: it reads no repository.

    Both directions, because a field hard-coded to either value passes a single-direction check.
    """
    stale_head = _ask(migrated_session, gateway=FakeEstateGateway(head_blob="f" * 40, behind=3))
    moved_workflow = _ask(migrated_session, gateway=FakeEstateGateway(blob="0" * 40, behind=3))

    assert LANDING_ROLLOUT_MOVED in stale_head.refusals
    assert stale_head.rollout_base_matches_pin is True
    assert LANDING_ROLLOUT_MOVED in moved_workflow.refusals
    assert moved_workflow.rollout_base_matches_pin is False


def test_a_pull_request_THAT_EDITS_the_rollout_workflow_refuses(migrated_session: Session) -> None:
    """The case a base-only pin cannot see, and the reason the head is read too.

    The base still carries the pinned bytes -- it is unchanged until the landing -- so every term
    passes and the squash then lands the edit, after which `on: push` fires bytes nobody
    transcribed under criteria written for bytes that no longer exist. That is the state this
    condition exists to prevent, reachable through the condition itself. Nothing in the cascade can
    see a pull request's changed files, so reading the head is the whole of the protection.
    """
    gateway = FakeEstateGateway(blob=ROLLOUT_BLOB, head_blob="f" * 40)
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_ROLLOUT_MOVED in answer.refusals
    assert gateway.blobs == [
        (REPOSITORY, ROLLOUT_PATH, "main"),
        (REPOSITORY, ROLLOUT_PATH, HEAD),
    ], "the pin must be read at BOTH the base and the head"


def test_a_pull_request_that_deletes_the_rollout_workflow_refuses(
    migrated_session: Session,
) -> None:
    """The same shape one step further: the pinned path names no file at the head."""
    gateway = FakeEstateGateway(blob=ROLLOUT_BLOB, head_blob=None)

    assert LANDING_ROLLOUT_MOVED in _ask(migrated_session, gateway=gateway).refusals


def test_ONCE_A_HEAD_IS_CURRENT_the_pin_tells_the_two_causes_apart(
    migrated_session: Session,
) -> None:
    """What the branch-update carve-out rests on, asserted where the pin is read.

    One refusal, `landing_rollout_moved`, has two causes: a head that predates the file's last
    change, and a head whose own diff edits it. Both rows below are a pull request that is no
    longer behind -- the state bringing a branch up to date produces -- and they answer
    differently. The first now carries the pinned bytes and every term is met; the second still
    differs and is still refused. Were they indistinguishable after freshening, excusing the
    refusal in order to permit it would be excusing it permanently.
    """
    stale_head_now_current = _ask(migrated_session, gateway=FakeEstateGateway(behind=0))
    edits_the_workflow = _ask(
        migrated_session, gateway=FakeEstateGateway(behind=0, head_blob="f" * 40)
    )

    assert stale_head_now_current.satisfied, stale_head_now_current.refusals
    assert not edits_the_workflow.satisfied
    assert LANDING_ROLLOUT_MOVED in edits_the_workflow.refusals


def test_mergeability_the_remote_has_not_computed_yet_is_named_apart_from_a_red_check(
    migrated_session: Session,
) -> None:
    """GitHub answers `unknown` while it works. Reporting that as "the checks are not clean" names
    the wrong cause to whoever reads the report -- and its remedy is to ask again, which no other
    refusal's is."""
    gateway = FakeEstateGateway(pull=pull_request(mergeable_state="unknown"))
    answer = _ask(migrated_session, gateway=gateway)

    assert not answer.satisfied
    assert LANDING_MERGEABILITY_UNKNOWN in answer.refusals
    assert LANDING_CHECKS_NOT_CLEAN not in answer.refusals


def test_a_deleted_or_renamed_rollout_workflow_refuses(migrated_session: Session) -> None:
    """A pinned path naming no file is a moved rollout. Reading it as "nothing to compare" would
    waive the condition exactly when it matters most."""
    gateway = FakeEstateGateway(blob=None)

    assert LANDING_ROLLOUT_MOVED in _ask(migrated_session, gateway=gateway).refusals


def test_a_repository_with_no_pin_refuses(migrated_session: Session) -> None:
    """A policy version that declares no pin is one that predates the condition. "Nobody said
    which bytes" is not "these bytes are fine"."""
    answer = _ask(migrated_session, record=approved(landing_conditions=conditions(pins={})))

    assert not answer.satisfied
    assert LANDING_ROLLOUT_UNPINNED in answer.refusals


def test_the_blob_is_compared_case_insensitively(migrated_session: Session) -> None:
    """GitHub serves object names lower-cased and a human transcribing one may not. Two spellings
    of one blob must not read as a moved rollout."""
    pins = {REPOSITORY: WorkflowPin(path=ROLLOUT_PATH, blob_sha=ROLLOUT_BLOB.upper())}
    answer = _ask(migrated_session, record=approved(landing_conditions=conditions(pins=pins)))

    assert answer.satisfied, answer.refusals


def test_a_pin_is_found_however_the_repository_is_spelled() -> None:
    """`pin_for` folds case as the record's own identity key does, so a repository named
    `AlobarQuest/change-manager` in one place and `alobarquest/change-manager` in another is one
    repository rather than an unpinned one."""
    pinned = conditions()

    assert pinned.pin_for(REPOSITORY.upper()) is not None
    assert pinned.pin_for(REPOSITORY) is not None
    assert pinned.pin_for("alobarquest/brain") is None


def test_an_unreadable_rollout_blob_refuses(migrated_session: Session) -> None:
    gateway = FakeEstateGateway(blob_error=EstateGatewayError("read_status", 500))

    assert LANDING_ROLLOUT_UNREADABLE in _ask(migrated_session, gateway=gateway).refusals


# ---------------------------------------------------------------------------
# The composed answer.
# ---------------------------------------------------------------------------


def test_no_unmet_answer_is_silent(migrated_session: Session) -> None:
    """`satisfied` is a positive conjunction, so an unmet answer with an empty refusal list would
    be a refusal nobody could act on. Asserted over a spread of independently unmet cases."""
    cases = [
        {"enabled": False},
        {"record": ChangeRecordAnswer(True)},
        {"moment": OUT_OF_WINDOW},
        {"gateway": FakeEstateGateway(behind=3)},
        {"landing": inert_source()},
    ]
    for case in cases:
        answer = _ask(migrated_session, **case)  # type: ignore[arg-type]
        assert not answer.satisfied, case
        assert answer.refusals, case


def test_a_record_with_no_readable_identifier_refuses(migrated_session: Session) -> None:
    """The landing writes the record's identifier into the squash body, where the estate's ledger
    reads it back to classify the landing. Without one it would write a placeholder the ledger's
    parse matches nothing in, and the landing would record as having no accountable basis at all
    -- the class nothing reads. Refused here rather than discovered there.
    """
    answer = _ask(migrated_session, record=approved(record_id=None))

    assert not answer.satisfied
    assert LANDING_RECORD_UNIDENTIFIED in answer.refusals


def test_the_repository_is_folded_so_the_report_and_the_act_ask_one_question(
    migrated_session: Session,
) -> None:
    """The acting path lowercased before calling in and the reporting route did not, which is two
    surfaces asking the estate and the record service a DIFFERENT question about one repository.

    Records carry both spellings -- production holds `AlobarQuest/change-manager` and
    `alobarquest/change-manager` -- and the record's own identity key folds case, so the fold has
    to happen somewhere both surfaces reach. Asserted on what the sources were ASKED, because the
    answer alone cannot tell a folded lookup from a fake that ignores its argument.
    """
    landing = redeploying_source()
    records = FakeChangeRecordSource({(REPOSITORY, PR): approved()})
    gateway = FakeEstateGateway()

    answer = estate_landing_admission(
        migrated_session,
        "AlobarQuest/Change-Manager",
        PR,
        landing,
        records,
        gateway,
        enabled=True,
        credentials_configured=True,
        clock=FixedClock(IN_WINDOW),
    )

    assert landing.asked == [REPOSITORY]
    assert records.asked == [(REPOSITORY, PR)]
    assert gateway.reads == [(REPOSITORY, PR)]
    assert answer.repository == REPOSITORY
    assert answer.satisfied, answer.refusals


def test_a_gateway_failure_names_the_status_when_the_remote_answered() -> None:
    """Both branches, because either alone passes under the defect the pair exists to catch.

    Rendering the code alone was the defect: three raise sites carry a status and every message
    dropped it. Rendering a status unconditionally would be the mirror defect -- most raisers
    never reach the remote at all, and a number there would claim it answered when it did not.
    """
    assert gateway_failure_detail(EstateGatewayError("branch_update_status", 403)) == (
        "branch_update_status (HTTP 403)"
    )
    assert gateway_failure_detail(EstateGatewayError("request_error:ConnectError")) == (
        "request_error:ConnectError"
    )


# --------------------------------------------------------------------------------------------
# The predicate: a category rule, pinned in both directions.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("base_matches", [False, True])
def test_freshness_alone_qualifies(base_matches: bool) -> None:
    """Pinned over BOTH values of the carve-out's fact, because it is irrelevant here and must
    stay so: no rollout refusal was raised, so nothing is being excused either way. A predicate
    that conditioned its whole answer on the base comparison rather than its one carve-out would
    pass a single-value assertion and fail this."""
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE,), rollout_base_matches_pin=base_matches
    )


@pytest.mark.parametrize("deliberate", sorted(DELIBERATE_REFUSALS))
def test_freshness_beside_a_refusal_that_clears_itself_qualifies(deliberate: str) -> None:
    """The pace resets and the clock moves. Neither says anything about the branch, so neither is
    a reason to leave it behind -- and both co-occur constantly, the pace on every sibling once a
    landing has happened, which is precisely the population this lane exists to unstick."""
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, deliberate), rollout_base_matches_pin=False
    )


def test_every_deliberate_refusal_together_with_freshness_still_qualifies() -> None:
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, *DELIBERATE_REFUSALS),
        rollout_base_matches_pin=False,
    )


@pytest.mark.parametrize(
    "real",
    [
        LANDING_CHECKS_NOT_CLEAN,
        LANDING_UPDATE_TYPE_UNPARSEABLE,
        "landing_record_absent",
        # A CONFLICT IS THE SHARPEST MEMBER, because for it the update does not merely buy nothing
        # -- the call fails at the remote. It reached this list on 2026-09-05 when it stopped being
        # reported as an unclean check, and it disqualified under both names: the subtraction below
        # names a few and refuses everything else, so a renamed refusal cannot become permission.
        LANDING_PULL_REQUEST_CONFLICTED,
        # And the state nobody has named yet, which is the whole point of having a name for it.
        LANDING_MERGEABILITY_UNRECOGNISED,
    ],
)
def test_freshness_beside_a_real_condition_does_NOT_qualify(real: str) -> None:
    """THE MUTANT THIS KILLS is "any refusal set containing freshness qualifies". Each of these
    pull requests is behind its base and cannot land whatever is done to its branch, so a build
    spent on it buys nothing and reads as progress to whoever sees it running."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, real), rollout_base_matches_pin=False
    )


def test_the_remainder_is_a_CATEGORY_and_not_a_COUNT() -> None:
    """TWO deliberate refusals beside freshness qualify; ONE real one does not.

    Built so the counts point the wrong way: the qualifying set has three members and the refusing
    set has two. A rule keyed on "at most one other refusal", or on any count at all, gets both of
    these backwards.
    """
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_PACE_EXHAUSTED, LANDING_OUTSIDE_CHANGE_WINDOW),
        rollout_base_matches_pin=False,
    )
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_NOT_CLEAN),
        rollout_base_matches_pin=False,
    )


def test_a_refusal_NOBODY_HAS_CLASSIFIED_does_not_qualify() -> None:
    """The polarity the whole lane argues for: a code a later increment invents and forgets to
    classify must fail toward leaving the branch alone, never toward touching it."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, "landing_something_nobody_has_thought_of"),
        rollout_base_matches_pin=False,
    )


@pytest.mark.parametrize(
    "refusals",
    [
        (),
        (LANDING_PACE_EXHAUSTED,),
        (LANDING_OUTSIDE_CHANGE_WINDOW, LANDING_PACE_EXHAUSTED),
        (LANDING_CHECKS_NOT_CLEAN,),
    ],
)
def test_without_freshness_there_is_NOTHING_TO_DO_and_it_does_not_qualify(
    refusals: tuple[str, ...],
) -> None:
    """THE SECOND MUTANT: dropping the requirement that freshness be present at all.

    An empty refusal set is the sharpest case -- that is a pull request about to LAND, and a rule
    that acted on it would push a commit onto a branch seconds before squashing it.
    """
    assert not qualifies_for_branch_update(refusals, rollout_base_matches_pin=False)


# --------------------------------------------------------------------------------------------
# The carve-out: a rollout pin that differs BECAUSE the head is stale.
#
# A single positive case cannot tell this narrow rule apart from suppressing the refusal
# unconditionally, so each of the three below denies a different half of it.
# --------------------------------------------------------------------------------------------


def test_a_rollout_pin_that_differs_BECAUSE_THE_HEAD_IS_STALE_qualifies() -> None:
    """THE DEADLOCK, in the shape it had in production on 2026-08-16.

    `brain#33`, `#34` and `#35` were opened before `brain#47` changed `ci.yml`, so each head still
    carried the previous bytes while the base carried the pinned ones. The pin term refused them
    for a difference that being behind had caused, and the freshness rule -- reading that refusal
    as an obstacle like any other -- declined to clear the very staleness producing it. Neither
    would ever have moved on its own.
    """
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED),
        rollout_base_matches_pin=True,
    )


def test_a_rollout_pin_that_GENUINELY_MOVED_does_not_qualify() -> None:
    """THE HALF THE POSITIVE CASE CANNOT PROVE, and the reason the fact is an argument at all.

    Identical refusals; only the base comparison differs. Here the base does NOT carry the pinned
    bytes, so the workflow this record was written about is not the one that would run -- a
    condition no amount of freshening touches, since the head would simply be brought up to date
    with the wrong bytes. Suppressing the refusal unconditionally passes the case above and fails
    this one, which is the whole distinction between a rule and a suppression list.
    """
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED),
        rollout_base_matches_pin=False,
    )


def test_a_pull_request_that_EDITS_the_workflow_AND_IS_CURRENT_does_not_qualify() -> None:
    """The other half: the base matches, and the head is NOT behind.

    So the difference cannot be staleness -- this pull request's own diff is what moved the bytes,
    which is `_rollout_term`'s founding case. There is also nothing to do: a head already current
    with its base gains nothing from being brought up to date. This is the control that keeps the
    return's own freshness condition load-bearing; drop it and this exact shape qualifies, and the
    lane spends a build on a pull request it has just declined to land.
    """
    assert not qualifies_for_branch_update((LANDING_ROLLOUT_MOVED,), rollout_base_matches_pin=True)


@pytest.mark.parametrize(
    "other",
    [LANDING_CHECKS_NOT_CLEAN, LANDING_UPDATE_TYPE_UNPARSEABLE, "landing_something_unclassified"],
)
def test_the_carve_out_excuses_ONE_refusal_and_nothing_beside_it(other: str) -> None:
    """A carve-out that widened to "a stale pin means stop reading the rest" would pass every case
    above. `brain#31` and `#32` are exactly this shape in production -- behind, stale pin, AND a
    requirement-range title -- and they are permanent exceptions that must never be touched."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED, other),
        rollout_base_matches_pin=True,
    )


# --------------------------------------------------------------------------------------------
# The criterion itself (ADR-0024), read directly rather than through either consumer.
#
# `qualifies_for_branch_update` cannot see the head-behind conjunct at all -- its own return
# requires the same fact -- so the criterion needs controls of its own or half of it is pinned by
# nothing on this side of the boundary.
# --------------------------------------------------------------------------------------------


def test_a_stale_rollout_pin_is_freshness_derived_when_the_base_carries_the_pinned_bytes() -> None:
    assert freshness_derived_refusals(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED), rollout_base_matches_pin=True
    ) == frozenset({LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED})


def test_a_rollout_pin_whose_base_DIFFERS_is_not_freshness_derived() -> None:
    """The workflow genuinely moved. Identical refusals; only the base comparison differs, which is
    the whole reason that fact is an argument."""
    assert freshness_derived_refusals(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED), rollout_base_matches_pin=False
    ) == frozenset({LANDING_HEAD_NOT_CURRENT_WITH_BASE})


def test_nothing_is_freshness_derived_when_the_head_is_NOT_BEHIND() -> None:
    """THE CONJUNCT, and this is the only control on this side that sees it.

    A refusal cannot be caused by a position the head is not in. Here the base carries the pinned
    bytes and the head is current, so the pin differs because THIS PULL REQUEST'S OWN DIFF edits
    the workflow -- `_rollout_term`'s founding case. `qualifies_for_branch_update` is blind to the
    conjunct because its return already requires the head to be behind; the reporting consumer has
    no such guard, and dropping it there silences exactly this shape.
    """
    assert (
        freshness_derived_refusals((LANDING_ROLLOUT_MOVED,), rollout_base_matches_pin=True)
        == frozenset()
    )


def test_a_FAILING_CHECK_is_never_freshness_derived() -> None:
    """The case that keeps the criterion narrow. Freshening re-runs checks and might turn one
    green, so "would freshening clear it?" would admit it; "does it say anything about the change?"
    does not. This also pins the intersection with what was actually raised: without it the answer
    would name a rollout refusal nobody raised."""
    assert freshness_derived_refusals(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_NOT_CLEAN),
        rollout_base_matches_pin=True,
    ) == frozenset({LANDING_HEAD_NOT_CURRENT_WITH_BASE})


def test_an_UNANSWERED_CHECK_does_not_disqualify_a_stale_branch() -> None:
    """The deadlock this change exists to break. Nothing else in the estate re-runs an abandoned
    check, so a pull request holding one waits forever -- and bringing the branch up to date is the
    act that answers it."""
    assert qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_AWAITING_VERDICT),
        rollout_base_matches_pin=False,
    )


def test_an_UNANSWERED_CHECK_ALONE_does_not_qualify_anything() -> None:
    """THE OTHER HALF OF THE PAIR, and it is the one that catches an excuse written too widely.
    A head that is current with its base is not stale, so there is nothing to bring up to date and
    no act this refusal could be excused by. Both cases are needed: a subtraction that ignored the
    head's position would pass the case above and fail here."""
    assert not qualifies_for_branch_update(
        (LANDING_CHECKS_AWAITING_VERDICT,), rollout_base_matches_pin=False
    )


def test_a_FAILING_CHECK_still_disqualifies_a_stale_branch() -> None:
    """The boundary that makes the split worth having. Freshening cannot turn a red verdict green,
    so offering it a build spends one to re-learn the same answer -- and a build running reads as
    progress. A subtraction that took the whole checks family rather than the one code would pass
    every test above and lose this."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_NOT_CLEAN),
        rollout_base_matches_pin=False,
    )


def test_a_check_STILL_RUNNING_disqualifies_a_stale_branch() -> None:
    """Bringing the branch up to date would abandon the very run whose verdict is awaited, turning
    a question that answers itself in minutes into one that needs another build."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_IN_FLIGHT),
        rollout_base_matches_pin=False,
    )


def test_runs_that_could_not_be_read_disqualify_a_stale_branch() -> None:
    """Which of the three holds is unknown, so acting would be acting on a question nobody asked."""
    assert not qualifies_for_branch_update(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_VERDICT_UNREADABLE),
        rollout_base_matches_pin=False,
    )


def test_an_unanswered_check_is_NOT_a_member_of_the_freshness_criterion() -> None:
    """It is excused for ACTING and not for REPORTING, which is why it is a separate subtraction
    rather than a fifth member here. The criterion asks whether the head's POSITION caused the
    refusal; an abandoned run is not a position, and folding it in would also make it quiet beside
    a permanent exception, where it is still worth saying."""
    assert freshness_derived_refusals(
        (LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_CHECKS_AWAITING_VERDICT),
        rollout_base_matches_pin=True,
    ) == frozenset({LANDING_HEAD_NOT_CURRENT_WITH_BASE})


def test_the_deliberate_refusals_are_exactly_the_landers_own() -> None:
    """TWO PACKAGES, ONE VOCABULARY. The lander cannot import the orchestrator -- it is isolated
    from it on purpose -- so the two copies can only be held together from outside, and this
    estate's standing lesson is that wherever two vocabularies must agree they do not, until
    something checks. A test may import both; the program may not.
    """
    from estate_lander.cli import _DELIBERATE

    assert DELIBERATE_REFUSALS == _DELIBERATE


def test_the_freshness_refusal_the_lander_classifies_on_is_exactly_the_one_composed_here() -> None:
    """The same two-package problem one string over, and this one is INVISIBLE to the scanner.

    `test_cross_boundary_vocabulary` finds vocabularies by AST-scanning for module-level string
    COLLECTIONS of two or more members, so a lone string shared across a boundary is exactly the
    shape it cannot see. The lander suppresses on this code by name; the orchestrator composes it.
    Rename it on either side with nothing holding them together and the lander silently stops
    recognising the condition it exists to classify -- reporting `#48` as a finding forever, which
    is the state this increment was written to end.
    """
    from estate_lander.cli import _FRESHNESS

    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE == _FRESHNESS


def test_the_rollout_refusal_the_lander_classifies_on_is_exactly_the_one_composed_here() -> None:
    """The second lone string, added by ADR-0024 and pinned for the reason above."""
    from estate_lander.cli import _ROLLOUT_MOVED

    assert LANDING_ROLLOUT_MOVED == _ROLLOUT_MOVED


def test_the_WIRE_KEY_the_lander_reads_the_base_comparison_from_is_a_field_this_side_SERVES() -> (
    None
):
    """THE FOURTH CROSS-BOUNDARY STRING, and the only one whose drift both suites would applaud.

    A coordinated rename on THIS side -- dataclass field, response model, construction keyword --
    is exactly what a sweep guided by the served-shape pin produces, and every gate stays green:
    that pin compares a renamed model to a renamed dataclass, and the lander's own tests pass
    because its double serves the lander's literal. Only production sees it, where `.get` returns
    `None` on a key nobody serves, the criterion excuses nothing, and `brain#31`/`#32` are held
    forever with nothing saying why -- the flattering direction the constant's own comment warns
    about.

    Asserted against the SERVICE dataclass rather than the response model: the model is pinned to
    the dataclass one test over, so this is the shorter chain, and it is the dataclass the route
    serializes.
    """
    from estate_lander.cli import _BASE_MATCHES_PIN

    assert _BASE_MATCHES_PIN in EstateLandingAdmission.__dataclass_fields__


def test_the_exception_the_lander_suppresses_beside_is_exactly_the_one_composed_here() -> None:
    """The CONDITION of the suppression, which this increment made load-bearing twice over.

    `_EXCEPTION` already decided a line's status; it now also decides whether being behind is
    reported at all. A one-member frozenset is below the two-member threshold the AST scanner
    matches on, and the scanner only walks `src/orchestrator/` besides, so this agreement was held
    together by nothing.

    Rename the code on the orchestrator side and the lander stops classifying `#48` as an exception
    AND stops suppressing its freshness refusal -- returning it to a permanent nightly finding,
    which is precisely the state this increment exists to end. Set equality rather than membership:
    a member ADDED to `_EXCEPTION` and pinned to nothing is the same hole one element over.
    """
    from estate_lander.cli import _EXCEPTION

    assert _EXCEPTION == frozenset({LANDING_UPDATE_TYPE_UNPARSEABLE})


def test_the_two_copies_of_the_freshness_criterion_AGREE_POINTWISE() -> None:
    """ADR-0024's "one concept, two consumers", held together from outside.

    Set equality would not do here: the criterion is a FUNCTION of two arguments, not a list, so
    what has to agree is every answer it gives. This walks the whole cross product of the refusal
    vocabulary and both values of the base comparison -- 64 subsets by two -- so a copy that drifts
    on any single input reddens, including the conjunct that only one input can see.

    The lander may not import the orchestrator; a test may import both. Same mechanism as
    `_DELIBERATE` above, one function rather than one set.
    """
    from itertools import combinations

    from estate_lander.cli import _freshness_derived

    vocabulary = (
        LANDING_HEAD_NOT_CURRENT_WITH_BASE,
        LANDING_ROLLOUT_MOVED,
        LANDING_CHECKS_NOT_CLEAN,
        LANDING_CHECKS_AWAITING_VERDICT,
        LANDING_UPDATE_TYPE_UNPARSEABLE,
        LANDING_PACE_EXHAUSTED,
        "landing_something_nobody_has_thought_of",
    )
    subsets = [
        subset for size in range(len(vocabulary) + 1) for subset in combinations(vocabulary, size)
    ]

    for subset in subsets:
        for base_matches in (False, True):
            assert freshness_derived_refusals(
                subset, rollout_base_matches_pin=base_matches
            ) == _freshness_derived(set(subset), rollout_base_matches_pin=base_matches), (
                subset,
                base_matches,
            )


def test_the_served_answer_DECLARES_the_verdict_the_caller_reads() -> None:
    """A `response_model` silently DROPS every key the service returns and the model does not
    name, with no error anywhere -- so "the service computes it" is never evidence "the caller
    receives it". This estate has already shipped that exact defect once, on the runner brief,
    where every service-level assertion passed and the wire carried nothing.

    Asserted as SET EQUALITY over the whole answer rather than as a membership check for this
    increment's one field, because the failure is not specific to this field: any future addition
    to the composed answer has the same silent hole, and a membership check would not see it.
    """
    from orchestrator.api.schemas import EstateLandingAdmissionResponse
    from orchestrator.services.estate_landing_admission import EstateLandingAdmission

    assert set(EstateLandingAdmissionResponse.model_fields) == set(
        EstateLandingAdmission.__dataclass_fields__
    )
