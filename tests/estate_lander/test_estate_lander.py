"""The caller: what it asks, what it relays, and what it never decides.

ADR-0019 increment 5b. The assertions that matter are about restraint -- that a held pull request
is reported rather than retried, that a pass without `--submit` asks for nothing, and that the
head the landing names is the head the answer was about.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from change_proposer.change_manager import ChangeManagerError
from estate_lander.cli import (
    _NOT_A_FINDING,
    _REPORTED,
    _UPDATE_SELF_CLEARING,
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_TOOL_FAILURE,
    Outcome,
    _branch_updates,
    _key,
    _pass,
    _subjects,
    _update_key,
    report,
    run,
)
from estate_lander.orchestrator_client import (
    LandingRefused,
    OrchestratorClient,
    OrchestratorError,
)

REPOSITORY = "alobarquest/change-manager"
HEAD = "9f7f6ea6b3adde1cfc712f737647bc308cadb59a"


class FakeRecords:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def records(self) -> list[dict[str, Any]]:
        return self._rows


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
        # ADR-0019 Increment 6. The list a refusal test asserts is EMPTY: an implementation that
        # asked and then reported the refusal would satisfy a status assertion and fail this.
        self.updated: list[tuple[str, int, str, str]] = []

    def admission(self, repository: str, pr_number: int) -> dict[str, Any]:
        self.asked.append((repository, pr_number))
        if self._admission_error is not None:
            raise self._admission_error
        return self._answers.get(
            (repository, pr_number),
            {"satisfied": False, "refusals": ["landing_record_absent"], "head_sha": None},
        )

    def land(self, repository, pr_number, *, head_sha, idempotency_key):
        self.landed.append((repository, pr_number, head_sha, idempotency_key))
        if self._land_error is not None:
            raise self._land_error
        return {"status": "merged"}

    def update_branch(self, repository, pr_number, *, head_sha, idempotency_key):
        self.updated.append((repository, pr_number, head_sha, idempotency_key))
        if self._update_error is not None:
            raise self._update_error
        return {
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "replayed": self._replayed,
        }


def _row(number: int, *, status: str = "approved", item_id: int = 50) -> dict[str, Any]:
    return {
        "id": item_id,
        "target_repository": REPOSITORY,
        "pull_request_number": number,
        "status": status,
    }


def _admissible() -> dict[str, Any]:
    return {"satisfied": True, "refusals": [], "head_sha": HEAD}


def test_a_dry_run_asks_the_question_and_requests_nothing() -> None:
    """The default. An operator reaching for "just show me what would happen" must not land."""
    client = FakeOrchestrator({(REPOSITORY, 49): _admissible()})

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=False)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["would-land"]
    assert client.asked == [(REPOSITORY, 49)]
    assert client.landed == []


def test_an_admissible_pull_request_is_landed_on_the_head_the_answer_was_about() -> None:
    """Naming the answer's head is what stops a rebase between the answer and the request landing
    a tree nobody evaluated -- the orchestrator refuses any other."""
    client = FakeOrchestrator({(REPOSITORY, 49): _admissible()})

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["landed"]
    assert client.landed[0][2] == HEAD


def test_a_held_pull_request_names_the_condition_it_misses_and_is_a_FINDING() -> None:
    """A first pass that lands nothing while naming why is the condition working. It is still a
    finding: somebody has to decide whether to act on the condition."""
    client = FakeOrchestrator(
        {
            (REPOSITORY, 49): {
                "satisfied": False,
                "refusals": ["landing_head_not_current_with_base"],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert "landing_head_not_current_with_base" in outcomes[0].detail
    assert client.landed == []
    assert report(outcomes) == EXIT_FINDINGS


@pytest.mark.parametrize(
    "status",
    [
        "pending",
        "wontfix",
        "resolved",
        "deferred",
        "blocked",
        "in_progress",
        "handed_off",
        "failed",
    ],
)
def test_only_an_APPROVED_record_is_asked_about(status: str) -> None:
    """It is not held on a condition -- it is waiting for the policy to approve its shape, which
    happens in the producer's pass. Asking would report it as a finding here and send somebody to
    the wrong place."""
    client = FakeOrchestrator()

    outcomes = _pass(_subjects(FakeRecords([_row(49, status=status)])), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == [] and client.asked == [] and client.landed == []


def test_a_refused_landing_is_reported_rather_than_retried() -> None:
    client = FakeOrchestrator({(REPOSITORY, 49): _admissible()}, land_error=LandingRefused("no"))

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert len(client.landed) == 1


def test_an_unreadable_admission_answer_lands_nothing() -> None:
    client = FakeOrchestrator(admission_error=OrchestratorError("unreachable"))

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.landed == []


def test_an_admissible_answer_with_no_head_lands_nothing() -> None:
    """Unreachable through the orchestrator's own cascade. Acting without a head would be asking
    for whatever has been pushed since, which is the one thing naming it prevents."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": True, "refusals": [], "head_sha": ""}}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.landed == []


def test_the_idempotency_key_is_content_addressed_so_a_replay_is_a_replay() -> None:
    """A random key would make every pass a new request for the same act, which the orchestrator
    refuses as a key belonging to a different subject -- turning a re-run into a finding."""
    assert _key(REPOSITORY, 49, HEAD) == _key(REPOSITORY, 49, HEAD)
    assert _key(REPOSITORY, 49, HEAD) != _key(REPOSITORY, 50, HEAD)
    assert _key(REPOSITORY, 49, HEAD) != _key(REPOSITORY, 49, "b" * 40)


def test_the_pass_is_ordered_so_which_one_lands_is_reproducible() -> None:
    """The orchestrator permits one landing per repository per window, so WHICH one lands is
    decided here. An order that depended on the listing's would make that arbitrary."""
    client = FakeOrchestrator({(REPOSITORY, n): _admissible() for n in (48, 49, 50)})
    rows = [_row(50, item_id=52), _row(48, item_id=50), _row(49, item_id=51)]

    _pass(_subjects(FakeRecords(rows)), client, submit=False)  # type: ignore[arg-type]

    assert client.asked == [(REPOSITORY, 48), (REPOSITORY, 49), (REPOSITORY, 50)]


def test_a_clean_pass_that_landed_something_is_not_a_finding() -> None:
    assert report([Outcome(REPOSITORY, 49, "landed", "status=merged")]) == EXIT_OK


def test_a_record_naming_no_subject_is_skipped_rather_than_asked_about() -> None:
    client = FakeOrchestrator()
    rows: list[dict[str, Any]] = [
        {"id": 1, "target_repository": None, "pull_request_number": 49, "status": "approved"},
        {
            "id": 2,
            "target_repository": REPOSITORY,
            "pull_request_number": None,
            "status": "approved",
        },
    ]

    assert _pass(_subjects(FakeRecords(rows)), client, submit=True) == []  # type: ignore[arg-type]
    assert client.asked == []


@pytest.mark.parametrize(
    "refusals",
    [
        ["landing_already_recorded", "landing_pull_request_not_open"],
        # The COMMONER case, and the one a first version missed: a person merged the pull request
        # themselves. There is no landing row, so the first refusal is absent -- and the record
        # stays approved forever, because nothing transitions a decision on a merge.
        ["landing_pull_request_not_open"],
    ],
    ids=["we-landed-it", "somebody-else-did"],
)
def test_a_pull_request_whose_subject_has_SETTLED_is_not_a_finding(refusals: list[str]) -> None:
    """Neither is a condition anybody can act on, and reporting either as held makes one landing --
    or one ordinary human merge -- a nightly page forever. A pager that never clears is a pager
    nobody reads."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": refusals, "head_sha": HEAD}}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["settled"]
    assert client.landed == []
    assert report(outcomes) == EXIT_OK


def test_a_genuinely_unmet_condition_is_still_a_finding() -> None:
    """The control for the pair above. Without it, `settled` could grow to swallow everything."""
    client = FakeOrchestrator(
        {
            (REPOSITORY, 49): {
                "satisfied": False,
                "refusals": ["landing_head_not_current_with_base"],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_SETTLED_is_read_with_INTERSECTION_and_ahead_of_every_other_classification() -> None:
    """Production's own refusal set for a pull request somebody had already landed (2026-08-13).

    Two properties in one row, neither of which the pair above can see because both of their
    fixtures happen to be entirely settled refusals. The subset rule the deliberate categories use
    would call this `held` on `mergeability_unknown`; a classification tested before `_SETTLED`
    would too. The pull request is gone, so none of those three says anything.
    """
    client = FakeOrchestrator(
        {
            (REPOSITORY, 50): {
                "satisfied": False,
                "refusals": [
                    "landing_already_recorded",
                    "landing_pace_exhausted",
                    "landing_pull_request_not_open",
                    "landing_mergeability_unknown",
                    "landing_head_not_current_with_base",
                ],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(50)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["settled"]
    assert report(outcomes) == EXIT_OK


@pytest.mark.parametrize(
    "refusal",
    ["landing_pace_exhausted", "landing_outside_change_window"],
)
def test_a_DELIBERATE_refusal_alone_is_not_a_finding(refusal: str) -> None:
    """The daily pace being spent, or the clock being outside the declared hours, is the system
    working as designed. Reporting either makes the one control watching autonomous landings
    permanently red, and a permanently red signal is one nobody reads."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": [refusal], "head_sha": HEAD}}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["deliberate"]
    assert client.landed == []
    assert report(outcomes) == EXIT_OK


@pytest.mark.parametrize(
    "refusals",
    [
        ["landing_checks_awaiting_verdict"],
        ["landing_checks_awaiting_verdict", "landing_head_not_current_with_base"],
        ["landing_checks_in_flight"],
    ],
)
def test_a_check_that_reached_no_verdict_is_a_finding(refusals: list[str]) -> None:
    """THE POLARITY, and the non-membership is deliberate rather than an omission.

    The orchestrator excuses `landing_checks_awaiting_verdict` for ACTING -- bringing the branch up
    to date is what re-runs an abandoned check -- and it is excused for nothing here. A suppression
    keyed on the qualification was written and then measured out: qualifying requires the head to
    be behind, and being behind is itself unexplained, so the line is held whichever way this rule
    goes. The mechanism makes the refusal transient; a label saying so would have been inert, and
    an inert suppression is a fail-open waiting for the freshness rule to be refactored.

    A check STILL RUNNING is a finding for a stronger reason: the lane must not touch it at all,
    because bringing the branch up to date would abandon the very run being waited on.

    The detail line still names the code, so an operator reads the right cause -- which is what
    the split bought -- without this program having to know it.
    """
    client = FakeOrchestrator(
        {
            (REPOSITORY, 60): {
                "satisfied": False,
                "refusals": refusals,
                "branch_update_qualifies": True,
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(60)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_an_EXCEPTION_alone_is_not_a_finding_and_is_NOT_called_deliberate() -> None:
    """A requirement-range bump states no single delta, so no update-type rule applies to it --
    ADR-0018 decided that and left it. It never clears and it waits on a person, which is a
    different thing from a refusal that clears tonight, and the status has to say which."""
    client = FakeOrchestrator(
        {
            (REPOSITORY, 48): {
                "satisfied": False,
                "refusals": ["landing_update_type_unparseable"],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(48)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["exception"]
    assert report(outcomes) == EXIT_OK


def test_an_EXCEPTION_beside_a_DELIBERATE_refusal_is_reported_as_the_EXCEPTION() -> None:
    """Production's `#48` (2026-08-13). The pace resets tonight and the record still cannot land,
    so the exception is the durable fact and is what the line must say."""
    client = FakeOrchestrator(
        {
            (REPOSITORY, 48): {
                "satisfied": False,
                "refusals": ["landing_pace_exhausted", "landing_update_type_unparseable"],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(48)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["exception"]
    assert report(outcomes) == EXIT_OK


def test_a_DELIBERATE_refusal_does_NOT_silence_a_real_condition_beside_it() -> None:
    """THE discriminating control, and it needs both rows in ONE pass: a single case cannot tell a
    subset rule from an intersection rule. `landing_pace_exhausted` co-occurs on every held pull
    request once the day's landing is spent, so an intersection rule silences `#51`'s failing
    checks -- and, that night, essentially everything.

    Both rows are production's own, measured 2026-08-13.
    """
    client = FakeOrchestrator(
        {
            (REPOSITORY, 49): {
                "satisfied": False,
                "refusals": ["landing_pace_exhausted"],
                "head_sha": HEAD,
            },
            (REPOSITORY, 51): {
                "satisfied": False,
                "refusals": [
                    "landing_pace_exhausted",
                    "landing_checks_not_clean",
                    "landing_head_not_current_with_base",
                ],
                "head_sha": HEAD,
            },
        }
    )
    rows = [_row(49, item_id=51), _row(51, item_id=53)]

    outcomes = _pass(_subjects(FakeRecords(rows)), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["deliberate", "held"]
    assert report(outcomes) == EXIT_FINDINGS


_BEHIND = "landing_head_not_current_with_base"
_UNPARSEABLE = "landing_update_type_unparseable"
_CHECKS = "landing_checks_not_clean"
_PACE = "landing_pace_exhausted"


@pytest.mark.parametrize(
    ("refusals", "verdict"),
    [
        ([_BEHIND], "held"),
        ([_BEHIND, _UNPARSEABLE], "exception"),
        ([_PACE, _BEHIND, _UNPARSEABLE], "exception"),
        ([_BEHIND, _CHECKS], "held"),
        ([_PACE, _BEHIND], "held"),
        ([_BEHIND, _UNPARSEABLE, _CHECKS], "held"),
        ([_CHECKS, _UNPARSEABLE], "held"),
    ],
)
def test_being_behind_is_SUPPRESSED_BESIDE_AN_EXCEPTION_and_is_a_finding_everywhere_else(
    refusals: list[str], verdict: str
) -> None:
    """Devon's THIRD refusal ruling, 2026-08-14, and it needs the whole table.

    A single positive case cannot tell this rule from "being behind is always suppressed", so the
    rows that carry the load are the negative ones, and WHICH row catches WHICH mistake was measured
    rather than assumed -- the two obvious fail-open forms are killed by different rows:

    * subtracting freshness with the condition dropped is invisible to `{behind, checks}` and is
      caught only by `{behind}` alone and `{pace, behind}`;
    * returning early on freshness being present is caught by `{behind, checks}` as well.

    `{pace, behind}` is the most valuable single row -- it kills both of those and the
    key-on-a-deliberate-refusal mistake too -- and it appears in no version of this rule's
    specification. `{checks, unparseable}` catches suppressing the wrong code, and nothing else
    does. `{pace}` alone stays `deliberate` and is covered by
    `test_a_DELIBERATE_refusal_alone_is_not_a_finding`.

    Every row is reachable: production composes `landing_outside_change_window` beside freshness on
    every subject outside the window, and `#48` carries the three-member exception row.
    """
    client = FakeOrchestrator(
        {(REPOSITORY, 48): {"satisfied": False, "refusals": refusals, "head_sha": HEAD}}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(48)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == [verdict]


def test_an_EXCEPTION_silences_being_behind_WITHOUT_silencing_a_failing_check_beside_it() -> None:
    """THE DISCRIMINATING CONTROL, and it needs both rows in ONE pass, like its sibling above.

    Were freshness suppressed generally, both of these would be non-findings and the pass would
    exit clean -- so the exit code, not just the statuses, is what this asserts. The first row is
    `#48`'s live shape, which the lane deliberately never freshens; the second is a pull request
    whose checks are failing and which happens also to be behind, which is a real condition and
    must reach a person.
    """
    client = FakeOrchestrator(
        {
            (REPOSITORY, 48): {
                "satisfied": False,
                "refusals": [_BEHIND, _UNPARSEABLE],
                "head_sha": HEAD,
            },
            (REPOSITORY, 51): {
                "satisfied": False,
                "refusals": [_BEHIND, _CHECKS],
                "head_sha": HEAD,
            },
        }
    )
    rows = [_row(48, item_id=51), _row(51, item_id=53)]

    outcomes = _pass(_subjects(FakeRecords(rows)), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["exception", "held"]
    assert report(outcomes) == EXIT_FINDINGS


_ROLLOUT = "landing_rollout_moved"


def _answer(refusals: list[str], **extra: Any) -> dict[str, Any]:
    return {"satisfied": False, "refusals": refusals, "head_sha": HEAD, **extra}


@pytest.mark.parametrize(
    ("refusals", "base_matches", "verdict"),
    [
        # `brain#31`/`#32` as production carries them on 2026-08-16: behind their base, a rollout
        # pin that differs BECAUSE they are behind, and a requirement-range title no rule can ever
        # classify. Held and reported every night, forever, until this.
        ([_BEHIND, _ROLLOUT, _UNPARSEABLE], True, "exception"),
        # THE HALF A POSITIVE CASE CANNOT PROVE. Identical refusals, opposite base comparison: the
        # workflow genuinely moved, freshening cannot put that right, and it must still report.
        # Without this row the rule is indistinguishable from suppressing the code unconditionally.
        ([_BEHIND, _ROLLOUT, _UNPARSEABLE], False, "held"),
        # THE CONJUNCT. The base carries the pinned bytes and the head is NOT behind, so the pin
        # differs because this pull request's own diff edits the workflow -- the founding case of
        # the guard, which no exception beside it may silence.
        ([_ROLLOUT, _UNPARSEABLE], True, "held"),
        # A failing check is a fact about the change and is never position-caused.
        ([_BEHIND, _ROLLOUT, _CHECKS, _UNPARSEABLE], True, "held"),
        # Suppression is only ever BESIDE AN EXCEPTION. Alone, being behind is transient and the
        # branch-update pass clears it, so it stays a finding until it does.
        ([_BEHIND, _ROLLOUT], True, "held"),
        # #168 preserved unchanged: the enumeration this criterion replaced still answers the case
        # it was written for.
        ([_BEHIND, _UNPARSEABLE], True, "exception"),
    ],
)
def test_a_refusal_CAUSED_BY_BEING_BEHIND_is_suppressed_beside_an_exception(
    refusals: list[str], base_matches: bool, verdict: str
) -> None:
    """ADR-0024, and the table is the rule -- the criterion cannot be shown by any single row.

    The previous ruling suppressed ONE code beside an exception. `brain#31`/`#32` produced a
    second the same way: the lane deliberately never freshens a pull request it can never land, so
    the head never acquires the pinned rollout workflow and the pin refusal persists forever --
    rebuilding the permanently-red control the first ruling exists to prevent, out of the third
    ruling's own correctness.

    So what is suppressed is a CATEGORY: a refusal produced by the head's position relative to its
    base that says nothing about the change. Rows two, three and four are the ones that carry the
    load, and each denies a different over-general reading.
    """
    client = FakeOrchestrator(
        {(REPOSITORY, 31): _answer(refusals, rollout_base_matches_pin=base_matches)}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(31)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == [verdict]
    # THE LINE STILL PRINTS EVERY REFUSAL. Suppression governs the exit code and never the report:
    # a reader must be able to see what was missed on a night nothing was a finding.
    assert all(refusal in outcomes[0].detail for refusal in refusals)


def test_an_answer_that_DOES_NOT_CARRY_the_base_comparison_leaves_the_line_a_FINDING() -> None:
    """THE DEPLOYMENT-SKEW CONTROL, and it is a live state rather than a hypothetical one.

    This program runs on a schedule against whatever orchestrator production is serving, and a
    field is on the wire only once a release carrying it has been deployed. In that window the
    answer has no such key -- and a missing key must withhold the criterion's conditional member,
    never supply it. Reading `None` as permission would silence `brain#31` on an answer that never
    said the base matched anything.
    """
    client = FakeOrchestrator({(REPOSITORY, 31): _answer([_BEHIND, _ROLLOUT, _UNPARSEABLE])})

    outcomes = _pass(_subjects(FakeRecords([_row(31)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_a_GENUINELY_MOVED_workflow_still_reports_while_a_STALE_PIN_beside_it_goes_quiet() -> None:
    """THE DISCRIMINATING CONTROL, both rows in ONE pass, like its two siblings above.

    Were the rollout refusal suppressed unconditionally beside an exception, both of these would be
    non-findings and the pass would exit clean -- so the exit code is what this asserts, not only
    the statuses. The rows differ in exactly one fact, and it is the fact the orchestrator serves.
    """
    client = FakeOrchestrator(
        {
            (REPOSITORY, 31): _answer(
                [_BEHIND, _ROLLOUT, _UNPARSEABLE], rollout_base_matches_pin=True
            ),
            (REPOSITORY, 32): _answer(
                [_BEHIND, _ROLLOUT, _UNPARSEABLE], rollout_base_matches_pin=False
            ),
        }
    )
    rows = [_row(31, item_id=61), _row(32, item_id=62)]

    outcomes = _pass(_subjects(FakeRecords(rows)), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["exception", "held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_an_UNCLASSIFIED_refusal_alone_is_a_finding() -> None:
    """The polarity. Only the three codes Devon ruled on are classified; every one of the others,
    present and future, must leave the line a finding on its own -- a code co-occurring with a
    known condition would be reported either way and so discriminates nothing."""
    client = FakeOrchestrator(
        {
            (REPOSITORY, 49): {
                "satisfied": False,
                "refusals": ["landing_estate_unknown"],
                "head_sha": HEAD,
            }
        }
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_an_unsatisfied_answer_that_names_NO_refusal_is_a_finding() -> None:
    """The subset test alone reads an empty set as "every refusal is deliberate". An answer that
    refuses while saying nothing is the orchestrator failing to say why, which is precisely what a
    person should be told about."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": [], "head_sha": HEAD}}
    )

    outcomes = _pass(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_a_status_nobody_classified_is_a_finding() -> None:
    """The polarity of `_NOT_A_FINDING`, one column over from the refusal codes. It is stated as
    the set to EXCLUDE, so a status a later increment adds and forgets to classify is reported
    rather than silently dropped from the exit code."""
    assert report([Outcome(REPOSITORY, 49, "invented", "")]) == EXIT_FINDINGS


def test_the_summary_counts_every_status_so_its_parts_sum_to_what_was_considered() -> None:
    """A literal pin, not one derived from `_REPORTED` -- a fixture built by iterating it would
    shrink with it and assert nothing. Three of these (`would-land`, `unreadable`, `error`) were
    absent from the summary before, so a dry run reported "1 considered" and then four zeros."""
    assert set(_REPORTED) == {
        "landed",
        "would-land",
        "held",
        "deliberate",
        "exception",
        "settled",
        "unreadable",
        "error",
        # ADR-0019 Increment 6, the branch-update pass. Both are printed and neither is a finding:
        # bringing a branch up to date is the lane clearing a condition the lane itself caused.
        "updated",
        "would-update",
    }
    assert _NOT_A_FINDING < set(_REPORTED)


# ------------------------------------------------------------------------------------------------
# ADR-0019 Increment 6: the branch-update pass.
#
# The rule itself lives in the orchestrator and is tested there. What is tested HERE is that this
# program relays it: it asks, it acts only on what it was told, and it prints a line either way.
# ------------------------------------------------------------------------------------------------


def _qualifies() -> dict[str, Any]:
    """Held on freshness plus the day's pace -- the shape a landing creates for every sibling."""
    return {
        "satisfied": False,
        "refusals": ["landing_pace_exhausted", "landing_head_not_current_with_base"],
        "head_sha": HEAD,
        "branch_update_qualifies": True,
    }


def _does_not_qualify() -> dict[str, Any]:
    """`#48`'s shape: behind its base AND a requirement-range bump nothing can ever classify."""
    return {
        "satisfied": False,
        "refusals": [
            "landing_pace_exhausted",
            "landing_head_not_current_with_base",
            "landing_update_type_unparseable",
        ],
        "head_sha": HEAD,
        "branch_update_qualifies": False,
    }


def test_a_dry_run_reports_what_it_would_update_and_asks_for_nothing() -> None:
    """The whole reason the answer carries the verdict: a dry run can say what a live pass would
    do without touching a branch. A program that had to POST to find out could not have one."""
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()})

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=False)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["would-update"]
    assert client.updated == []


def test_a_branch_the_orchestrator_says_qualifies_is_brought_up_to_date() -> None:
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()})

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["updated"]
    assert client.updated == [(REPOSITORY, 49, HEAD, _update_key(REPOSITORY, 49, HEAD))]


def test_a_branch_that_does_not_qualify_is_NOT_ASKED_ABOUT_and_gets_no_line() -> None:
    """THE STANDING LIVE CONTROL in this program's own terms. `#48` can never land, so a build
    spent on it buys nothing; and it gets no second line because the landing pass has already
    printed one naming every condition it misses."""
    client = FakeOrchestrator({(REPOSITORY, 48): _does_not_qualify()})

    outcomes = _branch_updates(_subjects(FakeRecords([_row(48)])), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == []
    assert client.updated == []


def test_the_MATCHED_PAIR_is_separated_in_ONE_pass() -> None:
    """Both live pull requests together, which is the case a single positive test cannot cover: it
    could not tell this rule apart from "update everything that is behind"."""
    client = FakeOrchestrator(
        {(REPOSITORY, 48): _does_not_qualify(), (REPOSITORY, 49): _qualifies()}
    )
    records = FakeRecords([_row(48, item_id=48), _row(49, item_id=49)])

    outcomes = _branch_updates(_subjects(records), client, submit=True)  # type: ignore[arg-type]

    assert [(o.number, o.status) for o in outcomes] == [(49, "updated")]
    assert [number for _, number, _, _ in client.updated] == [49]


def test_the_key_is_content_addressed_over_the_head_so_a_rerun_is_a_replay() -> None:
    """And so a LATER update, after the base moves again, is a genuinely different key -- which is
    what stops one night's landing barring this branch forever."""
    assert _update_key(REPOSITORY, 49, HEAD) == _update_key(REPOSITORY, 49, HEAD)
    assert _update_key(REPOSITORY, 49, HEAD) != _update_key(REPOSITORY, 49, "d" * 40)
    assert _update_key(REPOSITORY, 49, HEAD) != _key(REPOSITORY, 49, HEAD)


def test_an_orchestrator_refusal_is_reported_rather_than_retried() -> None:
    """The answer and the act are separate transactions, so the orchestrator may compose a
    different answer when asked to act. That is a line in the report, not a loop."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): _qualifies()}, update_error=LandingRefused("no longer qualifies")
    )

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]


def test_an_unreadable_answer_updates_nothing() -> None:
    client = FakeOrchestrator(admission_error=OrchestratorError("unreachable"))

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.updated == []


def test_an_answer_with_no_verdict_at_all_updates_nothing() -> None:
    """An orchestrator too old to carry the field. Absence must read as "no", never as "yes"."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": ["x"], "head_sha": HEAD}}
    )

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == []
    assert client.updated == []


def test_a_record_nobody_routed_is_not_asked_about_by_either_pass() -> None:
    """Both passes read the same subject filter, so they cannot disagree about which pull requests
    this program is for."""
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()})

    subjects = _subjects(FakeRecords([_row(49, status="pending")]))  # type: ignore[arg-type]

    outcomes = _branch_updates(subjects, client, submit=True)  # type: ignore[arg-type]

    assert outcomes == []
    assert client.asked == []


def test_neither_update_status_is_a_finding() -> None:
    """An update happening is the lane clearing a condition it caused -- the system working."""
    assert (
        report(
            [
                Outcome(REPOSITORY, 49, "updated", ""),
                Outcome(REPOSITORY, 49, "would-update", ""),
            ]
        )
        == EXIT_OK
    )


def test_the_landing_pass_runs_BEFORE_the_branch_update_pass(monkeypatch) -> None:
    """THE ORDERING IS A DESIGN DECISION AND IT IS LOAD-BEARING, so it is pinned rather than
    described.

    A landing moves the base, so it is the act that puts every sibling behind. Going the other way
    round would bring a branch up to date and then immediately stale it again by landing something
    else -- spending a real build on a tree that is out of date before it finishes, which is the
    exact waste this whole increment exists to avoid.

    Driven through `run()`, because the order lives there and nowhere else: a test that called the
    two passes itself would be asserting what the test does.
    """
    sequence: list[str] = []

    class SequencedOrchestrator(FakeOrchestrator):
        def land(self, *args, **kwargs):
            sequence.append("land")
            return super().land(*args, **kwargs)

        def update_branch(self, *args, **kwargs):
            sequence.append("update")
            return super().update_branch(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class SequencedRecords(FakeRecords):
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    # 51 lands; 49 is behind and qualifies. Both in one pass, which is the only arrangement in
    # which the order is observable at all.
    client = SequencedOrchestrator(
        {(REPOSITORY, 51): _admissible(), (REPOSITORY, 49): _qualifies()}
    )
    records = SequencedRecords([_row(49, item_id=49), _row(51, item_id=51)])

    monkeypatch.setenv("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("ESTATE_LANDING_ORCHESTRATOR_TOKEN", "orch")
    monkeypatch.setattr("estate_lander.cli.ChangeManagerClient", lambda *a, **k: records)
    monkeypatch.setattr("estate_lander.cli.OrchestratorClient", lambda *a, **k: client)

    run(["--submit"])

    assert sequence == ["land", "update"]


# ------------------------------------------------------------------------------------------------
# What a POST-time refusal MEANS. The answer and the act are separate transactions, so the
# orchestrator legitimately recomposes a different answer when asked to act -- and some of those
# refusals name a condition somebody must look at while others say only that the world moved.
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(_UPDATE_SELF_CLEARING))
def test_a_refusal_that_only_says_THE_ANSWER_MOVED_is_not_a_finding(code: str) -> None:
    """The update bot rebasing in the window between the read and the request, or a mergeability
    the platform had not finished computing, are not things anybody can act on -- and the second is
    ordinary rather than exotic. Reporting them as findings rebuilds the class the estate closed
    one commit before this branch: a deliberate, self-clearing refusal reported as something that
    could not be measured."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): _qualifies()}, update_error=LandingRefused("moved", code)
    )

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["deliberate"]
    assert report(outcomes) == EXIT_OK


@pytest.mark.parametrize(
    "code",
    ["estate_branch_update_refused_by_remote", "idempotency_conflict", ""],
    ids=["remote-refused", "key-conflict", "no-code-parsed"],
)
def test_EVERY_OTHER_refusal_is_still_a_finding(code: str) -> None:
    """Including one this program could not parse a code from: an answer it cannot classify must
    fail toward being reported, which is the polarity the whole file argues for."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): _qualifies()}, update_error=LandingRefused("no", code)
    )

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_a_REPLAY_means_the_branch_never_moved_and_is_a_finding() -> None:
    """THE FAILURE THAT WOULD OTHERWISE DESCRIBE ITSELF AS SUCCESS FOREVER.

    The key is content-addressed over the head and a success moves the head, so a replay is a
    request about a branch that did not move -- i.e. the platform accepted the work (202) and did
    not deliver it. Every subsequent pass would compute the same key, replay the same event, never
    call the remote, and print `updated`, which is not a finding.
    """
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()}, replayed=True)

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert "still behind" in outcomes[0].detail
    assert report(outcomes) == EXIT_FINDINGS


def test_a_FRESH_act_is_reported_as_an_update_and_is_not_a_finding() -> None:
    """The pair to the case above, so a classifier that called everything a replay -- or nothing
    one -- reddens."""
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()}, replayed=False)

    outcomes = _branch_updates(_subjects(FakeRecords([_row(49)])), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["updated"]
    assert report(outcomes) == EXIT_OK


def test_a_record_source_that_fails_MID_PASS_never_discards_a_landing_that_happened(
    monkeypatch,
) -> None:
    """THE REPORT OF A PRODUCTION MUTATION MUST NOT BE A LOCAL IN A TRY BLOCK.

    An earlier version read the change records again for the second pass. That put a second network
    call inside `run()`'s `try`, where a transient failure raises `ChangeManagerError`, returns a
    bare tool error, and discards every outcome the landing pass had already collected -- so a pass
    that landed a pull request into a repository where landing IS deploying would report nothing at
    all. Reading once removes the window rather than handling it.
    """
    reads: list[int] = []

    class CountingRecords(FakeRecords):
        def records(self):
            reads.append(1)
            if len(reads) > 1:
                raise ChangeManagerError("the record service is unreachable")
            return super().records()

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class EnterableOrchestrator(FakeOrchestrator):
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    client = EnterableOrchestrator({(REPOSITORY, 51): _admissible()})
    monkeypatch.setenv("ESTATE_LANDING_CHANGE_MANAGER_TOKEN", "cm")
    monkeypatch.setenv("ESTATE_LANDING_ORCHESTRATOR_TOKEN", "orch")
    monkeypatch.setattr(
        "estate_lander.cli.ChangeManagerClient",
        lambda *a, **k: CountingRecords([_row(51, item_id=51)]),
    )
    monkeypatch.setattr("estate_lander.cli.OrchestratorClient", lambda *a, **k: client)

    exit_code = run(["--submit"])

    assert reads == [1], "the record source must be read exactly once per pass"
    assert client.landed, "the landing still happened"
    assert exit_code != EXIT_TOOL_FAILURE


def test_the_status_column_is_wide_enough_for_the_widest_status(capsys) -> None:
    """`would-update` is twelve characters. A narrower column pushes the detail out of alignment
    on exactly the lines a dry run exists to produce."""
    report([Outcome(REPOSITORY, 49, "would-update", "head abc")])

    line = capsys.readouterr().out.splitlines()[0]
    assert "would-update head abc" in line


def test_the_refusal_code_is_read_from_where_a_domain_error_actually_puts_it() -> None:
    """NESTED under `error`. A check written from the exception handler's own shape matches neither
    that nor the framework's `detail`, and the classifier that decides whether a refusal is a
    finding is keyed on this value."""
    client = OrchestratorClient(
        "t",
        "k",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                409,
                json={"error": {"code": "estate_branch_update_head_moved", "message": "moved"}},
            )
        ),
    )

    with pytest.raises(LandingRefused) as raised:
        client.update_branch("owner/repo", 49, head_sha="a" * 40, idempotency_key="k")

    assert raised.value.code == "estate_branch_update_head_moved"
    assert str(raised.value) == "moved"


@pytest.mark.parametrize(
    "body",
    [
        {"detail": "Not Found"},
        {"error": {"message": "no code here"}},
        {"error": "not an object"},
        "not an object at all",
    ],
    ids=["framework-shape", "no-code", "error-not-an-object", "not-an-object"],
)
def test_a_body_this_program_cannot_read_yields_NO_CODE_rather_than_a_guess(body) -> None:
    """The empty string matches no classifier, so an answer this program cannot parse stays a
    finding -- never silently self-clearing."""
    client = OrchestratorClient(
        "t", "k", transport=httpx.MockTransport(lambda request: httpx.Response(409, json=body))
    )

    with pytest.raises(LandingRefused) as raised:
        client.update_branch("owner/repo", 49, head_sha="a" * 40, idempotency_key="k")

    assert raised.value.code == ""
