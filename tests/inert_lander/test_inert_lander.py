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

from bump_proposer.landing_policy import InertLanding
from deploy_watcher.github import ReadError
from inert_lander.cli import (
    _DEFERRAL_AUTHOR,
    _NOT_A_FINDING,
    _REPORTED,
    _SETTLED,
    _UPDATE_SELF_CLEARING,
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_TOOL_FAILURE,
    EXIT_UNUSABLE,
    Outcome,
    _branch_updates,
    _key,
    _pass,
    _subjects,
    _update_key,
    report,
    run,
)
from inert_lander.orchestrator_client import (
    LandingRefused,
    OrchestratorClient,
    OrchestratorError,
)

REPOSITORY = "alobarquest/intent-packages"
OTHER = "alobarquest/factory-runner"
BOT = "dependabot[bot]"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"


def _rule(*repositories: str) -> InertLanding:
    """The declaration, as `bump_proposer.landing_policy` parses it from the live document.

    The real dataclass rather than a stub, because `declares` and `covers_author` are the two
    questions this program asks of it and a stub would let either drift.
    """
    return InertLanding(
        version=6,
        repositories=frozenset(name.lower() for name in (repositories or (REPOSITORY,))),
        permitted_authors=frozenset({BOT}),
        excluded_ecosystems=frozenset({"docker"}),
    )


class FakeGitHub:
    """Records which repositories were enumerated, so a test can assert on the absence of one."""

    def __init__(
        self,
        pulls: dict[str, list[dict[str, Any]]] | None = None,
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
    ) -> None:
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
    assert client.landed == [(REPOSITORY, 1, HEAD, _key(REPOSITORY, 1, HEAD))]


def test_a_held_pull_request_names_the_condition_it_misses_and_is_a_FINDING() -> None:
    client = FakeOrchestrator({(REPOSITORY, 1): _held("landing_checks_not_clean")})
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert "landing_checks_not_clean" in outcomes[0].detail
    assert client.landed == []
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


@pytest.mark.parametrize("refusal", sorted(_SETTLED))
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
        # lane never asks about an update type at all.
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


def test_being_behind_its_base_is_a_FINDING_here_under_every_circumstance() -> None:
    """Transient, and the branch-update pass clears it on a later run -- but a condition all the
    same, and this lane has no exception for it to sit beside."""
    client = FakeOrchestrator(
        {(REPOSITORY, 1): _held("landing_head_not_current_with_base", "landing_rollout_moved")}
    )
    outcomes = _pass([(REPOSITORY, 1)], client, True)
    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes, {}, 6) == EXIT_FINDINGS


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
    assert _key(REPOSITORY, 1, HEAD) == _key(REPOSITORY, 1, HEAD)
    assert _key(REPOSITORY, 1, HEAD) != _key(REPOSITORY, 2, HEAD)
    assert _key(REPOSITORY, 1, HEAD) != _key(REPOSITORY, 1, "0" * 40)
    assert _key(REPOSITORY, 1, HEAD) != _update_key(REPOSITORY, 1, HEAD)


def test_this_lanes_keys_can_never_collide_with_the_SIBLING_lanes() -> None:
    """The two lanes cannot have the same subject -- each requires the opposite answer from the
    estate about a repository -- but a shared prefix would make that a fact a reader has to know
    rather than one the key states."""
    from estate_lander.cli import _key as estate_key
    from estate_lander.cli import _update_key as estate_update_key

    assert _key(REPOSITORY, 1, HEAD) != estate_key(REPOSITORY, 1, HEAD)
    assert _update_key(REPOSITORY, 1, HEAD) != estate_update_key(REPOSITORY, 1, HEAD)


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
    assert client.updated == [(REPOSITORY, 1, HEAD, _update_key(REPOSITORY, 1, HEAD))]
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


@pytest.mark.parametrize("code", sorted(_UPDATE_SELF_CLEARING))
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
    assert _update_key(REPOSITORY, 1, HEAD) == _update_key(REPOSITORY, 1, HEAD)
    assert _update_key(REPOSITORY, 1, HEAD) != _update_key(REPOSITORY, 1, "0" * 40)


# --------------------------------------------------------------------------------------------
# The report.
# --------------------------------------------------------------------------------------------


def test_the_summary_counts_every_status_so_its_parts_sum_to_what_was_considered(capsys) -> None:
    outcomes = [Outcome(REPOSITORY, index, status, "") for index, status in enumerate(_REPORTED)]
    report(outcomes, {}, 6)
    printed = capsys.readouterr().out
    assert f"{len(outcomes)} considered" in printed
    for status in _REPORTED:
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
    assert _NOT_A_FINDING <= set(_REPORTED)


def test_the_status_column_is_wide_enough_for_the_widest_status(capsys) -> None:
    """The column is a literal width, so a status longer than it runs into the detail with no
    separating space and the report stops lining up. Read from `_REPORTED` rather than restated,
    so a longer status added later reddens this rather than being noticed by eye."""
    widest = max(_REPORTED, key=len)
    report([Outcome(REPOSITORY, 1, widest, "detail")], {}, 6)
    assert f"{widest} detail" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# `run`: the wiring, and the order of the two passes.
# --------------------------------------------------------------------------------------------


def _wire(monkeypatch, github: FakeGitHub, client: FakeOrchestrator, rule: InertLanding) -> None:
    monkeypatch.setenv("INERT_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("INERT_LANDING_ORCHESTRATOR_TOKEN", "orch")
    monkeypatch.setenv("INERT_LANDING_GITHUB_TOKEN", "gh")
    monkeypatch.setattr("inert_lander.cli.GitHubReader", lambda *a, **k: github)
    monkeypatch.setattr("inert_lander.cli.OrchestratorClient", lambda *a, **k: client)
    monkeypatch.setattr("inert_lander.cli.read_inert_landing", lambda *a, **k: rule)


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

    def refuse(*_: Any, **__: Any) -> InertLanding:
        raise LandingPolicyError("declares no inert population")

    monkeypatch.setattr("inert_lander.cli.read_inert_landing", refuse)
    assert run([]) == EXIT_UNUSABLE
    assert github.enumerated == []


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
    rule = InertLanding(
        version=99,
        repositories=frozenset({REPOSITORY}),
        permitted_authors=frozenset({BOT}),
        excluded_ecosystems=frozenset(),
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
    assert client.landed == [(REPOSITORY, 1, HEAD, _key(REPOSITORY, 1, HEAD))]


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
