"""The caller: what it asks, what it relays, and what it never decides.

ADR-0019 increment 5b. The assertions that matter are about restraint -- that a held pull request
is reported rather than retried, that a pass without `--submit` asks for nothing, and that the
head the landing names is the head the answer was about.
"""

from __future__ import annotations

from typing import Any

import pytest

from estate_lander.cli import EXIT_FINDINGS, EXIT_OK, Outcome, _key, _pass, report
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
    ) -> None:
        self._answers = answers or {}
        self._land_error = land_error
        self._admission_error = admission_error
        self.asked: list[tuple[str, int]] = []
        self.landed: list[tuple[str, int, str, str]] = []

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
