"""The act: bringing a stale branch up to date with the base the lane itself moved.
ADR-0019 Increment 6.

Everything runs with no network. **The assertions that matter most are the ones about what did
NOT happen**, and specifically that `gateway.branch_updates` stayed empty: a test that only checks
the raised error would pass against an implementation that touched the branch first and complained
afterwards, which is this repository's standing lesson about the report surface and the acting
surface being different tests.

The rule under test is a CATEGORY rule, so it is exercised as one. A single positive case cannot
tell "freshness is the sole remaining obstacle" apart from "anything containing freshness", and a
single negative case cannot tell it apart from "at most one refusal" -- so both directions are
pinned with sets built to make the count uninformative.

Persistence is asserted through a SECOND SESSION, never by re-reading the one that wrote: a
flushed-but-uncommitted row is visible to its own transaction, so an in-session re-read passes
under the exact defect it would be written to catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event
from orchestrator.services import estate_pr_merge
from orchestrator.services.estate_landing_admission import (
    DELIBERATE_REFUSALS,
    LANDING_CHECKS_AWAITING_VERDICT,
    LANDING_CHECKS_IN_FLIGHT,
    LANDING_CHECKS_NOT_CLEAN,
    LANDING_CHECKS_VERDICT_UNREADABLE,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_MERGEABILITY_UNRECOGNISED,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    LANDING_PULL_REQUEST_CONFLICTED,
    LANDING_ROLLOUT_MOVED,
    LANDING_UPDATE_TYPE_UNPARSEABLE,
    EstateGatewayError,
    EstateLandingAdmission,
    freshness_derived_refusals,
    qualifies_for_branch_update,
)
from orchestrator.services.estate_pr_branch_update import (
    BRANCH_UPDATE_ACTION,
    BRANCH_UPDATE_HEAD_MOVED,
    BRANCH_UPDATE_NOT_QUALIFIED,
    BRANCH_UPDATE_REFUSED_BY_REMOTE,
    BRANCH_UPDATE_SIBLING_HOLDING,
    BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    BRANCH_UPDATE_SUBJECT,
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
from orchestrator.services.estate_pr_merge import GitHubEstatePullRequests
from orchestrator.services.lifecycle import ActorContext
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import inert_source, redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    FakeEstateGateway,
    SiblingGateway,
    approved,
    dependabot_commit,
    foreign_commit,
    pull_request,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 49
IN_WINDOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 8, 11, 19, 30, tzinfo=UTC)

# A requirement-range bump: the real title of the pull request this lane must never touch. It
# states no single version delta, so no rule about update types can apply to it, and no amount of
# bringing its branch up to date will ever make it landable.
RANGE_TITLE = "build(deps): update uvicorn[standard] requirement from >=0.51.0 to >=0.52.1"

# A minor bump whose checks are red. Behind its base AND unlandable, like the one above, but for a
# reason that lives somewhere else entirely -- so the two together show the rule keying on the
# category rather than on any one code.
MINOR_TITLE = "build(deps): bump alembic from 1.18.5 to 1.19.0"


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


def _update(
    session: Session,
    *,
    gateway: FakeEstateGateway,
    actor: ActorContext = SYSTEM,
    record=None,
    landing=None,
    enabled: bool = True,
    credentials: bool = True,
    moment: datetime = IN_WINDOW,
    expected_head: str = HEAD,
    key: str = "branch-update-1",
    record_source: FakeChangeRecordSource | None = None,
):
    return update_estate_pull_request_branch(
        session,
        EstateBranchUpdateCommand(
            repository=REPOSITORY,
            pr_number=PR,
            actor=actor,
            idempotency_key=key,
            expected_head_sha=expected_head,
        ),
        gateway,
        landing or redeploying_source(),
        record_source or FakeChangeRecordSource({(REPOSITORY, PR): record or approved()}),
        enabled=enabled,
        credentials_configured=credentials,
        clock=FixedClock(moment),
    )


def _behind(**kwargs) -> FakeEstateGateway:
    """A pull request that is behind its base and otherwise perfectly landable."""
    return FakeEstateGateway(behind=3, **kwargs)


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


# --------------------------------------------------------------------------------------------
# The act.
# --------------------------------------------------------------------------------------------


def test_a_branch_held_only_by_freshness_is_brought_up_to_date(migrated_session: Session) -> None:
    gateway = _behind()

    outcome = _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]
    assert outcome.repository == REPOSITORY
    assert outcome.pr_number == PR
    assert outcome.head_sha == HEAD


def test_a_branch_also_held_by_the_pace_is_brought_up_to_date(migrated_session: Session) -> None:
    """The ordinary case, and the one the lane creates for itself: something landed tonight, so
    every sibling is behind AND has no landing left in this window. Both facts clear by morning
    and neither is a reason to leave the branch stale."""
    migrated_session.add(_landed_tonight())
    migrated_session.flush()
    gateway = _behind()

    _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


def test_a_branch_is_brought_up_to_date_OUTSIDE_the_change_window_too(
    migrated_session: Session,
) -> None:
    """Pinned as a PAIR with the in-window case above, because a term that never reads its clock
    agrees with a single out-of-window assertion for most of the day.

    That this fires at any hour is deliberate and worth stating: the window governs changing
    something already serving, and this changes a topic branch that serves nothing. Doing it in
    daylight means the checks are green well before the window opens.
    """
    gateway = _behind()

    _update(migrated_session, gateway=gateway, moment=OUT_OF_WINDOW)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


@pytest.mark.parametrize(
    ("title", "state"),
    [
        pytest.param(RANGE_TITLE, "clean", id="can-never-be-classified"),
        pytest.param(MINOR_TITLE, "dirty", id="checks-are-red"),
    ],
)
def test_a_branch_that_could_not_land_ANYWAY_is_never_touched(
    migrated_session: Session, title: str, state: str
) -> None:
    """THE STANDING LIVE CONTROL, in the shape it has in production. `#48` is a requirement-range
    bump that is behind its base and can never be classified; a red-checked sibling is behind for
    the same reason and unlandable for a different one. Neither becomes landable because its
    branch moved, so the build each would trigger is spent for nothing.

    The assertion is on the EMPTY call list, not on the error: an implementation that acted and
    then raised would satisfy `pytest.raises` and fail this.
    """
    gateway = _behind(pull=pull_request(title=title, mergeable_state=state))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_branch_that_is_ALREADY_CURRENT_is_never_touched(migrated_session: Session) -> None:
    """Nothing to do. A pull request whose head is current is either about to land or is held on
    something a fresher base cannot fix."""
    gateway = FakeEstateGateway(behind=0)

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_deployment_that_may_not_LAND_may_not_touch_a_branch_either(
    migrated_session: Session,
) -> None:
    """The off-switch, held by the same term rather than by a second one somebody has to
    remember. `landing_not_enabled` is a refusal like any other and is not one that clears
    itself, so a deployment nobody has enabled composes an answer that declines this too."""
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, enabled=False)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_without_credentials_nothing_is_asked_of_the_remote(migrated_session: Session) -> None:
    gateway = _behind()

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway, credentials=False)

    assert gateway.branch_updates == []


# --------------------------------------------------------------------------------------------
# The carve-out, through the surface that actually writes to a repository.
#
# The predicate cases above pass a refusal tuple and a fact directly. These compose both from
# blobs, so they also cover the wiring: a correct predicate reading a fact nobody carried, or
# carrying one that was never observed, is invisible to every case above.
# --------------------------------------------------------------------------------------------


def test_a_branch_whose_STALE_HEAD_moved_the_rollout_pin_is_brought_up_to_date(
    migrated_session: Session,
) -> None:
    """The five `brain` pull requests, composed from the blobs rather than asserted.

    The base carries the pinned bytes and the head carries the previous ones -- measured
    2026-08-16 as base `c5c08871`, head `6cad4cf9` on all five, twelve commits behind.
    """
    gateway = _behind(head_blob="6cad4cf9f03d816ce8bf8fb87fa67d8634486ef1")

    _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


def test_a_branch_whose_BASE_no_longer_carries_the_pinned_bytes_is_never_touched(
    migrated_session: Session,
) -> None:
    """THE WIRING CONTROL. Behind its base, exactly as above, and the workflow genuinely moved.

    A predicate that reads the fact correctly but is handed a hardcoded `True`, or a term that
    reports the base as matching whatever it read, passes every other case in this module and
    fails this one. The assertion is on the empty call list rather than on the error.
    """
    gateway = _behind(blob="0" * 40, head_blob="0" * 40)

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_branch_that_EDITS_the_rollout_workflow_and_is_current_is_never_touched(
    migrated_session: Session,
) -> None:
    """`_rollout_term`'s founding case, and what the carve-out must leave standing."""
    gateway = FakeEstateGateway(behind=0, head_blob="f" * 40)

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_STALE_branch_that_can_never_land_ANYWAY_is_never_touched(
    migrated_session: Session,
) -> None:
    """`brain#31` and `#32`: behind, a stale pin, AND a requirement-range title.

    They are permanent exceptions -- no update makes a range bump classifiable -- and the standing
    instruction about their production twins is that they must not be touched. The carve-out
    excuses the pin refusal and this pull request still has one the lane cannot clear.
    """
    gateway = _behind(
        pull=pull_request(title=RANGE_TITLE),
        head_blob="6cad4cf9f03d816ce8bf8fb87fa67d8634486ef1",
    )

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_NOT_QUALIFIED
    assert gateway.branch_updates == []


def test_a_repository_where_landing_is_INERT_is_not_this_lanes_business(
    migrated_session: Session,
) -> None:
    gateway = _behind()

    with pytest.raises(DomainError):
        _update(migrated_session, gateway=gateway, landing=inert_source())

    assert gateway.branch_updates == []


def test_a_head_that_moved_since_the_caller_read_it_is_refused(migrated_session: Session) -> None:
    """The same claim the landing makes, over the same window: the update bot rewriting its own
    branch between the answer and the request is the ordinary cause, and the next pass reads the
    new head and asks about that one."""
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, expected_head="c" * 40)

    assert raised.value.code == BRANCH_UPDATE_HEAD_MOVED
    assert gateway.branch_updates == []


def test_the_head_the_answer_named_is_the_head_the_remote_is_told_to_expect(
    migrated_session: Session,
) -> None:
    """The whole of the concurrency control. The platform refuses if the branch moved under us,
    which closes the window between deciding and doing without this side having to see the move."""
    gateway = _behind()

    _update(migrated_session, gateway=gateway)

    assert [sha for _, _, sha in gateway.branch_updates] == [HEAD]


@pytest.mark.parametrize("actor", [WORKER, HUMAN], ids=["worker", "human"])
def test_only_the_system_actor_may_bring_a_branch_up_to_date(
    migrated_session: Session, actor: ActorContext
) -> None:
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, actor=actor)

    assert raised.value.code == "role_forbidden"
    assert gateway.branch_updates == []


def test_a_remote_refusal_is_reported_and_bars_NOTHING(migrated_session: Session) -> None:
    """No record, so the next pass composes the answer again and may ask again. That is right for
    an act whose whole nature is that repeating it is harmless -- the opposite of the landing,
    whose row is permanent because its act cannot be retried."""
    gateway = _behind(update_error=EstateGatewayError("branch_update_status", 422))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway)

    assert raised.value.code == BRANCH_UPDATE_REFUSED_BY_REMOTE
    assert (
        migrated_session.scalar(select(Event).where(Event.action == BRANCH_UPDATE_ACTION)) is None
    )


# --------------------------------------------------------------------------------------------
# What is written down.
# --------------------------------------------------------------------------------------------


def test_the_act_is_recorded_as_an_event_and_is_readable_from_ANOTHER_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Asserted through a second session, because a flushed-but-uncommitted row is visible to its
    own transaction -- so an in-session re-read passes under the very defect it would catch."""
    _update(migrated_session, gateway=_behind())

    with Session(migrated_engine) as reader:
        event = reader.scalar(select(Event).where(Event.action == BRANCH_UPDATE_ACTION))
        assert event is not None
        assert event.subject_type == BRANCH_UPDATE_SUBJECT
        assert event.actor_id == "orchestrator-system"
        assert event.payload["repository"] == REPOSITORY
        assert event.payload["pr_number"] == PR
        assert event.payload["head_sha"] == HEAD


def test_a_repeat_replays_the_event_and_never_calls_the_remote_again(
    migrated_session: Session,
) -> None:
    """The idempotency story named in the coverage matrix."""
    first = _update(migrated_session, gateway=_behind())
    gateway = _behind()

    second = _update(migrated_session, gateway=gateway)

    assert gateway.branch_updates == []
    assert (second.repository, second.pr_number, second.head_sha) == (
        first.repository,
        first.pr_number,
        first.head_sha,
    )
    # AND IT SAYS SO. A replay here means the head did not move, which -- because the platform
    # answers 202 and does the work afterwards -- is the shape of it having accepted and not
    # delivered. Unflagged, the caller prints that as a success on every pass forever.
    assert first.replayed is False
    assert second.replayed is True


def test_a_key_spent_on_a_DIFFERENT_subject_is_refused_rather_than_replayed(
    migrated_session: Session,
) -> None:
    """One globally unique column reaches both, so an operator who copies a request and edits only
    the number would otherwise be told the pull request they named was brought up to date when a
    different one had been."""
    _update(migrated_session, gateway=_behind(), key="shared")

    with pytest.raises(DomainError) as raised:
        update_estate_pull_request_branch(
            migrated_session,
            EstateBranchUpdateCommand(
                repository=REPOSITORY,
                pr_number=51,
                actor=SYSTEM,
                idempotency_key="shared",
                expected_head_sha=HEAD,
            ),
            _behind(),
            redeploying_source(),
            FakeChangeRecordSource({(REPOSITORY, 51): approved()}),
            enabled=True,
            credentials_configured=True,
            clock=FixedClock(IN_WINDOW),
        )

    assert raised.value.code == "idempotency_conflict"


def test_a_LATER_update_after_the_base_moves_again_is_never_barred_by_the_first(
    migrated_session: Session,
) -> None:
    """THE PROPERTY THAT MAKES A KEY SAFE ON A REPEATABLE ACT. A successful update changes the
    head, so the caller's next key -- content-addressed over the head -- is a different key. If it
    were not, the second night's landing would stale this branch forever with no way to clear it.
    """
    moved = "d" * 40
    _update(migrated_session, gateway=_behind(), key="branch-update-at-old-head")
    gateway = _behind(pull=pull_request(head_sha=moved))

    _update(
        migrated_session,
        gateway=gateway,
        expected_head=moved,
        key="branch-update-at-new-head",
    )

    assert gateway.branch_updates == [(REPOSITORY, PR, moved)]


def _landed_tonight():
    """A landing already recorded for this repository inside the open window, which is what makes
    the pace term refuse every sibling for the rest of the night."""
    from orchestrator.persistence.models import EstatePrMerge

    return EstatePrMerge(
        repository=REPOSITORY,
        pr_number=51,
        head_sha="e" * 40,
        status="merged",
        reason_code=None,
        merge_commit_sha="f" * 40,
        github_status=200,
        change_record_id=52,
        policy_version=2,
        idempotency_key="a-landing-tonight",
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


# --------------------------------------------------------------------------------------------
# The REAL gateway. Everything above runs against a double, so nothing above can see what is
# actually sent or which status is actually believed -- and the status is the one fact about this
# call that is easy to get wrong by copying the landing call beside it.
# --------------------------------------------------------------------------------------------


class _Sent:
    """Stands in for the module-level `httpx.put`, recording what left the process."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append((url, json))
        return httpx.Response(self.status, json={"message": "Updating pull request branch"})


def _gateway(monkeypatch, sent: _Sent) -> GitHubEstatePullRequests:
    monkeypatch.setattr(estate_pr_merge.httpx, "put", sent)
    return GitHubEstatePullRequests(lambda: "a-token")


def test_the_platform_answers_202_AND_THAT_IS_SUCCESS(monkeypatch) -> None:
    """**202, NOT 200**, and it is the whole reason this call could not be copied from the landing
    beside it. The platform accepts the request and performs the work afterwards, so a `!= 200`
    check reads every success as a refusal -- silently, and in the direction where the lane simply
    stops working while reporting that the remote declined.
    """
    sent = _Sent(202)

    _gateway(monkeypatch, sent).update_branch(
        repository=REPOSITORY, number=PR, expected_head_sha=HEAD
    )

    assert len(sent.calls) == 1


def test_a_200_is_NOT_believed(monkeypatch) -> None:
    """The pair to the case above. Pinned in both directions, so a check widened to accept any 2xx
    -- which would look more permissive and more robust -- is caught as the loss of information it
    is."""
    with pytest.raises(EstateGatewayError):
        _gateway(monkeypatch, _Sent(200)).update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )


def test_the_request_NAMES_the_head_and_addresses_the_right_pull_request(monkeypatch) -> None:
    """`expected_head_sha` is the whole of the concurrency control: omit it and the platform
    substitutes whatever the head is now, which is precisely what it is here to prevent."""
    sent = _Sent(202)

    _gateway(monkeypatch, sent).update_branch(
        repository=REPOSITORY, number=PR, expected_head_sha=HEAD
    )

    url, body = sent.calls[0]
    assert url.endswith(f"/repos/{REPOSITORY}/pulls/{PR}/update-branch")
    assert body == {"expected_head_sha": HEAD}


def test_a_refusal_from_the_platform_carries_its_status_and_never_the_token(monkeypatch) -> None:
    with pytest.raises(EstateGatewayError) as raised:
        _gateway(monkeypatch, _Sent(422)).update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )

    assert raised.value.status_code == 422
    assert "a-token" not in str(raised.value)


def test_an_unreachable_platform_is_a_gateway_error_and_never_an_escape(monkeypatch) -> None:
    """A bare exception out of here reaches an unhandled HTTP 500: only `DomainError` and the
    authentication error have registered handlers."""

    def explode(url, *, headers, json, timeout):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(estate_pr_merge.httpx, "put", explode)

    with pytest.raises(EstateGatewayError) as raised:
        GitHubEstatePullRequests(lambda: "a-token").update_branch(
            repository=REPOSITORY, number=PR, expected_head_sha=HEAD
        )

    assert raised.value.code.startswith("request_error:")


# --------------------------------------------------------------------------------------------
# The two LIST reads the sibling rule needs. Both are paginated, and the pagination is the part
# that fails silently: a reader that stops after page one, or treats a failed later page as the
# end of the list, answers with a shorter list that looks complete -- and a sibling missing from
# it is a sibling the rule never considered.
# --------------------------------------------------------------------------------------------


class _Pages:
    """Stands in for the module-level `httpx.get`, answering by the `page=` query parameter."""

    def __init__(self, pages: dict[int, httpx.Response]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url, *, headers, timeout):
        self.urls.append(url)
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        return self.pages.get(page, httpx.Response(200, json=[]))


def _reader(monkeypatch, pages: _Pages) -> GitHubEstatePullRequests:
    monkeypatch.setattr(estate_pr_merge.httpx, "get", pages)
    return GitHubEstatePullRequests(lambda: "a-token")


def _open_row(number: int, *, login: str = "dependabot[bot]", kind: str = "Bot") -> dict:
    return {
        "number": number,
        "head": {"sha": f"head-{number}"},
        "user": {"login": login, "type": kind},
    }


def _commit_row(
    sha: str,
    *,
    author: str | None = "dependabot[bot]",
    committer: str | None = "web-flow",
    verified: object = True,
) -> dict:
    return {
        "sha": sha,
        "author": None if author is None else {"login": author},
        "committer": None if committer is None else {"login": committer},
        "commit": {"verification": {"verified": verified}},
    }


def test_the_open_list_follows_every_page(monkeypatch) -> None:
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_open_row(n) for n in range(1, 101)]),
            2: httpx.Response(200, json=[_open_row(n) for n in range(101, 104)]),
        }
    )

    pulls = _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert len(pulls) == 103
    assert [p.number for p in pulls] == list(range(1, 104))
    first, second = (parse_qs(urlsplit(u).query) for u in pages.urls)
    assert first["state"] == ["open"] and second["state"] == ["open"]
    assert first["per_page"] == ["100"] and second["per_page"] == ["100"]
    assert first["page"] == ["1"] and second["page"] == ["2"]
    assert all(f"/repos/{REPOSITORY}/pulls?" in u for u in pages.urls)


def test_a_failing_LATER_page_of_the_open_list_raises(monkeypatch) -> None:
    """The failure this whole section exists for: page one read, page two refused, and a reader
    that treats the refusal as the end of the list returns 100 rows that look complete."""
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_open_row(n) for n in range(1, 101)]),
            2: httpx.Response(500, json={"message": "boom"}),
        }
    )

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert raised.value.status_code == 500


def test_a_404_on_the_open_list_raises_rather_than_reading_empty(monkeypatch) -> None:
    """The single-object reader turns a 404 into `None`. A LIST that answers 404 is not an empty
    list -- read as one, it would say this repository has no siblings at all."""
    pages = _Pages({1: httpx.Response(404, json={"message": "Not Found"})})

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert raised.value.status_code == 404


def test_the_open_list_reads_author_type_and_head(monkeypatch) -> None:
    pages = _Pages(
        {
            1: httpx.Response(
                200,
                json=[
                    _open_row(7),
                    _open_row(8, login="AlobarQuest", kind="User"),
                ],
            )
        }
    )

    bot, person = _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert (bot.number, bot.head_sha, bot.author_login, bot.author_is_bot) == (
        7,
        "head-7",
        "dependabot[bot]",
        True,
    )
    assert (person.author_login, person.author_is_bot) == ("AlobarQuest", False)


@pytest.mark.parametrize(
    "row",
    [
        {"head": {"sha": "x"}, "user": {"login": "dependabot[bot]", "type": "Bot"}},
        {"number": True, "head": {"sha": "x"}, "user": {"login": "dependabot[bot]", "type": "Bot"}},
        {"number": 5, "head": {}, "user": {"login": "dependabot[bot]", "type": "Bot"}},
        {"number": 5, "user": {"login": "dependabot[bot]", "type": "Bot"}},
        "not a row",
    ],
)
def test_an_open_list_row_missing_its_number_or_head_raises(monkeypatch, row) -> None:
    """A row with no number cannot be matched to anything, and a row with no head cannot be
    compared with its commits -- neither may be dropped silently, which would shorten the list."""
    pages = _Pages({1: httpx.Response(200, json=[_open_row(1), row])})

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert raised.value.code == "open_pulls_response_invalid"


def test_commits_follow_every_page_and_keep_order(monkeypatch) -> None:
    """ORDER IS LOAD-BEARING: the rule compares the LAST commit's sha with the head the list call
    named, so a reader that reordered would compare the wrong commit."""
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_commit_row(f"c{n}") for n in range(100)]),
            2: httpx.Response(200, json=[_commit_row("c100"), _commit_row("newest")]),
        }
    )

    commits = _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert len(commits) == 102
    assert commits[0].sha == "c0"
    assert commits[-1].sha == "newest"
    assert all(f"/repos/{REPOSITORY}/pulls/{PR}/commits?" in u for u in pages.urls)


def test_a_failing_later_page_of_the_commits_raises(monkeypatch) -> None:
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_commit_row(f"c{n}") for n in range(100)]),
            2: httpx.Response(502, text="bad gateway"),
        }
    )

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert raised.value.status_code == 502


def test_a_commit_list_that_reaches_the_platform_cap_is_not_believed_complete(monkeypatch) -> None:
    """The commits listing returns at most 250 entries however it is paged. A list that reaches
    the cap may have been cut short, so it is an unread list rather than a complete one."""
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_commit_row(f"a{n}") for n in range(100)]),
            2: httpx.Response(200, json=[_commit_row(f"b{n}") for n in range(100)]),
            3: httpx.Response(200, json=[_commit_row(f"c{n}") for n in range(50)]),
        }
    )

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert raised.value.code == "commits_list_truncated"


def test_a_commit_list_one_short_of_the_cap_is_believed(monkeypatch) -> None:
    """The pair to the case above, so the cap is pinned at its value rather than anywhere below."""
    pages = _Pages(
        {
            1: httpx.Response(200, json=[_commit_row(f"a{n}") for n in range(100)]),
            2: httpx.Response(200, json=[_commit_row(f"b{n}") for n in range(100)]),
            3: httpx.Response(200, json=[_commit_row(f"c{n}") for n in range(49)]),
        }
    )

    commits = _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert len(commits) == 249


def test_an_unlinked_author_or_committer_reads_as_None(monkeypatch) -> None:
    """A commit whose email links to no GitHub account carries `author: null` at the top level.
    That is not the update bot and not somebody else either -- it is unknown, and the classifier
    downstream is what decides what unknown means."""
    pages = _Pages(
        {
            1: httpx.Response(
                200,
                json=[
                    _commit_row("no-author", author=None),
                    _commit_row("no-committer", committer=None),
                ],
            )
        }
    )

    unlinked_author, unlinked_committer = _reader(monkeypatch, pages).pull_request_commits(
        repository=REPOSITORY, number=PR
    )

    assert unlinked_author.author_login is None
    assert unlinked_author.committer_login == "web-flow"
    assert unlinked_committer.author_login == "dependabot[bot]"
    assert unlinked_committer.committer_login is None


def test_verification_is_true_only_when_the_platform_says_true(monkeypatch) -> None:
    pages = _Pages(
        {
            1: httpx.Response(
                200,
                json=[
                    _commit_row("yes", verified=True),
                    _commit_row("string", verified="true"),
                    _commit_row("absent", verified=None),
                    {"sha": "no-commit-object", "author": None, "committer": None},
                ],
            )
        }
    )

    commits = _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert [c.verified for c in commits] == [True, False, False, False]


def test_a_commit_row_with_no_sha_raises(monkeypatch) -> None:
    pages = _Pages({1: httpx.Response(200, json=[_commit_row("ok"), {"author": None}])})

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).pull_request_commits(repository=REPOSITORY, number=PR)

    assert raised.value.code == "commits_response_invalid"


def test_a_token_never_appears_in_a_list_read_error(monkeypatch) -> None:
    pages = _Pages({1: httpx.Response(403, json={"message": "Resource not accessible"})})

    with pytest.raises(EstateGatewayError) as raised:
        _reader(monkeypatch, pages).open_pull_requests(repository=REPOSITORY)

    assert raised.value.status_code == 403
    assert "a-token" not in str(raised.value)
    assert "a-token" not in repr(raised.value)


def test_an_unreachable_platform_on_a_list_read_is_a_gateway_error(monkeypatch) -> None:
    def explode(url, *, headers, timeout):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(estate_pr_merge.httpx, "get", explode)

    with pytest.raises(EstateGatewayError) as raised:
        GitHubEstatePullRequests(lambda: "a-token").pull_request_commits(
            repository=REPOSITORY, number=PR
        )

    assert raised.value.code.startswith("request_error:")


# --------------------------------------------------------------------------------------------
# `events.idempotency_key` is unique across the WHOLE table rather than per act, so "spent on a
# different subject" has three shapes and every one of them reaches this replay path.
# --------------------------------------------------------------------------------------------


def test_a_key_spent_by_a_DIFFERENT_KIND_OF_ACT_is_refused(migrated_session: Session) -> None:
    """Answering from it would report a branch as brought up to date on the strength of an event
    about something else entirely."""
    migrated_session.add(
        Event(
            actor_id="orchestrator-system",
            action="something.else",
            subject_type="work_unit",
            subject_id=uuid.uuid4(),
            payload={"repository": REPOSITORY, "pr_number": PR, "head_sha": HEAD},
            correlation_id=uuid.uuid4(),
            idempotency_key="shared-across-acts",
        )
    )
    migrated_session.flush()
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, key="shared-across-acts")

    assert raised.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []


def test_a_key_spent_on_a_DIFFERENT_REPOSITORY_is_refused(migrated_session: Session) -> None:
    """Same pull request number, different repository -- the shape an operator produces by copying
    a request and editing one field."""
    migrated_session.add(
        Event(
            actor_id="orchestrator-system",
            action=BRANCH_UPDATE_ACTION,
            subject_type=BRANCH_UPDATE_SUBJECT,
            subject_id=uuid.uuid4(),
            payload={"repository": "alobarquest/brain", "pr_number": PR, "head_sha": HEAD},
            correlation_id=uuid.uuid4(),
            idempotency_key="shared-across-repositories",
        )
    )
    migrated_session.flush()
    gateway = _behind()

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, key="shared-across-repositories")

    assert raised.value.code == "idempotency_conflict"
    assert gateway.branch_updates == []


def test_the_repository_lock_is_actually_TAKEN_and_actually_WAITS(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """THE KEY IS THE RACE, NOT THE BRANCH -- and this pins that the lock is held rather than that
    the statement was typed.

    The platform refuses a head that moved, so the branch guards itself. Nothing guards the
    idempotency key: two concurrent requests carrying one key would both read no spent event, both
    call the remote, and the loser's commit would violate the unique index as an unhandled 500 over
    an act that happened twice.

    Driven with a real second connection holding the same advisory lock, and a `lock_timeout` on
    this one, so a passing run means this transaction genuinely WAITED on the other. A test that
    only asserted the statement was issued would pass against a lock taken on the wrong key.

    Note this is the first test in this repository to assert any of its four advisory locks is
    taken -- the others are documented in the idempotency matrix and pinned by nothing.
    """
    with Session(migrated_engine) as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"estate_pr_branch_update:{REPOSITORY}"},
        )
        migrated_session.execute(text("SET LOCAL lock_timeout = '250ms'"))
        gateway = _behind()

        with pytest.raises(OperationalError):
            _update(migrated_session, gateway=gateway)

        assert gateway.branch_updates == [], "it must not act while another holder has the lock"
        holder.rollback()


def test_the_self_clearing_codes_are_exactly_the_ones_this_service_raises() -> None:
    """THE SECOND cross-boundary vocabulary this increment created, pinned like the first.

    The lander decides whether a POST-time refusal is a finding by matching the code against its
    own copy of these two. Rename one here and the classification silently stops matching, so a
    self-clearing refusal starts reporting as a finding every night -- the drift fails toward
    noise rather than toward a hole, which is the safe direction and still not one anybody would
    notice from either side alone.

    `estate_branch_update_refused_by_remote` is deliberately NOT a member: the platform declining
    can mean a real merge conflict, which no later pass clears on its own.
    """
    from estate_lander.cli import _UPDATE_SELF_CLEARING

    assert _UPDATE_SELF_CLEARING == {
        BRANCH_UPDATE_HEAD_MOVED,
        BRANCH_UPDATE_NOT_QUALIFIED,
        BRANCH_UPDATE_SIBLING_HOLDING,
    }
    assert BRANCH_UPDATE_REFUSED_BY_REMOTE not in _UPDATE_SELF_CLEARING


def test_an_UNREADABLE_scan_is_NOT_self_clearing() -> None:
    """ADR-0045. The two sibling refusals look alike and mean opposite things to the lander.

    A holding sibling is a deliberate withhold that clears when the branch ahead lands. A scan that
    could not read is the orchestrator not knowing, and nothing about not knowing clears on its
    own -- so an act refused for it must stay a finding every pass until the reads succeed.
    """
    from estate_lander.cli import _UPDATE_SELF_CLEARING

    assert BRANCH_UPDATE_SIBLINGS_UNREADABLE not in _UPDATE_SELF_CLEARING


def test_neither_new_code_contains_the_other() -> None:
    """A code that is a substring of another satisfies every substring reader of the other -- the
    report greps, and the discriminating tests that assert a code is ABSENT on one pass and PRESENT
    on the next. Checked over all four sibling codes, across both lanes."""
    from orchestrator.services.inert_pr_branch_update import (
        INERT_BRANCH_UPDATE_SIBLING_HOLDING,
        INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    )

    codes = [
        BRANCH_UPDATE_SIBLING_HOLDING,
        BRANCH_UPDATE_SIBLINGS_UNREADABLE,
        INERT_BRANCH_UPDATE_SIBLING_HOLDING,
        INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE,
    ]
    assert len(set(codes)) == 4
    for one in codes:
        for other in codes:
            if one != other:
                assert one not in other, (one, other)


# --------------------------------------------------------------------------------------------
# ADR-0045: the lane edits one Dependabot branch per repository at a time.
#
# Every case here composes a REAL sibling admission through the act, over a gateway that answers
# each pull request for itself -- so what is tested is the act computing the rule, not a value
# somebody handed it. The single-target fake would hand every sibling the target's answer.
# --------------------------------------------------------------------------------------------

SIBLING = 50
SIBLING_HEAD = "b" * 40


def _beside_sibling(*, sibling_commits=None, target_commits=None, **kwargs) -> SiblingGateway:
    """The target behind its base, beside one Dependabot sibling also behind its base.

    Both are otherwise landable, so the sibling's own composed answer names only freshness -- a
    holding answer. Whether it HOLDS therefore turns on its ownership alone, which is the variable
    each pair below moves.
    """
    commits = {}
    if sibling_commits is not None:
        commits[SIBLING] = sibling_commits
    if target_commits is not None:
        commits[PR] = target_commits
    return SiblingGateway(
        target=PR,
        pulls={
            PR: pull_request(number=PR),
            SIBLING: pull_request(number=SIBLING, head_sha=SIBLING_HEAD),
        },
        behind={PR: 3, SIBLING: 3},
        commits=commits,
        **kwargs,
    )


def _both_records() -> FakeChangeRecordSource:
    return FakeChangeRecordSource({(REPOSITORY, PR): approved(), (REPOSITORY, SIBLING): approved()})


def _no_update_event_readable(engine: Engine) -> bool:
    with Session(engine) as reader:
        return reader.scalar(select(Event).where(Event.action == BRANCH_UPDATE_ACTION)) is None


def test_an_owned_branch_beside_an_edited_sibling_that_holds_is_never_touched(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """THE DISCRIMINATING CONTROL, at the surface that writes to a repository. The sibling carries
    this estate's commit, so Dependabot will no longer rebase it; making the target edited too is
    the deadlock's whole precondition. The assertion is on the untouched branch and the absent
    record, not only on the code."""
    gateway = _beside_sibling(sibling_commits=(foreign_commit(SIBLING_HEAD),))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, record_source=_both_records())

    assert raised.value.code == BRANCH_UPDATE_SIBLING_HOLDING
    assert gateway.branch_updates == []
    assert _no_update_event_readable(migrated_engine)


def test_the_same_branch_beside_an_OWNED_sibling_is_brought_up_to_date(
    migrated_session: Session,
) -> None:
    """The pair to the case above, identical but for the sibling's ownership. Only the rule makes
    the two answers differ, and the act must compute it itself: nothing on the admission answer
    it composes says so."""
    gateway = _beside_sibling()

    _update(migrated_session, gateway=gateway, record_source=_both_records())

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


def test_an_already_edited_branch_is_freshened_again_beside_a_holding_sibling(
    migrated_session: Session,
) -> None:
    """It has already lost Dependabot's ownership, so updating it again costs nothing -- and this
    is what keeps a repository that already has several edited branches workable."""
    gateway = _beside_sibling(
        sibling_commits=(foreign_commit(SIBLING_HEAD),),
        target_commits=(dependabot_commit("a" * 40), foreign_commit(HEAD)),
    )

    _update(migrated_session, gateway=gateway, record_source=_both_records())

    assert gateway.branch_updates == [(REPOSITORY, PR, HEAD)]


def test_an_unreadable_scan_refuses_with_its_own_code_and_touches_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Not the holding code: the orchestrator could not establish that no other edited branch is
    queued, and the lander must be able to tell that apart from a repository waiting its turn."""
    gateway = _beside_sibling(open_error=EstateGatewayError("open_pull_requests_status", 502))

    with pytest.raises(DomainError) as raised:
        _update(migrated_session, gateway=gateway, record_source=_both_records())

    assert raised.value.code == BRANCH_UPDATE_SIBLINGS_UNREADABLE
    assert gateway.branch_updates == []
    assert _no_update_event_readable(migrated_engine)
