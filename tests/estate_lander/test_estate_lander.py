"""The caller: what it asks, what it relays, and what it never decides.

ADR-0019 increment 5b. The assertions that matter are about restraint -- that a held pull request
is reported rather than retried, that a pass without `--submit` asks for nothing, and that the
head the landing names is the head the answer was about.
"""

from __future__ import annotations

from typing import Any

import pytest

from estate_lander.cli import (
    _NOT_A_FINDING,
    _REPORTED,
    EXIT_FINDINGS,
    EXIT_OK,
    Outcome,
    _branch_updates,
    _key,
    _pass,
    _update_key,
    report,
    run,
)
from estate_lander.orchestrator_client import LandingRefused, OrchestratorError

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
    ) -> None:
        self._answers = answers or {}
        self._land_error = land_error
        self._admission_error = admission_error
        self._update_error = update_error
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
        return {"repository": repository, "pr_number": pr_number, "head_sha": head_sha}


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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=False)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["would-land"]
    assert client.asked == [(REPOSITORY, 49)]
    assert client.landed == []


def test_an_admissible_pull_request_is_landed_on_the_head_the_answer_was_about() -> None:
    """Naming the answer's head is what stops a rebase between the answer and the request landing
    a tree nobody evaluated -- the orchestrator refuses any other."""
    client = FakeOrchestrator({(REPOSITORY, 49): _admissible()})

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(49, status=status)]), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == [] and client.asked == [] and client.landed == []


def test_a_refused_landing_is_reported_rather_than_retried() -> None:
    client = FakeOrchestrator({(REPOSITORY, 49): _admissible()}, land_error=LandingRefused("no"))

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert len(client.landed) == 1


def test_an_unreadable_admission_answer_lands_nothing() -> None:
    client = FakeOrchestrator(admission_error=OrchestratorError("unreachable"))

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.landed == []


def test_an_admissible_answer_with_no_head_lands_nothing() -> None:
    """Unreachable through the orchestrator's own cascade. Acting without a head would be asking
    for whatever has been pushed since, which is the one thing naming it prevents."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": True, "refusals": [], "head_sha": ""}}
    )

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    _pass(FakeRecords(rows), client, submit=False)  # type: ignore[arg-type]

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

    assert _pass(FakeRecords(rows), client, submit=True) == []  # type: ignore[arg-type]
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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(50)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["deliberate"]
    assert client.landed == []
    assert report(outcomes) == EXIT_OK


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

    outcomes = _pass(FakeRecords([_row(48)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords([_row(48)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _pass(FakeRecords(rows), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["deliberate", "held"]
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

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]
    assert report(outcomes) == EXIT_FINDINGS


def test_an_unsatisfied_answer_that_names_NO_refusal_is_a_finding() -> None:
    """The subset test alone reads an empty set as "every refusal is deliberate". An answer that
    refuses while saying nothing is the orchestrator failing to say why, which is precisely what a
    person should be told about."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": [], "head_sha": HEAD}}
    )

    outcomes = _pass(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _branch_updates(FakeRecords([_row(49)]), client, submit=False)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["would-update"]
    assert client.updated == []


def test_a_branch_the_orchestrator_says_qualifies_is_brought_up_to_date() -> None:
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()})

    outcomes = _branch_updates(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["updated"]
    assert client.updated == [(REPOSITORY, 49, HEAD, _update_key(REPOSITORY, 49, HEAD))]


def test_a_branch_that_does_not_qualify_is_NOT_ASKED_ABOUT_and_gets_no_line() -> None:
    """THE STANDING LIVE CONTROL in this program's own terms. `#48` can never land, so a build
    spent on it buys nothing; and it gets no second line because the landing pass has already
    printed one naming every condition it misses."""
    client = FakeOrchestrator({(REPOSITORY, 48): _does_not_qualify()})

    outcomes = _branch_updates(FakeRecords([_row(48)]), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == []
    assert client.updated == []


def test_the_MATCHED_PAIR_is_separated_in_ONE_pass() -> None:
    """Both live pull requests together, which is the case a single positive test cannot cover: it
    could not tell this rule apart from "update everything that is behind"."""
    client = FakeOrchestrator(
        {(REPOSITORY, 48): _does_not_qualify(), (REPOSITORY, 49): _qualifies()}
    )
    records = FakeRecords([_row(48, item_id=48), _row(49, item_id=49)])

    outcomes = _branch_updates(records, client, submit=True)  # type: ignore[arg-type]

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

    outcomes = _branch_updates(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["held"]


def test_an_unreadable_answer_updates_nothing() -> None:
    client = FakeOrchestrator(admission_error=OrchestratorError("unreachable"))

    outcomes = _branch_updates(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert [o.status for o in outcomes] == ["unreadable"]
    assert client.updated == []


def test_an_answer_with_no_verdict_at_all_updates_nothing() -> None:
    """An orchestrator too old to carry the field. Absence must read as "no", never as "yes"."""
    client = FakeOrchestrator(
        {(REPOSITORY, 49): {"satisfied": False, "refusals": ["x"], "head_sha": HEAD}}
    )

    outcomes = _branch_updates(FakeRecords([_row(49)]), client, submit=True)  # type: ignore[arg-type]

    assert outcomes == []
    assert client.updated == []


def test_a_record_nobody_routed_is_not_asked_about_by_either_pass() -> None:
    """Both passes read the same subject filter, so they cannot disagree about which pull requests
    this program is for."""
    client = FakeOrchestrator({(REPOSITORY, 49): _qualifies()})

    outcomes = _branch_updates(FakeRecords([_row(49, status="pending")]), client, submit=True)  # type: ignore[arg-type]

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
