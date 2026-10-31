"""The inert-population caller: what it asks, what it relays, and what it never decides.

ADR-0038 part 2a. The assertions that matter are about restraint -- that a pull request nobody
declared an author for is never asked about, that a pass without `--submit` asks for nothing, and
that the head a landing names is the head the answer was about.

**THE ABSENCE OF A DELIBERATE REFUSAL IS TESTED, not merely omitted.** Its sibling classifies a
spent pace and an hour outside the change window as refusals nobody can act on. This lane raises
neither, because it has no clock -- so the same codes arriving here must stay findings, and a
future increment that gives this lane a clock has to red a test and decide.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from bump_proposer.landing_policy import LandingPolicy
from deploy_watcher.github import ReadError
from inert_lander.cli import (
    _DEFERRAL_AUTHOR,
    LANE,
    _branch_updates,
    _pass,
    _subjects,
    report,
    run,
)
from inert_lander.orchestrator_client import (
    LandingRefused,
    OrchestratorClient,
    OrchestratorError,
)
from lander.core import (
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_TOOL_FAILURE,
    EXIT_UNUSABLE,
    NOT_A_FINDING,
    REPORTED,
    SETTLED,
    Outcome,
    held_status,
    landing_key,
    update_key,
)

REPOSITORY = "alobarquest/intent-packages"
OTHER = "alobarquest/factory-runner"
BOT = "dependabot[bot]"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"


def _rule(*repositories: str) -> LandingPolicy:
    """The declaration, as `bump_proposer.landing_policy` parses it from the live document.

    The real dataclass rather than a stub, because `declares` and `covers_author` are the two
    questions this program asks of it and a stub would let either drift.
    """
    return LandingPolicy(
        version=6,
        inert=frozenset(name.lower() for name in (repositories or (REPOSITORY,))),
        permitted_authors=frozenset({BOT}),
        inert_excluded_ecosystems=frozenset({"docker"}),
        deploying=frozenset({"alobarquest/brain"}),
        deploying_excluded_ecosystems=frozenset({"github_actions"}),
    )


class FakeGitHub:
    """Records which repositories were enumerated, so a test can assert on the absence of one."""

    def __init__(
        self,
        pulls: dict[str, list[Any]] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._pulls = pulls or {}
        self._errors = errors or {}
        self.enumerated: list[str] = []

    def __enter__(self) -> FakeGitHub:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]:
        self.enumerated.append(repository)
        error = self._errors.get(repository)
        if error is not None:
            raise error
        return list(self._pulls.get(repository, []))


class FakeOrchestrator:
    """Records every question and every request, so a test can assert on the absence of one."""

    def __init__(
        self,
        answers: dict[tuple[str, int], dict[str, Any]] | None = None,
        *,
        land_error: Exception | None = None,
        admission_error: Exception | None = None,
        update_error: Exception | None = None,
        replayed: bool = False,
        watch: list[dict[str, Any]] | None = None,
        watch_error: Exception | None = None,
        revert_answers: dict[tuple[str, int], dict[str, Any]] | None = None,
    ) -> None:
        self.unknown: list[dict[str, Any]] = []
        self.unknown_error: Exception | None = None
        self._answers = answers or {}
        self._land_error = land_error
        self._admission_error = admission_error
        self._update_error = update_error
        self._replayed = replayed
        self.asked: list[tuple[str, int]] = []
        self.landed: list[tuple[str, int, str, str]] = []
        # The list a refusal test asserts is EMPTY: an implementation that asked and then reported
        # the refusal would satisfy a status assertion and fail this.
        self.updated: list[tuple[str, int, str, str]] = []
        self.closed: list[tuple[str, int, str]] = []
        # Landing Part 3a. The watch this program reads, and every act it asked for on one.
        self._watch = watch or []
        self._watch_error = watch_error
        self._revert_answers = revert_answers or {}
        self.reverts: list[tuple[str, int, str]] = []
        self.revert_reads: list[str | None] = []

    def __enter__(self) -> FakeOrchestrator:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
        self.asked.append((repository, pr_number))
        if self._admission_error is not None:
            raise self._admission_error
        return self._answers.get(
            (repository, pr_number),
            {"satisfied": False, "refusals": ["landing_head_not_current_with_base"]},
        )

    def land(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.landed.append((repository, pr_number, head_sha, idempotency_key))
        if self._land_error is not None:
            raise self._land_error
        merged: dict[str, Any] = {"status": "merged"}
        return merged

    def update_branch(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.updated.append((repository, pr_number, head_sha, idempotency_key))
        if self._update_error is not None:
            raise self._update_error
        answered: dict[str, Any] = {
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "replayed": self._replayed,
        }
        return answered

    def close_held(
        self, repository: str, pr_number: int, *, head_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.closed.append((repository, pr_number, idempotency_key))
        closed: dict[str, Any] = {"commented": True, "replayed": False, "ceilings": []}
        return closed

    def unknown_outcomes(self) -> list[dict[str, Any]]:
        if self.unknown_error is not None:
            raise self.unknown_error
        return self.unknown

    def landing_watch(self) -> list[dict[str, Any]]:
        if self._watch_error is not None:
            raise self._watch_error
        return self._watch

    def revert_landing(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_status: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.reverts.append((repository, pr_number, idempotency_key))
        self.revert_reads.append(expected_status)
        return self._revert_answers.get(
            (repository, pr_number),
            {"acted": True, "status": "rerun_requested", "detail": "failed once: test"},
        )


def _pull(number: int, *, author: str = BOT) -> dict[str, Any]:
    """An open pull request, projected exactly as the reader projects one."""
    return {
        "number": number,
        "title": f"Bump a from 1 to 2 ({number})",
        "author": author,
        "is_bot": author.endswith("[bot]"),
        "draft": False,
        "base_ref": "main",
    }


def _admissible() -> dict[str, Any]:
    return {"satisfied": True, "refusals": [], "head_sha": HEAD}


def _held(*refusals: str) -> dict[str, Any]:
    return {"satisfied": False, "refusals": list(refusals), "head_sha": HEAD}


# --------------------------------------------------------------------------------------------
# Enumeration: which pull requests this lane is for at all.
# --------------------------------------------------------------------------------------------


def test_only_a_pull_request_by_a_DECLARED_AUTHOR_becomes_a_subject() -> None:
    """The one thing bounding which pull requests this lane sees.

    The deploying lane gets this for free from a producer that refuses a non-bot pull request
    upstream; there is no record here and so no upstream filter. Four of the declared repositories
    carry a factory caller workflow, so a machine-authored pull request with green checks is a
    real subject rather than a hypothetical one -- and this lane asks none of the questions a
    factory landing rests on.
    """
    github = FakeGitHub({REPOSITORY: [_pull(1), _pull(2, author="AlobarQuest")]})
    assert _subjects(github, _rule()).subjects == [(REPOSITORY, 1)]


def test_a_pull_request_a_person_opened_is_DEFERRED_and_is_not_a_finding() -> None:
    """Deferring is this program working: somebody's own pull request is not this lane's business.

    Reported, because a subject that vanishes without a line is the silent failure this program is
    written against -- but not a finding, because nothing about it is unmet.
    """
    github = FakeGitHub({REPOSITORY: [_pull(2, author="AlobarQuest")]})
    selection = _subjects(github, _rule())
    assert selection.subjects == []
    assert selection.deferred == {_DEFERRAL_AUTHOR: 1}
    assert report([], selection.deferred, 6) == EXIT_OK


def test_a_pull_request_naming_NO_AUTHOR_is_deferred_rather_than_asked_about() -> None:
    """A projection missing the field is not a permission to guess at who opened it."""
    github = FakeGitHub({REPOSITORY: [{"number": 3, "author": None}]})
    selection = _subjects(github, _rule())
    assert selection.subjects == []
    assert selection.deferred == {_DEFERRAL_AUTHOR: 1}


def test_only_the_DECLARED_repositories_are_enumerated_at_all() -> None:
    """Scope is the declaration's, and this program adds nothing to it.

    A repository GitHub would happily answer for is never asked about, so an undeclared one costs
    no request and produces no line.
    """
    github = FakeGitHub({REPOSITORY: [_pull(1)], OTHER: [_pull(9)]})
    selection = _subjects(github, _rule(REPOSITORY))
    assert github.enumerated == [REPOSITORY]
    assert selection.subjects == [(REPOSITORY, 1)]


def test_a_DEPLOYING_repository_is_never_enumerated() -> None:
    """The landing policy the bump proposer reads now declares both populations; this lane is the
    inert one alone. `_rule` declares `alobarquest/brain` deploying."""
    github = FakeGitHub({REPOSITORY: [_pull(1)], "alobarquest/brain": [_pull(9)]})
    _subjects(github, _rule(REPOSITORY))
    assert github.enumerated == [REPOSITORY]


def test_a_repository_GITHUB_CANNOT_ANSWER_FOR_is_a_finding_and_the_others_still_run() -> None:
    """One repository's outage must not discard the queue of the other five.

    It IS a finding: a repository nobody could enumerate is one whose queue is unmeasured, which
    is exactly the silence this lane exists to end.
    """
    github = FakeGitHub(
        {OTHER: [_pull(9)]}, errors={REPOSITORY: ReadError("github is unreachable")}
    )
    selection = _subjects(github, _rule(REPOSITORY, OTHER))
    assert selection.subjects == [(OTHER, 9)]
    assert [(o.repository, o.status) for o in selection.unreadable] == [(REPOSITORY, "unreadable")]
    assert report(selection.unreadable, selection.deferred, 6) == EXIT_FINDINGS


def test_the_pass_is_ordered_so_WHICH_ONE_LANDS_is_reproducible() -> None:
    """Freshness lets at most one pull request per repository land per pass, so the order decides
    which one does. Whatever order GitHub answered in must not."""
    github = FakeGitHub({REPOSITORY: [_pull(7), _pull(2)], OTHER: [_pull(4)]})
    assert _subjects(github, _rule(REPOSITORY, OTHER)).subjects == [
        (OTHER, 4),
        (REPOSITORY, 2),
        (REPOSITORY, 7),
    ]


@pytest.mark.parametrize("number", [True, False, 0, -1, "3", None])
def test_a_pull_request_number_that_is_not_a_POSITIVE_INT_is_never_asked_about(
    number: Any,
) -> None:
    """`bool` is an `int` and `True == 1`, so a boolean would be asked about as pull request one.
    Three other readers in this repository exclude it for this exact field."""
    github = FakeGitHub({REPOSITORY: [{"number": number, "author": BOT}]})
    assert _subjects(github, _rule()).subjects == []


def test_a_pull_request_whose_NUMBER_IS_NOT_A_NUMBER_beside_one_that_is_never_crashes() -> None:
    """TWO pull requests, because one cannot discriminate. Sorting a list whose numbers have not
    been checked compares whatever the platform answered, and one string beside one integer raises
    a `TypeError` no caller here catches -- a scheduled pass ending in a traceback instead of a
    report. With a single pull request the comparison never happens and the test passes either
    way, which is the shape of a control that proves nothing."""
    github = FakeGitHub({REPOSITORY: [{"number": "3", "author": BOT}, _pull(4)]})
    assert _subjects(github, _rule()).subjects == [(REPOSITORY, 4)]


def test_something_that_is_not_a_pull_request_at_all_is_skipped() -> None:
    pulls: dict[str, list[Any]] = {REPOSITORY: ["not a pull request", _pull(4)]}
    github = FakeGitHub(pulls)
    assert _subjects(github, _rule()).subjects == [(REPOSITORY, 4)]


# --------------------------------------------------------------------------------------------
# Classification: what is a finding, and what is not.
# --------------------------------------------------------------------------------------------


def test_a_dry_run_asks_the_question_and_requests_nothing() -> None:
    """The default. An operator reaching for "just show me what would happen" must not land."""
    client = FakeOrchestrator({(REPOSITORY, 1): _admissible()})
    outcomes = _pass([(REPOSITORY, 1)], client, False)
    assert [o.status for o in outcomes] == ["would-land"]
    assert client.asked == [(REPOSITORY, 1)]
    assert client.landed == []


def test_an_admissible_pull_request_is_landed_on_the_head_THE_ANSWER_WAS_ABOUT() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _admissible()})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["landed"]
    assert client.landed == [(REPOSITORY, 1, HEAD, landing_key(LANE, REPOSITORY, 1, HEAD))]


def test_a_held_pull_request_names_the_condition_it_misses_and_is_a_FINDING() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_checks_not_clean")})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert "landing_checks_not_clean" in outcomes[0].detail
    assert client.landed == []
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


@pytest.mark.parametrize("refusal", sorted(SETTLED))
def test_a_SETTLED_subject_is_not_a_finding(refusal: str) -> None:
    """One landing, or one pull request a person merged themselves, must not become a nightly
    page forever. The row this lane writes has no delete path."""
    client = FakeOrchestrator({(REPOSITORY, 1): _held(refusal)})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["settled"]
    assert report(outcomes, {}, 6) == EXIT_OK


def test_SETTLED_is_read_with_INTERSECTION_and_ahead_of_every_other_classification() -> None:
    """A settled subject's other refusals are meaningless: the pull request is gone, or this lane
    has already acted on it. This is the ONE place that polarity is right, and the test says so
    because the subset rule its sibling uses for a deliberate refusal is the opposite."""
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _held("landing_pull_request_not_open", "landing_checks_not_clean")}
    )
    assert [o.status for o in _pass([(REPOSITORY, 1)], client, True)] == ["settled"]


@pytest.mark.parametrize(
    "refusal",
    [
        # Its sibling calls these two DELIBERATE and not findings, because there a pace resets and
        # a window reopens. This lane has no clock and raises neither, so if one ever arrives it
        # is a condition nobody has explained and it must report.
        "landing_pace_exhausted",
        "landing_outside_change_window",
        # And this one its sibling calls an EXCEPTION -- current policy can never clear it. This
        # lane never asks about an update type at all, so it is unexplained here. Note this lane
        # DOES now have an exception of its own (`landing_ecosystem_excluded`); the point of this
        # case is that the sibling's member is not silently inherited with it.
        "landing_update_type_unparseable",
        # A code nobody has classified, present or future.
        "landing_something_nobody_has_thought_of",
    ],
)
def test_a_refusal_THE_SIBLING_LANE_WOULD_EXCUSE_is_still_a_finding_here(refusal: str) -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _held(refusal)})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_being_behind_its_base_is_a_FINDING_when_no_exception_sits_beside_it() -> None:
    """Transient, and the branch-update pass clears it on a later run -- but a condition all the
    same.

    RENAMED, from `..._under_every_circumstance`. That was true when this lane had no exception
    and is false now: beside one, being behind is the lane's own permanent declining and carries
    nothing a reader could act on. The pair that pins the difference is below."""
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _held("landing_head_not_current_with_base", "landing_rollout_moved")}
    )
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_a_refusal_CURRENT_POLICY_CAN_NEVER_CLEAR_is_an_exception_and_not_a_finding() -> None:
    """Devon's 2026-08-13 ruling, applied to this lane's own member.

    The deploy policy names the ecosystems whose changes a pull request's required checks do not
    exercise. Such a pull request waits on a person forever and no pass of this program will ever
    change that, so reporting it hourly makes the exit code stop discriminating -- which is the
    whole cost the ruling exists to avoid.
    """
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_ecosystem_excluded")})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["exception"]
    assert "landing_ecosystem_excluded" in outcomes[0].detail
    assert client.landed == []
    assert report(outcomes, {}, 6) == EXIT_OK


def test_an_ungranted_repository_is_an_exception_not_held() -> None:
    """Landing Part 2. The member record not granting the repository clears only when a person
    grants it on `/review`, so no pass of this program changes it: an exception, not a finding."""
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_repository_not_granted")})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["exception"]
    assert client.landed == []
    assert report(outcomes, {}, 6) == EXIT_OK


def test_freshness_is_suppressed_WHEN_AND_ONLY_WHEN_an_exception_is_present() -> None:
    """THREE ROWS, and the third was added because a mutation proved the first two insufficient.

    The rule has two failure directions and needs a row for each, plus a row for it holding:

    - **suppress nothing** -- row one reports `held`, so row one catches it.
    - **suppress unconditionally** -- rows one and two BOTH give the same answer either way, a
      branch beside an exception being quiet regardless and a failing check unexplained
      regardless. **Only row three, freshness ALONE, changes**: unconditionally it reads
      `exception`, which is a merely-stale branch gone quiet. Measured -- the first version of
      this test carried rows one and two, asserted in its own docstring that they caught the
      over-general fix, and the mutant SURVIVED. The existing behind-its-base test does not cover
      it either: it pairs freshness with a second refusal, which stays unexplained under the
      mutant.

    Row two is `orchestrator#3` as it stands today: the exclusion AND a red build, and the red
    build is a person's to act on.
    """
    beside_exception = FakeOrchestrator(
        {(REPOSITORY, 1): _held("landing_ecosystem_excluded", "landing_head_not_current_with_base")}
    )
    beside_a_real_condition = FakeOrchestrator(
        {
            (REPOSITORY, 1): _held(
                "landing_ecosystem_excluded",
                "landing_head_not_current_with_base",
                "landing_checks_not_clean",
            )
        }
    )
    alone = FakeOrchestrator({(REPOSITORY, 1): _held("landing_head_not_current_with_base")})

    assert [o.status for o in _pass([(REPOSITORY, 1)], beside_exception, True)] == ["exception"]
    assert [o.status for o in _pass([(REPOSITORY, 1)], beside_a_real_condition, True)] == ["held"]
    assert [o.status for o in _pass([(REPOSITORY, 1)], alone, True)] == ["held"]


def test_a_SETTLED_subject_outranks_an_exception() -> None:
    """Ordering, asserted rather than left to the reading order. There is nothing left to land on
    a settled subject, so its other refusals -- an exception included -- say nothing."""
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _held("landing_already_recorded", "landing_ecosystem_excluded")}
    )
    assert [o.status for o in _pass([(REPOSITORY, 1)], client, True)] == ["settled"]


def test_an_unsatisfied_answer_that_names_NO_refusal_is_a_finding() -> None:
    """The orchestrator failing to say why is exactly the thing worth reporting."""
    client = FakeOrchestrator({(REPOSITORY, 1): {"satisfied": False, "refusals": []}})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_an_unreadable_admission_answer_lands_nothing_and_is_a_finding() -> None:
    """The state EVERY subject is in until production serves this lane's routes: a 404 is a
    question that was not answered, which is a different thing from one answered no."""
    client = FakeOrchestrator(admission_error=OrchestratorError("the orchestrator answered 404"))
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["unreadable"]
    assert "404" in outcomes[0].detail
    assert client.landed == []
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_an_admissible_answer_with_NO_HEAD_lands_nothing() -> None:
    """Acting without a head would be asking for whatever has been pushed since."""
    client = FakeOrchestrator({(REPOSITORY, 1): {"satisfied": True, "refusals": []}})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.landed == []


def test_a_refused_landing_is_reported_rather_than_retried() -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _admissible()},
        land_error=LandingRefused("the remote refused", "inert_merge_refused_by_remote"),
    )
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_an_error_at_the_landing_is_reported_as_an_ERROR_and_is_a_finding() -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _admissible()}, land_error=OrchestratorError("unreachable")
    )
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["error"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_the_idempotency_key_is_content_addressed_so_a_replay_is_a_replay() -> None:
    """A random key would make every pass a new request for the same act, which the orchestrator
    would refuse as a spent key belonging to a different subject."""
    assert landing_key(LANE, REPOSITORY, 1, HEAD) == landing_key(LANE, REPOSITORY, 1, HEAD)
    assert landing_key(LANE, REPOSITORY, 1, HEAD) != landing_key(LANE, REPOSITORY, 2, HEAD)
    assert landing_key(LANE, REPOSITORY, 1, HEAD) != landing_key(LANE, REPOSITORY, 1, "0" * 40)
    assert landing_key(LANE, REPOSITORY, 1, HEAD) != update_key(LANE, REPOSITORY, 1, HEAD)


def test_this_lanes_keys_can_never_collide_with_the_SIBLING_lanes() -> None:
    """The two lanes cannot have the same subject -- each requires the opposite answer from the
    estate about a repository -- but a shared prefix would make that a fact a reader has to know
    rather than one the key states."""
    from estate_lander.cli import LANE as ESTATE_LANE

    assert landing_key(LANE, REPOSITORY, 1, HEAD) != landing_key(ESTATE_LANE, REPOSITORY, 1, HEAD)
    assert update_key(LANE, REPOSITORY, 1, HEAD) != update_key(ESTATE_LANE, REPOSITORY, 1, HEAD)


# --------------------------------------------------------------------------------------------
# The branch update: the act that keeps a required freshness satisfiable.
# --------------------------------------------------------------------------------------------


def _qualifies(**extra: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "satisfied": False,
        "refusals": ["landing_head_not_current_with_base"],
        "head_sha": HEAD,
        "branch_update_qualifies": True,
    }
    answer.update(extra)
    return answer


def test_a_dry_run_reports_what_it_WOULD_update_and_asks_for_nothing() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies()})
    outcomes = _branch_updates([(REPOSITORY, 1)], client, False)
    assert [o.status for o in outcomes] == ["would-update"]
    assert client.updated == []


def test_a_branch_the_orchestrator_says_qualifies_is_brought_up_to_date() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies()})
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["updated"]
    assert client.updated == [(REPOSITORY, 1, HEAD, update_key(LANE, REPOSITORY, 1, HEAD))]
    assert report(outcomes, {}, 6) == EXIT_OK


def test_a_branch_that_does_NOT_qualify_is_never_asked_about_and_gets_no_line() -> None:
    """The landing pass has already printed one naming every condition it misses."""
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies(branch_update_qualifies=False)})
    assert _branch_updates([(REPOSITORY, 1)], client, True) == []
    assert client.updated == []


def test_an_answer_CARRYING_NO_SUCH_KEY_withholds_the_act() -> None:
    """The direction to fail in, and not hypothetical: a deployed image that predates the field
    serves an answer without it, and its sibling read a key that was not there for two days --
    freshening nothing while its report said zero."""
    answer = _qualifies()
    del answer["branch_update_qualifies"]
    client = FakeOrchestrator({(REPOSITORY, 1): answer})
    assert _branch_updates([(REPOSITORY, 1)], client, True) == []
    assert client.updated == []


def test_a_qualifying_answer_with_NO_HEAD_updates_nothing() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies(head_sha=None)})
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.updated == []


def test_an_unreadable_answer_updates_nothing() -> None:
    client = FakeOrchestrator(admission_error=OrchestratorError("the orchestrator answered 404"))
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.updated == []


@pytest.mark.parametrize("code", sorted(LANE.update_self_clearing))
def test_a_refusal_that_only_says_THE_ANSWER_MOVED_is_not_a_finding(code: str) -> None:
    """The answer and the act are separate transactions by design, so a head the update bot
    rebased in that window is ordinary rather than exotic."""
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _qualifies()}, update_error=LandingRefused("moved", code)
    )
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["deliberate"]
    assert report(outcomes, {}, 6) == EXIT_OK


@pytest.mark.parametrize(
    "code",
    [
        # The SIBLING lane's spellings. They are a different lane's codes and this lane must not
        # excuse them -- which is also the measured reason the two classifiers could not be shared.
        "estate_branch_update_head_moved",
        "estate_branch_update_not_qualified",
        "inert_merge_refused_by_remote",
        "",
    ],
)
def test_EVERY_OTHER_update_refusal_is_still_a_finding(code: str) -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _qualifies()}, update_error=LandingRefused("refused", code)
    )
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_a_REPLAY_means_the_branch_never_moved_and_is_a_finding() -> None:
    """The key is content-addressed over the head and a success moves the head, so a replay is the
    platform having accepted the work and not delivered it."""
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies()}, replayed=True)
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


def test_an_error_at_the_update_is_reported_as_an_ERROR() -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _qualifies()}, update_error=OrchestratorError("unreachable")
    )
    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["error"]


def test_the_update_key_is_content_addressed_over_the_head() -> None:
    """A successful update CHANGES the head, so the next legitimate update after the base moves
    again necessarily carries a different key and can never be barred by this one."""
    assert update_key(LANE, REPOSITORY, 1, HEAD) == update_key(LANE, REPOSITORY, 1, HEAD)
    assert update_key(LANE, REPOSITORY, 1, HEAD) != update_key(LANE, REPOSITORY, 1, "0" * 40)


# --------------------------------------------------------------------------------------------
# The report.
# --------------------------------------------------------------------------------------------


def test_the_summary_counts_every_status_so_its_parts_sum_to_what_was_considered(capsys) -> None:
    outcomes = [Outcome(REPOSITORY, index, status, "") for index, status in enumerate(REPORTED)]
    report(outcomes, {}, 6)
    printed = capsys.readouterr().out
    assert f"{len(outcomes)} considered" in printed
    for status in REPORTED:
        assert f"1 {status}" in printed


def test_the_policy_version_the_pass_acted_under_is_printed(capsys) -> None:
    """One version covers both of the document's populations, so a reader comparing two nights
    needs to know whether the declaration moved under them."""
    report([], {}, 6)
    assert "landing policy version 6" in capsys.readouterr().out


def test_a_status_nobody_classified_is_a_finding() -> None:
    assert report([Outcome(REPOSITORY, 1, "invented", "")], {}, 6) == EXIT_FINDINGS


def test_every_NOT_A_FINDING_status_is_one_the_summary_counts() -> None:
    """A status excluded from findings but absent from the report order would vanish from the
    summary while still being printed as a line."""
    assert NOT_A_FINDING <= set(REPORTED)


def test_the_status_column_is_wide_enough_for_the_widest_status(capsys) -> None:
    """The column is a literal width, so a status longer than it runs into the detail with no
    separating space and the report stops lining up. Read from `REPORTED` rather than restated,
    so a longer status added later reddens this rather than being noticed by eye."""
    widest = max(REPORTED, key=len)
    report([Outcome(REPOSITORY, 1, widest, "detail")], {}, 6)
    assert f"{widest} detail" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# `run`: the wiring, and the order of the two passes.
# --------------------------------------------------------------------------------------------


def _wire(monkeypatch, github: FakeGitHub, client: FakeOrchestrator, rule: LandingPolicy) -> None:
    monkeypatch.setenv("INERT_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("INERT_LANDING_ORCHESTRATOR_TOKEN", "orch")
    monkeypatch.setenv("INERT_LANDING_GITHUB_TOKEN", "gh")
    monkeypatch.setattr("inert_lander.cli.GitHubReader", lambda *a, **k: github)
    monkeypatch.setattr("inert_lander.cli.OrchestratorClient", lambda *a, **k: client)
    monkeypatch.setattr("inert_lander.cli.read_landing_policy", lambda *a, **k: rule)


@pytest.mark.parametrize(
    "missing",
    [
        "INERT_LANDING_CHANGE_MANAGER_TOKEN",
        "INERT_LANDING_ORCHESTRATOR_TOKEN",
        "INERT_LANDING_GITHUB_TOKEN",
    ],
)
def test_each_missing_credential_is_NAMED_and_stops_the_pass(monkeypatch, missing, capsys) -> None:
    """Three different people fix these three. A launcher that fetched two of the three would
    otherwise report the same line whichever it dropped."""
    _wire(monkeypatch, FakeGitHub(), FakeOrchestrator(), _rule())
    monkeypatch.delenv(missing)
    assert run([]) == EXIT_UNUSABLE
    assert missing in capsys.readouterr().err


def test_a_declaration_this_program_cannot_read_stops_the_WHOLE_pass(monkeypatch) -> None:
    """One declaration covers every repository at once, so reporting it per repository would be N
    copies of one fact -- and a rule this program cannot read is a rule it will not guess at."""
    from bump_proposer.landing_policy import LandingPolicyError

    github = FakeGitHub({REPOSITORY: [_pull(1)]})
    _wire(monkeypatch, github, FakeOrchestrator(), _rule())

    def refuse(*_: Any, **__: Any) -> LandingPolicy:
        raise LandingPolicyError("declares no inert population")

    monkeypatch.setattr("inert_lander.cli.read_landing_policy", refuse)
    assert run([]) == EXIT_UNUSABLE
    assert github.enumerated == []


@pytest.mark.parametrize("drop", [("repositories",), ("landing",), ("repositories", "landing")])
def test_a_document_with_no_deploying_half_runs_exactly_as_the_full_one(
    monkeypatch, capsys, drop
) -> None:
    """The inert lane never asks about the deploying population, so a policy without it (or
    without its `landing` block) must not stop or change this pass. Compared against the same
    pass over the full live document, through the real parser."""
    import json

    from bump_proposer.landing_policy import parse
    from tests.bump_proposer.test_landing_policy import LIVE

    def one_pass(document: dict[str, Any]) -> tuple[int, list[str], str]:
        github = FakeGitHub({REPOSITORY: [_pull(1)]})
        _wire(monkeypatch, github, FakeOrchestrator({(REPOSITORY, 1): _admissible()}), _rule())
        monkeypatch.setattr("inert_lander.cli.read_landing_policy", lambda *a, **k: parse(document))
        code = run([])
        return code, github.enumerated, capsys.readouterr().out

    reduced = json.loads(json.dumps(LIVE))
    for key in drop:
        del reduced[key]

    full = one_pass(LIVE)
    assert full[0] == EXIT_OK and REPOSITORY in full[1]
    assert one_pass(reduced) == full


def test_a_github_failure_MID_PASS_is_a_tool_failure(monkeypatch) -> None:
    """`_subjects` catches a per-repository read, so reaching this means the reader itself broke."""

    class Exploding(FakeGitHub):
        def open_pull_requests(self, repository: str) -> list[dict[str, Any]]:
            raise ReadError("github is unreachable")

    github = Exploding({REPOSITORY: []})
    monkeypatch.setattr(
        "inert_lander.cli._subjects", lambda *a, **k: (_ for _ in ()).throw(ReadError("boom"))
    )
    _wire(monkeypatch, github, FakeOrchestrator(), _rule())
    assert run([]) == EXIT_TOOL_FAILURE


def test_the_landing_pass_runs_BEFORE_the_branch_update_pass(monkeypatch) -> None:
    """A landing moves the base, so it is the act that puts every sibling behind. Going first
    would bring a branch up to date and then immediately stale it by landing something else."""
    order: list[str] = []
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), FakeOrchestrator(), _rule())
    monkeypatch.setattr("inert_lander.cli._pass", lambda *a, **k: (order.append("land"), [])[1])
    monkeypatch.setattr(
        "inert_lander.cli._branch_updates", lambda *a, **k: (order.append("update"), [])[1]
    )
    assert run([]) == EXIT_OK
    assert order == ["land", "update"]


def test_the_branch_update_pass_READS_THE_ANSWER_AGAIN(monkeypatch) -> None:
    """Because the landing pass may have changed it -- which is the whole reason this runs
    second. Carrying the first answer over would freshen against a base that has moved."""
    client = FakeOrchestrator({(REPOSITORY, 1): _admissible()})
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())
    run([])
    assert client.asked == [(REPOSITORY, 1), (REPOSITORY, 1)]


def test_run_reports_the_REAL_deferral_tally(monkeypatch, capsys) -> None:
    """A default that quietly meant "none" would be a wiring bug nothing could see."""
    _wire(
        monkeypatch,
        FakeGitHub({REPOSITORY: [_pull(2, author="AlobarQuest")]}),
        FakeOrchestrator(),
        _rule(),
    )
    assert run([]) == EXIT_OK
    assert _DEFERRAL_AUTHOR in capsys.readouterr().out


def test_run_reports_the_version_of_the_declaration_it_actually_read(monkeypatch, capsys) -> None:
    rule = LandingPolicy(
        version=99,
        inert=frozenset({REPOSITORY}),
        permitted_authors=frozenset({BOT}),
        inert_excluded_ecosystems=frozenset(),
        deploying=frozenset({"alobarquest/brain"}),
        deploying_excluded_ecosystems=frozenset(),
    )
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), FakeOrchestrator(), rule)
    run([])
    assert "landing policy version 99" in capsys.readouterr().out


def test_a_bare_run_asks_for_nothing_while_submit_is_what_asks(monkeypatch) -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _admissible()})
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())
    run([])
    assert client.landed == []
    run(["--submit"])
    assert client.landed == [(REPOSITORY, 1, HEAD, landing_key(LANE, REPOSITORY, 1, HEAD))]


# --------------------------------------------------------------------------------------------
# The client's own reading of a refusal.
# --------------------------------------------------------------------------------------------


def test_the_refusal_code_is_read_from_where_a_domain_error_actually_puts_it() -> None:
    """NESTED under `error`. A check written from the handler's shape matches neither that nor the
    framework's own `detail`."""
    client = OrchestratorClient(
        "t",
        "k",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                409, json={"error": {"code": "inert_branch_update_head_moved", "message": "moved"}}
            )
        ),
    )
    with pytest.raises(LandingRefused) as raised:
        client.update_branch("o/r", 1, head_sha=HEAD, idempotency_key="k")
    assert raised.value.code == "inert_branch_update_head_moved"
    assert str(raised.value) == "moved"


@pytest.mark.parametrize("body", [{"detail": "Not Found"}, {"error": "flat"}, {}])
def test_a_body_this_program_cannot_read_yields_NO_CODE_rather_than_a_guess(body) -> None:
    client = OrchestratorClient(
        "t", "k", transport=httpx.MockTransport(lambda request: httpx.Response(409, json=body))
    )
    with pytest.raises(LandingRefused) as raised:
        client.land("o/r", 1, head_sha=HEAD, idempotency_key="k")
    assert raised.value.code == ""


def test_a_route_the_deployed_image_does_not_serve_is_an_ERROR_naming_the_status() -> None:
    """The state this lane is in until its release: a 404 answering the framework's own shape,
    which must read as a question that was not answered."""
    client = OrchestratorClient(
        "t",
        "k",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"detail": "Not Found"})
        ),
    )
    with pytest.raises(OrchestratorError) as raised:
        client.admission("o/r", 1)
    assert "404" in str(raised.value)
    assert not isinstance(raised.value, LandingRefused)


# --------------------------------------------------------------------------------------------
# ADR-0045: a sibling withheld because an edited branch ahead of it is queued to land.
#
# Keyed on a sibling the orchestrator OBSERVED, never on a clock -- so this lane still has no
# deliberate refusal. A key alone never quiets a line: `waiting` needs the key TRUE and the
# freshness refusal actually present to subtract.
# --------------------------------------------------------------------------------------------

_BEHIND = "landing_head_not_current_with_base"
_WITHHELD = "branch_update_withheld_for_sibling"


def _line(answer: dict[str, Any]) -> str:
    (outcome,) = _pass([(REPOSITORY, 1)], FakeOrchestrator({(REPOSITORY, 1): answer}), True)
    return outcome.status


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        pytest.param(_held(_BEHIND) | {_WITHHELD: True}, "waiting", id="behind-key"),
        pytest.param(_held(_BEHIND), "held", id="behind-no-key"),
        pytest.param(_held(_BEHIND) | {_WITHHELD: False}, "held", id="behind-key-false"),
        # Guards this program on its own: a failing check beside the key is red CI, never quiet.
        pytest.param(
            _held(_BEHIND, "landing_checks_not_clean") | {_WITHHELD: True},
            "held",
            id="behind-checks-key",
        ),
        # An exception outranks the withhold, because it is the durable fact.
        pytest.param(
            _held(_BEHIND, "landing_ecosystem_excluded") | {_WITHHELD: True},
            "exception",
            id="exception-beside-key",
        ),
        # Nothing to subtract: the key alone changes nothing.
        pytest.param(
            _held("landing_checks_in_flight") | {_WITHHELD: True}, "held", id="no-freshness-key"
        ),
    ],
)
def test_a_sibling_WITHHELD_for_a_holding_branch_reads_waiting_only_on_the_key(
    answer: dict[str, Any], verdict: str
) -> None:
    assert _line(answer) == verdict


def test_waiting_is_not_a_finding() -> None:
    assert report([Outcome(REPOSITORY, 1, "waiting", "")], {}, 6) == EXIT_OK
    assert "waiting" in REPORTED


def test_the_update_pass_SKIPS_a_withheld_sibling_with_no_line() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _qualifies(**{_WITHHELD: True})})

    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)

    assert outcomes == []
    assert client.updated == []


@pytest.mark.parametrize("key", [False, None], ids=["false", "absent"])
def test_the_update_pass_acts_when_the_key_is_false_or_absent(key: bool | None) -> None:
    answer = _qualifies() if key is None else _qualifies(**{_WITHHELD: key})
    client = FakeOrchestrator({(REPOSITORY, 1): answer})

    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)

    assert [o.status for o in outcomes] == ["updated"]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("inert_branch_update_sibling_holding", "deliberate"),
        ("inert_branch_update_siblings_unreadable", "held"),
    ],
)
def test_an_act_refused_for_a_sibling_reads_by_WHICH_sibling_code(code: str, status: str) -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _qualifies()}, update_error=LandingRefused("sibling", code)
    )

    outcomes = _branch_updates([(REPOSITORY, 1)], client, True)

    assert [o.status for o in outcomes] == [status]


def test_no_reported_status_is_a_substring_of_another() -> None:
    """A status that contains another breaks every substring reader of the report.

    An operator greps the pass's output and the tests assert on its text, so `waiting` beside
    `held` meant `grep held` matched both and an "absent" assertion on `held` failed for a
    status that was not `held` at all. Holds for every pair, including statuses added later.
    """
    collisions = [(a, b) for a in REPORTED for b in REPORTED if a != b and a in b]
    assert collisions == []


# Bridge layer Part 4 (Rulings 6 and 7). A held bump clears only when Devon lifts the hold, so it is
# an exception, and an acting pass closes it with a comment; a dependency list the orchestrator
# could not read is a finding, never a quiet refusal, and is never closed.
_CEILING = "landing_version_ceiling_exceeded"
_LIST_UNREADABLE = "landing_dependency_list_unreadable"


@pytest.mark.parametrize(
    ("refusals", "status", "exit_code"),
    [
        ((_CEILING,), "closed", EXIT_OK),
        ((_CEILING, "landing_head_not_current_with_base"), "closed", EXIT_OK),
        ((_LIST_UNREADABLE,), "held", EXIT_FINDINGS),
    ],
)
def test_a_hold_is_closed_and_an_unreadable_list_is_a_finding(
    refusals: tuple[str, ...], status: str, exit_code: int
) -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _held(*refusals)})

    outcomes = _pass([(REPOSITORY, 1)], client, True)

    assert [o.status for o in outcomes] == [status]
    assert client.landed == []
    assert len(client.closed) == (1 if status == "closed" else 0)
    assert report(outcomes, {}, 6) == exit_code


def test_a_hold_is_still_an_exception_to_the_classifier() -> None:
    """The close replaces the line, not the classification: were it ever skipped, the line would be
    the exception it was before (`held_status` unchanged)."""
    assert held_status(LANE, [_CEILING], rollout_base_matches_pin=False) == "exception"


# --------------------------------------------------------------------------------------------
# `run`: the held-pull-request alert (Landing Part 1, task 1).
# --------------------------------------------------------------------------------------------


class AlertCalls:
    """Stands in for `sync_held_alerts`, so no test here reaches alert-hub or the real state
    directory. It records each call and answers `reason`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Outcome]]] = []
        self.reason: str | None = None

    def __call__(self, lane: str, outcomes: Any, **_: Any) -> str | None:
        self.calls.append((lane, list(outcomes)))
        return self.reason


class RevertAlertCalls:
    """Stands in for `sync_revert_alerts`: records the watch each call was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]] | None]] = []
        self.stranded: list[set[tuple[str, int]]] = []
        self.unknown: list[set[tuple[str, int]]] = []
        self.outside: list[set[tuple[str, int]]] = []

    def __call__(
        self,
        lane: str,
        watch: Any,
        *,
        stranded: Any = frozenset(),
        unknown: Any = frozenset(),
        outside: Any = frozenset(),
        **_: Any,
    ) -> str | None:
        self.outside.append(set(outside))
        self.calls.append((lane, None if watch is None else list(watch)))
        self.stranded.append(set(stranded))
        self.unknown.append(set(unknown))
        return None


@pytest.fixture(autouse=True)
def alerts(monkeypatch) -> AlertCalls:
    calls = AlertCalls()
    monkeypatch.setattr("inert_lander.cli.sync_held_alerts", calls)
    return calls


@pytest.fixture(autouse=True)
def revert_alerts(monkeypatch) -> RevertAlertCalls:
    calls = RevertAlertCalls()
    monkeypatch.setattr("inert_lander.cli.sync_revert_alerts", calls)
    return calls


def test_a_submit_pass_syncs_held_alerts_with_its_outcomes(monkeypatch, alerts) -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_checks_not_clean")})
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())

    assert run(["--submit"]) == EXIT_FINDINGS

    assert len(alerts.calls) == 1
    lane, outcomes = alerts.calls[0]
    assert lane == "inert-landing"
    assert Outcome(REPOSITORY, 1, "held", "landing_checks_not_clean") in outcomes


def test_a_report_run_sends_no_alerts_and_writes_no_state(monkeypatch, alerts, tmp_path) -> None:
    from lander import held_alerts

    monkeypatch.setattr("inert_lander.cli.sync_held_alerts", held_alerts.sync_held_alerts)
    monkeypatch.setenv(held_alerts.TOKEN_ENV, "sender-token")
    monkeypatch.setenv(held_alerts.STATE_DIR_ENV, str(tmp_path / "state"))
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_checks_not_clean")})
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())

    assert run([]) == EXIT_FINDINGS

    assert not (tmp_path / "state").exists()


def test_a_pass_that_produced_no_outcomes_does_not_sync(monkeypatch, alerts) -> None:
    """The early exits: a missing credential and an unreadable declaration. Neither measured
    anything, so neither may resolve an alert a measured pass raised."""
    from bump_proposer.landing_policy import LandingPolicyError

    _wire(monkeypatch, FakeGitHub(), FakeOrchestrator(), _rule())
    monkeypatch.delenv("INERT_LANDING_GITHUB_TOKEN")
    assert run(["--submit"]) == EXIT_UNUSABLE

    _wire(monkeypatch, FakeGitHub(), FakeOrchestrator(), _rule())

    def refuse(*_: Any, **__: Any) -> LandingPolicy:
        raise LandingPolicyError("declares no inert population")

    monkeypatch.setattr("inert_lander.cli.read_landing_policy", refuse)
    assert run(["--submit"]) == EXIT_UNUSABLE

    assert alerts.calls == []


def test_an_empty_pass_still_syncs_so_remembered_alerts_resolve(monkeypatch, alerts) -> None:
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), FakeOrchestrator(), _rule())

    assert run(["--submit"]) == EXIT_OK

    assert alerts.calls == [("inert-landing", [])]


def test_an_alert_failure_exits_one_after_printing_the_report(monkeypatch, alerts, capsys) -> None:
    alerts.reason = "SDS_BRIDGE_ALERT_TOKEN is unset"
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_checks_not_clean")})
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())

    assert run(["--submit"]) == EXIT_TOOL_FAILURE

    captured = capsys.readouterr()
    assert "landing_checks_not_clean" in captured.out
    assert "alert-hub: SDS_BRIDGE_ALERT_TOKEN is unset" in captured.err


# --------------------------------------------------------------------------------------------
# The watch (Landing Part 3a): landings that went bad, and the reverts the orchestrator opened.
# --------------------------------------------------------------------------------------------

LANDED = 7
REVERT_PR = 9


def _watched(verdict: str | None, status: str | None = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "repository": REPOSITORY,
        "pr_number": LANDED,
        "verdict": verdict,
        "detail": f"{verdict or status}: test",
        "failed_job_names": ["test"],
        "failed_runs": [[18_000_000_001, 1]],
        "status": status,
        "revert_pr_number": None,
        "hold_outcome": None,
    }
    row.update(extra)
    return row


def test_an_acting_pass_asks_for_each_bad_landing(monkeypatch, capsys) -> None:
    watch = [
        _watched("flaky_retry"),
        _watched("bad", "rerun_requested", pr_number=8),
        _watched("clean", pr_number=10),
        _watched("pending", pr_number=11),
    ]
    client = FakeOrchestrator(
        watch=watch,
        revert_answers={(REPOSITORY, 8): {"acted": True, "status": "revert_opened", "detail": ""}},
    )
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run(["--submit"]) == EXIT_OK

    assert client.reverts == [
        (
            REPOSITORY,
            LANDED,
            f"inert-landing-revert:{REPOSITORY}:{LANDED}:flaky_retry:18000000001.1",
        ),
        (REPOSITORY, 8, f"inert-landing-revert:{REPOSITORY}:8:bad:18000000001.1"),
    ]
    assert client.revert_reads == [None, "rerun_requested"]
    out = capsys.readouterr().out
    assert f"{REPOSITORY}#{LANDED}  reran" in out
    assert f"{REPOSITORY}#8  revert-opened" in out


def test_a_report_run_asks_for_nothing(monkeypatch, alerts, revert_alerts, capsys) -> None:
    client = FakeOrchestrator(watch=[_watched("flaky_retry"), _watched("bad", pr_number=8)])
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run([]) == EXIT_OK

    assert client.reverts == []
    assert alerts.calls == [] and revert_alerts.calls == []
    out = capsys.readouterr().out
    assert "would-rerun" in out and "would-revert" in out


def test_an_opened_revert_is_a_subject_of_the_landing_and_branch_update_passes(
    monkeypatch,
) -> None:
    """The App opened it, which no declared author is: it is asked about by the record."""
    client = FakeOrchestrator(
        {(REPOSITORY, REVERT_PR): _admissible()},
        watch=[_watched(None, "revert_opened", revert_pr_number=REVERT_PR)],
    )
    github = FakeGitHub({REPOSITORY: [_pull(REVERT_PR, author="alobar-sds-dispatch[bot]")]})
    _wire(monkeypatch, github, client, _rule())

    run(["--submit"])

    assert client.asked == [(REPOSITORY, REVERT_PR), (REPOSITORY, REVERT_PR)]
    assert [(r, n) for r, n, _, _ in client.landed] == [(REPOSITORY, REVERT_PR)]
    assert client.reverts == []


def test_a_closed_revert_is_never_asked_about(monkeypatch) -> None:
    client = FakeOrchestrator(watch=[_watched(None, "revert_opened", revert_pr_number=REVERT_PR)])
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    run(["--submit"])

    assert client.asked == []


def test_a_refused_revert_is_a_finding_while_it_is_watched(monkeypatch, capsys) -> None:
    client = FakeOrchestrator(watch=[_watched(None, "refused_overlap")])
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run(["--submit"]) == EXIT_FINDINGS
    assert "revert-refused" in capsys.readouterr().out


def test_a_revert_raises_its_alert_from_the_watch_it_was_handed(monkeypatch, revert_alerts) -> None:
    watch = [_watched(None, "revert_landed", hold_outcome="held uv / typer at 0.20.0")]
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), FakeOrchestrator(watch=watch), _rule())

    assert run(["--submit"]) == EXIT_OK

    assert revert_alerts.calls == [("inert-landing", watch)]


def test_an_unreadable_watch_still_lands_and_holds_the_revert_alerts(
    monkeypatch, alerts, revert_alerts, capsys
) -> None:
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _admissible()}, watch_error=OrchestratorError("answered 404")
    )
    _wire(monkeypatch, FakeGitHub({REPOSITORY: [_pull(1)]}), client, _rule())

    assert run(["--submit"]) == EXIT_FINDINGS

    assert [(r, n) for r, n, _, _ in client.landed] == [(REPOSITORY, 1)]
    assert revert_alerts.calls == [("inert-landing", None)]
    # The watch's row is not a pull request held: it never reaches the held alerts.
    assert all(o.repository != "landing-watch" for o in alerts.calls[0][1])
    assert "landing-watch#0  unreadable" in capsys.readouterr().out


def test_a_refused_act_is_reported_as_an_error(monkeypatch, capsys) -> None:
    class Refusing(FakeOrchestrator):
        def revert_landing(self, repository: str, pr_number: int, **_: Any):
            raise LandingRefused("no failing run could be rerun", "landing_revert_rerun_refused")

    _wire(
        monkeypatch,
        FakeGitHub({REPOSITORY: []}),
        Refusing(watch=[_watched("flaky_retry")]),
        _rule(),
    )

    assert run(["--submit"]) == EXIT_FINDINGS
    assert "error" in capsys.readouterr().out


def test_a_revert_closed_without_landing_is_a_finding(monkeypatch, revert_alerts, capsys) -> None:
    """Its record stays `revert_opened` and GitHub no longer lists it: nothing else says so."""
    watch = [_watched(None, "revert_opened", revert_pr_number=REVERT_PR)]
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), FakeOrchestrator(watch=watch), _rule())

    assert run(["--submit"]) == EXIT_FINDINGS

    assert f"{REPOSITORY}#{LANDED}  revert-stranded" in capsys.readouterr().out
    assert revert_alerts.stranded == [{(REPOSITORY, LANDED)}]
    assert revert_alerts.unknown == [set()]


def test_an_open_revert_is_not_stranded_and_an_unlisted_one_is_unknown(
    monkeypatch, revert_alerts
) -> None:
    watch = [
        _watched(None, "revert_opened", revert_pr_number=REVERT_PR),
        _watched(None, "revert_opened", repository=OTHER, revert_pr_number=REVERT_PR),
    ]
    github = FakeGitHub(
        {REPOSITORY: [_pull(REVERT_PR, author="alobar-sds-dispatch[bot]")]},
        errors={OTHER: ReadError("listing failed")},
    )
    _wire(monkeypatch, github, FakeOrchestrator(watch=watch), _rule(REPOSITORY, OTHER))

    run(["--submit"])

    assert revert_alerts.stranded == [set()]
    assert revert_alerts.unknown == [{(OTHER, LANDED)}]


def test_a_revert_outside_the_population_is_stranded_not_unknown(
    monkeypatch, revert_alerts, capsys
) -> None:
    """Nothing lists that repository, so nothing lands the revert: no later pass will know more."""
    watch = [_watched(None, "revert_opened", repository=OTHER, revert_pr_number=REVERT_PR)]
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), FakeOrchestrator(watch=watch), _rule())

    assert run(["--submit"]) == EXIT_FINDINGS

    assert "outside this lane's population" in capsys.readouterr().out
    assert revert_alerts.stranded == [{(OTHER, LANDED)}]
    assert revert_alerts.outside == [{(OTHER, LANDED)}]
    assert revert_alerts.unknown == [set()]


# --------------------------------------------------------------------------------------------
# Final review: the watch acts before the landing pass, and a race reads as waiting.
# --------------------------------------------------------------------------------------------


def test_an_acting_pass_acts_on_the_watch_BEFORE_it_lands_anything(monkeypatch) -> None:
    """A sibling landed on top of a bad bump would make its revert an overlap, so the bad landing
    is acted on first; the revert the watch already names is still a subject of this pass."""
    calls: list[str] = []

    class Recording(FakeOrchestrator):
        def landing_watch(self) -> list[dict[str, Any]]:
            calls.append("watch")
            return super().landing_watch()

        def revert_landing(self, repository: str, pr_number: int, **kwargs: Any):
            calls.append(f"act {pr_number}")
            return super().revert_landing(repository, pr_number, **kwargs)

        def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
            calls.append(f"ask {pr_number}")
            return super().admission(repository, pr_number)

        def land(self, repository: str, pr_number: int, **kwargs: Any) -> dict[str, Any]:
            calls.append(f"land {pr_number}")
            return super().land(repository, pr_number, **kwargs)

    client = Recording(
        {(REPOSITORY, 1): _admissible(), (REPOSITORY, REVERT_PR): _admissible()},
        watch=[
            _watched("bad", "rerun_requested"),
            _watched(None, "revert_opened", pr_number=8, revert_pr_number=REVERT_PR),
        ],
    )
    github = FakeGitHub(
        {REPOSITORY: [_pull(1), _pull(REVERT_PR, author="alobar-sds-dispatch[bot]")]}
    )
    _wire(monkeypatch, github, client, _rule())

    run(["--submit"])

    assert calls[:2] == ["watch", f"act {LANDED}"]
    assert f"land {REVERT_PR}" in calls
    assert calls.index(f"act {LANDED}") < min(
        i for i, call in enumerate(calls) if call.startswith(("ask", "land"))
    )


def test_an_act_the_orchestrator_declined_is_waiting_not_unreadable(monkeypatch, capsys) -> None:
    """`acted: false` means the act judged again and found nothing to do (the watch and the act
    disagreed, a harmless race): the next pass asks again. Not a finding."""
    client = FakeOrchestrator(
        watch=[_watched("flaky_retry")],
        revert_answers={
            (REPOSITORY, LANDED): {
                "acted": False,
                "status": None,
                "verdict": "pending",
                "detail": "the rerun has not reported a failure of its own yet",
            }
        },
    )
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run(["--submit"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"{REPOSITORY}#{LANDED}  waiting" in out
    assert "the rerun has not reported" in out


@pytest.mark.parametrize(
    ("verdict", "expected", "code"),
    [
        # A read inside the act failed: nothing was reverted, and nothing else will say so.
        pytest.param("unreadable", "unreadable", EXIT_FINDINGS, id="a-failed-read-is-a-finding"),
        pytest.param("pending", "waiting", EXIT_OK, id="a-race-is-waiting"),
        pytest.param("clean", "waiting", EXIT_OK, id="a-clean-race-is-waiting"),
    ],
)
def test_an_act_that_did_nothing_is_a_finding_only_when_its_read_failed(
    monkeypatch, capsys, verdict: str, expected: str, code: int
) -> None:
    client = FakeOrchestrator(
        watch=[_watched("bad", "rerun_requested")],
        revert_answers={
            (REPOSITORY, LANDED): {
                "acted": False,
                "status": "rerun_requested",
                "verdict": verdict,
                "detail": f"{verdict}: the reason",
            }
        },
    )
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run(["--submit"]) == code
    assert f"{REPOSITORY}#{LANDED}  {expected:<12} {verdict}: the reason" in capsys.readouterr().out


def test_the_estate_lanes_landings_in_the_watch_are_never_this_lanes(
    monkeypatch, revert_alerts, capsys
) -> None:
    """Landing Part 3b: the watch serves both lanes' landings. A landing that redeploys is the
    estate lander's to act on and alert about; this one acts, reports and alerts on its own."""
    mine = _watched("flaky_retry", lane="inert")
    theirs = [
        _watched("bad", pr_number=79, lane="estate"),
        _watched(None, "refused_overlap", pr_number=80, lane="estate"),
        _watched(None, "revert_opened", pr_number=81, revert_pr_number=82, lane="estate"),
    ]
    client = FakeOrchestrator(watch=[mine, *theirs])
    _wire(monkeypatch, FakeGitHub({REPOSITORY: []}), client, _rule())

    assert run(["--submit"]) == EXIT_OK

    assert [number for _, number, _ in client.reverts] == [LANDED]
    assert revert_alerts.calls == [("inert-landing", [mine])]
    out = capsys.readouterr().out
    assert all(f"{REPOSITORY}#{number}" not in out for number in (79, 80, 81))


# ---- Unknown landing outcomes (Landing Part 5) -----------------------------------------------


def _an_acting_pass(monkeypatch, client: FakeOrchestrator, github: FakeGitHub) -> list[Any]:
    _wire(monkeypatch, github, client, _rule(REPOSITORY))
    calls: list[Any] = []
    monkeypatch.setattr("inert_lander.cli.sync_held_alerts", lambda *a, **k: None)

    def sync(*_: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("inert_lander.cli.sync_revert_alerts", sync)
    run(["--submit"])
    return calls


UNKNOWN_ANSWER = {
    "satisfied": False,
    "refusals": ["landing_outcome_unknown", "landing_pull_request_not_open"],
    "head_sha": "a" * 40,
}


def test_an_unknown_row_is_asked_about_though_no_listing_names_it(monkeypatch) -> None:
    client = FakeOrchestrator({(REPOSITORY, 9): UNKNOWN_ANSWER})
    client.unknown = [{"repository": REPOSITORY, "pr_number": 9}]

    _an_acting_pass(monkeypatch, client, FakeGitHub({REPOSITORY: []}))

    assert [(repository, number) for repository, number, _, _ in client.landed] == [(REPOSITORY, 9)]


def test_a_subject_that_is_also_unknown_is_asked_about_once(monkeypatch) -> None:
    client = FakeOrchestrator({(REPOSITORY, 9): UNKNOWN_ANSWER})
    client.unknown = [{"repository": REPOSITORY.upper(), "pr_number": 9}]
    client._answers[(REPOSITORY.upper(), 9)] = UNKNOWN_ANSWER
    pulls = [{"number": 9, "author": "dependabot[bot]"}]

    _an_acting_pass(monkeypatch, client, FakeGitHub({REPOSITORY: pulls}))

    assert len(client.landed) == 1


def test_an_unreadable_list_of_unknown_rows_is_a_finding_and_the_pass_runs(
    monkeypatch, capsys
) -> None:
    client = FakeOrchestrator({(REPOSITORY, 9): {"satisfied": True, "head_sha": "a" * 40}})
    client.unknown_error = OrchestratorError("no answer")
    pulls = [{"number": 9, "author": "dependabot[bot]"}]

    _an_acting_pass(monkeypatch, client, FakeGitHub({REPOSITORY: pulls}))

    assert "landing-unknown-outcomes#0  unreadable" in capsys.readouterr().out
    assert len(client.landed) == 1


def _opened_revert() -> dict[str, Any]:
    return {
        "repository": REPOSITORY,
        "pr_number": 5,
        "verdict": None,
        "status": "revert_opened",
        "revert_pr_number": 6,
        "lane": "inert",
    }


@pytest.mark.parametrize("unreadable", [False, True], ids=["unknown-row", "list-unread"])
def test_a_closed_revert_whose_row_is_unknown_is_not_called_stranded(
    monkeypatch, capsys, unreadable: bool
) -> None:
    client = FakeOrchestrator({(REPOSITORY, 6): UNKNOWN_ANSWER}, watch=[_opened_revert()])
    if unreadable:
        client.unknown_error = OrchestratorError("no answer")
    else:
        client.unknown = [{"repository": REPOSITORY, "pr_number": 6}]

    [alerted] = _an_acting_pass(monkeypatch, client, FakeGitHub({REPOSITORY: []}))

    assert "closed without landing" not in capsys.readouterr().out
    assert alerted["stranded"] == frozenset()
    assert alerted["unknown"] == {(REPOSITORY, 5)}


def test_a_closed_revert_with_no_unknown_row_is_still_stranded(monkeypatch, capsys) -> None:
    client = FakeOrchestrator(watch=[_opened_revert()])

    _an_acting_pass(monkeypatch, client, FakeGitHub({REPOSITORY: []}))

    assert "closed without landing" in capsys.readouterr().out
