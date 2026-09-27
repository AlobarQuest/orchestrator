"""Ruling B1 (Devon, 2026-09-27): a stale work record is REPORTED, and a person retires it.

A change-manager `work` record is stale when it is pending or approved while a newer revision's
record has superseded it, or while the Dependabot pull request it names was closed or merged by
hand. The watcher reports it as a finding in its existing vocabulary (exit 3) and holds no write
that could retire it.

**EVERY STALE CASE HERE HAS A DISCRIMINATING TWIN.** The same record with its pull request OPEN is
not a finding, and a record whose work the factory BUILT is retired rather than reported even though
its pull request is closed -- the ordinary success path produces exactly the observable a stale
record does, and only the completion verdict tells them apart.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from bump_proposer.cli import _reasoning
from bump_proposer.standing import StandingPackage
from landing_ledger.model import PendingUpdate
from landing_ledger.titles import Bump
from work_carrier.change_manager import PipelineRecord, WorkRecord
from work_watcher.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from work_watcher.github import CLOSED, MERGED, OPEN, GitHubError, PullRequestReader
from work_watcher.orchestrator_client import WorkCompletion
from work_watcher.staleness import pull_request_of, superseded_by

REPO = "AlobarQuest/infraops-mcp-server"
PACKAGE = "infraops-mcp-server-npm-zod"


def reasoning(number: int = 71, repository: str = REPO) -> str:
    """The reasoning bump-proposer ACTUALLY writes, composed by its own function."""
    package = StandingPackage(
        package_id=PACKAGE,
        path=Path("/nonexistent"),
        target_repository=repository,
        dependency="zod",
        revision=2,
        state="approved",
        from_version="3.25.76",
        to_version="4.4.3",
    )
    bump = Bump(from_version="3.25.76", to_version="4.4.3", kind="major")
    pending = PendingUpdate(
        repository=repository,
        number=number,
        head_commit="0" * 40,
        opened_at=datetime(2026, 9, 1, tzinfo=UTC),
        armed=False,
        title="Bump zod from 3.25.76 to 4.4.3",
    )
    return _reasoning(package, bump, pending)


def record(**overrides) -> WorkRecord:
    base = {
        "change_record_id": 61,
        "package_id": PACKAGE,
        "package_revision": 2,
        "package_source_repository": "AlobarQuest/intent-packages",
        "reasoning": reasoning(),
        "decided_by": "devon",
    }
    return WorkRecord(**{**base, **overrides})


def row(**overrides) -> PipelineRecord:
    base = {
        "change_record_id": 70,
        "status": "pending",
        "package_id": PACKAGE,
        "package_revision": 2,
        "reasoning": reasoning(),
    }
    return PipelineRecord(**{**base, **overrides})


class Source:
    def __init__(self, *records: WorkRecord, others: tuple[PipelineRecord, ...] = ()) -> None:
        self._records = records
        self._others = others

    def approved_work(self) -> tuple[WorkRecord, ...]:
        return self._records

    def work_pipeline(self) -> tuple[PipelineRecord, ...]:
        approved = tuple(
            PipelineRecord(
                change_record_id=r.change_record_id,
                status="approved",
                package_id=r.package_id,
                package_revision=r.package_revision,
                reasoning=r.reasoning,
            )
            for r in self._records
        )
        return approved + self._others


class Reader:
    def __init__(self, answers: dict[int, WorkCompletion]) -> None:
        self._answers = answers

    def work_for(self, change_record_id: int) -> WorkCompletion:
        return self._answers[change_record_id]


class Retirer:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def retire(self, item_id: int, *, package_id: str, package_revision: int) -> dict:
        self.calls.append(item_id)
        return {"id": item_id, "status": "resolved"}


class GitHub:
    """GitHub, answering the state of the one pull request it is asked about."""

    def __init__(self, states: dict[tuple[str, int], str | Exception]) -> None:
        self._states = states
        self.asked: list[tuple[str, int]] = []

    def state(self, repository: str, number: int) -> str:
        self.asked.append((repository, number))
        answer = self._states[(repository, number)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def complete() -> WorkCompletion:
    return WorkCompletion(all_units_completed=True, unit_states=("completed",), revision_count=1)


def incomplete() -> WorkCompletion:
    return WorkCompletion(all_units_completed=False, unit_states=("executing",), revision_count=1)


def _run(argv, source, reader, github, retirer=None) -> tuple[int, str]:
    out = io.StringIO()
    code = run(argv, source=source, reader=reader, retirer=retirer, github=github, out=out)
    return code, out.getvalue()


# --- the reasoning contract ----------------------------------------------------------------------


def test_the_pull_request_is_read_off_what_bump_proposer_actually_writes() -> None:
    """A same-repository contract, pinned to the producer's live output rather than to a copy of
    its template: if `_reasoning` is reworded, this reds instead of every record going unassessed.
    """
    assert pull_request_of(reasoning(number=71)) == (REPO, 71)
    assert pull_request_of(reasoning(number=5, repository="AlobarQuest/brain")) == (
        "AlobarQuest/brain",
        5,
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "the human approved building it",
        f"{REPO} carries an open dependency update of zod (pull request )",
        f"Something else. {REPO} carries an open dependency update of zod (pull request 71)",
    ],
)
def test_reasoning_that_names_no_pull_request_is_not_assessed(text: str) -> None:
    assert pull_request_of(text) is None


def test_supersession_is_any_higher_revision_for_the_same_package_whatever_its_status() -> None:
    pipeline = (
        row(change_record_id=1, package_revision=2, status="approved"),
        row(change_record_id=2, package_revision=3, status="resolved"),
        row(change_record_id=3, package_revision=4, package_id="other-package"),
    )
    newer = superseded_by(PACKAGE, 2, pipeline)
    assert newer is not None and newer.change_record_id == 2
    assert superseded_by(PACKAGE, 3, pipeline) is None


# --- approved records: completion first, staleness second ---------------------------------------


def test_an_approved_record_whose_pull_request_is_open_is_waiting_not_a_finding() -> None:
    """THE CONTROL. Without it every stale case below passes on a watcher that reports all."""
    github = GitHub({(REPO, 71): OPEN})
    code, out = _run([], Source(record()), Reader({61: incomplete()}), github)

    assert code == EXIT_OK
    assert "[WAITING]" in out and f"{REPO}#71 is open" in out
    assert github.asked == [(REPO, 71)]


@pytest.mark.parametrize(
    ("state", "verb"), [(CLOSED, "closed without merging"), (MERGED, "merged")]
)
def test_an_approved_record_whose_pull_request_is_gone_is_a_finding(state: str, verb: str) -> None:
    code, out = _run([], Source(record()), Reader({61: incomplete()}), GitHub({(REPO, 71): state}))

    assert code == EXIT_FINDINGS
    assert "[FINDING]" in out and "STALE" in out and verb in out
    assert "1 findings (1 stale" in out


def test_a_record_whose_work_is_built_is_retired_not_reported_though_its_pull_request_merged() -> (
    None
):
    """THE FALSE-POSITIVE CONTROL: the factory landed it and the pull request is merged. Completion
    is checked FIRST, so this is the ordinary retirement and GitHub is never asked."""
    github = GitHub({(REPO, 71): MERGED})
    retirer = Retirer()
    code, out = _run(["--retire"], Source(record()), Reader({61: complete()}), github, retirer)

    assert code == EXIT_OK
    assert retirer.calls == [61]
    assert github.asked == []
    assert "STALE" not in out


def test_a_superseded_approved_record_is_a_finding_without_asking_github_about_it() -> None:
    """The newer record is itself live and IS assessed; the superseded one never reaches GitHub."""
    github = GitHub({(REPO, 90): OPEN})
    newer = row(change_record_id=80, package_revision=3, reasoning=reasoning(number=90))
    code, out = _run([], Source(record(), others=(newer,)), Reader({61: incomplete()}), github)

    assert code == EXIT_FINDINGS
    assert "superseded by change record 80 (revision 3" in out
    assert github.asked == [(REPO, 90)]


def test_a_superseded_record_is_still_retired_when_its_work_was_built() -> None:
    retirer = Retirer()
    github = GitHub({(REPO, 90): OPEN})
    newer = row(change_record_id=80, package_revision=3, reasoning=reasoning(number=90))
    code, out = _run(
        ["--retire"], Source(record(), others=(newer,)), Reader({61: complete()}), github, retirer
    )
    assert code == EXIT_OK
    assert retirer.calls == [61]
    assert "STALE" not in out


# --- pending records: only staleness applies -----------------------------------------------------


def test_a_pending_record_whose_pull_request_is_open_is_not_a_finding() -> None:
    code, out = _run([], Source(others=(row(),)), Reader({}), GitHub({(REPO, 71): OPEN}))
    assert code == EXIT_OK
    assert "[PENDING]" in out
    assert "0 findings (0 stale, 1 pending)" in out


def test_a_pending_record_whose_pull_request_was_closed_is_a_finding() -> None:
    code, out = _run([], Source(others=(row(),)), Reader({}), GitHub({(REPO, 71): CLOSED}))
    assert code == EXIT_FINDINGS
    assert "STALE" in out and "(pending)" in out


def test_a_superseded_pending_record_is_a_finding() -> None:
    pipeline = (
        row(change_record_id=70, package_revision=2),
        row(change_record_id=71, package_revision=3),
    )
    github = GitHub({(REPO, 71): OPEN})
    code, out = _run([], Source(others=pipeline), Reader({}), github)

    assert code == EXIT_FINDINGS
    assert "change record 70" in out and "superseded by change record 71" in out
    assert "change record 71: " in out and "[PENDING]" in out


@pytest.mark.parametrize("status", ["resolved", "wontfix", "deferred"])
def test_a_record_that_is_not_live_is_not_examined(status: str) -> None:
    github = GitHub({})
    code, out = _run([], Source(others=(row(status=status),)), Reader({}), github)
    assert code == EXIT_OK
    assert github.asked == []
    assert "change record 70" not in out


# --- what is not assessable, and what is unusable ------------------------------------------------


def test_a_record_naming_no_pull_request_is_unassessed_and_not_a_finding() -> None:
    github = GitHub({})
    code, out = _run(
        [], Source(record(reasoning="hand-proposed")), Reader({61: incomplete()}), github
    )
    assert code == EXIT_OK
    assert "staleness not assessed" in out
    assert github.asked == []


def test_a_pass_that_needed_github_without_a_credential_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORK_CARRIER_GITHUB_TOKEN", raising=False)
    code, out = _run([], Source(record()), Reader({61: incomplete()}), None)
    assert code == EXIT_UNUSABLE
    assert "WORK_CARRIER_GITHUB_TOKEN" in out


def test_an_unreadable_pull_request_is_a_finding_and_does_not_stop_the_rest() -> None:
    github = GitHub({(REPO, 71): GitHubError("GitHub answered 502"), (REPO, 72): OPEN})
    second = record(change_record_id=62, package_id="other", reasoning=reasoning(number=72))
    code, out = _run(
        [],
        Source(record(), second),
        Reader({61: incomplete(), 62: incomplete()}),
        github,
    )
    assert code == EXIT_FINDINGS
    assert "could not read" in out
    assert github.asked == [(REPO, 71), (REPO, 72)]


# --- the GitHub reader --------------------------------------------------------------------------


def _reader(status: int, body: object) -> PullRequestReader:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/repos/{REPO}/pulls/71"
        return httpx.Response(status, json=body)

    return PullRequestReader(
        "x",
        client=httpx.Client(
            base_url="https://api.github.example", transport=httpx.MockTransport(handler)
        ),
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"state": "open", "merged_at": None}, OPEN),
        ({"state": "closed", "merged_at": None}, CLOSED),
        ({"state": "closed", "merged_at": "2026-09-04T00:00:00Z"}, MERGED),
    ],
)
def test_the_reader_tells_merged_from_closed_by_merged_at(body: dict, expected: str) -> None:
    assert _reader(200, body).state(REPO, 71) == expected


@pytest.mark.parametrize(
    ("status", "body"),
    [(404, {"message": "Not Found"}), (200, {"state": "draft"}), (200, ["not", "an", "object"])],
)
def test_the_reader_refuses_what_it_cannot_interpret(status: int, body: object) -> None:
    with pytest.raises(GitHubError):
        _reader(status, body).state(REPO, 71)
