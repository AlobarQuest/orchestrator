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
from work_carrier.change_manager import ChangeManagerError, WorkRecord
from work_watcher.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, run
from work_watcher.github import (
    CLOSED,
    MERGED,
    OPEN,
    GitHubCredentialRefused,
    GitHubError,
    PullRequestReader,
)
from work_watcher.orchestrator_client import WorkCompletion
from work_watcher.pipeline import Pipeline, PipelineRecord
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
    def __init__(
        self,
        *records: WorkRecord,
        others: tuple[PipelineRecord, ...] = (),
        unreadable: tuple[str, ...] = (),
    ) -> None:
        self._records = records
        self._others = others
        self._unreadable = unreadable

    def approved_work(self) -> tuple[WorkRecord, ...]:
        return self._records

    def work_pipeline(self) -> Pipeline:
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
        return Pipeline(approved + self._others, self._unreadable)


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
    # Review item 2: a closed pull request may have been re-opened for the same bump under a new
    # number, which mints no revision; the instruction must send a person to check first.
    assert "re-opened this bump" in out
    assert "a person retires this record" not in out
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


def test_a_superseded_record_whose_work_is_in_flight_says_so_rather_than_can_never_register() -> (
    None
):
    """Review finding: revision r was carried and is executing when r+1 is proposed. Still a
    finding, but "the carry can never register this revision" would be false -- it already did."""
    newer = row(change_record_id=80, package_revision=3, reasoning=reasoning(number=90))
    code, out = _run(
        [],
        Source(record(), others=(newer,)),
        Reader({61: incomplete()}),
        GitHub({(REPO, 90): OPEN}),
    )
    assert code == EXIT_FINDINGS
    assert "already in flight" in out
    assert "can never register" not in out


@pytest.mark.parametrize("states", [("failed",), ("cancelled",), ("failed", "cancelled")])
def test_a_superseded_record_whose_units_all_settled_is_not_called_in_flight(
    states: tuple[str, ...],
) -> None:
    """Review item 3: a unit that failed or was cancelled is not running."""
    newer = row(change_record_id=80, package_revision=3, reasoning=reasoning(number=90))
    settled = WorkCompletion(all_units_completed=False, unit_states=states, revision_count=1)
    code, out = _run(
        [], Source(record(), others=(newer,)), Reader({61: settled}), GitHub({(REPO, 90): OPEN})
    )
    assert code == EXIT_FINDINGS
    assert "already in flight" not in out


def test_the_settled_states_are_orchestrator_states() -> None:
    """A second copy of a vocabulary across a process boundary, so it is pinned to the source."""
    from orchestrator.kernel.states import WorkUnitState
    from work_watcher.cli import SETTLED_UNIT_STATES

    assert SETTLED_UNIT_STATES == {
        WorkUnitState.COMPLETED.value,
        WorkUnitState.FAILED.value,
        WorkUnitState.CANCELLED.value,
    }


def test_a_superseded_record_with_no_work_yet_can_never_be_registered() -> None:
    newer = row(change_record_id=80, package_revision=3, reasoning=reasoning(number=90))
    none_yet = WorkCompletion(all_units_completed=False, unit_states=(), revision_count=0)
    code, out = _run(
        [], Source(record(), others=(newer,)), Reader({61: none_yet}), GitHub({(REPO, 90): OPEN})
    )
    assert code == EXIT_FINDINGS
    assert "can never register" in out and "already in flight" not in out


class _BrokenPipeline(Source):
    def work_pipeline(self) -> Pipeline:
        raise ChangeManagerError("a resolved record names no package revision")


def test_an_unreadable_pipeline_does_not_stop_retirement() -> None:
    """Review finding: the staleness listing reads every status, so a malformed OLD record must
    cost a finding, never the retirement that runs before the carry."""
    retirer = Retirer()
    code, out = _run(
        ["--retire"], _BrokenPipeline(record()), Reader({61: complete()}), GitHub({}), retirer
    )
    assert retirer.calls == [61]
    assert code == EXIT_FINDINGS
    assert "work pipeline is unreadable" in out


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


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_github_credential_makes_the_pass_unusable_not_quietly_findings(
    status: int,
) -> None:
    """Review item 1: a revoked token or a rate limit is the credential failing, the same class as
    a missing one. Per-record findings ping the dead-man as success and would never page."""
    refused = GitHubCredentialRefused(f"GitHub answered {status}")
    code, out = _run(
        [], Source(record()), Reader({61: incomplete()}), GitHub({(REPO, 71): refused})
    )
    assert code == EXIT_UNUSABLE
    assert "[FINDING]" not in out


def test_a_missing_pull_request_is_still_a_finding() -> None:
    """The control: a deleted pull request or repository genuinely needs a person."""
    gone = GitHubError("GitHub answered 404")
    code, out = _run([], Source(record()), Reader({61: incomplete()}), GitHub({(REPO, 71): gone}))
    assert code == EXIT_FINDINGS
    assert "could not read" in out


def test_a_malformed_pipeline_row_is_named_and_the_rest_are_still_assessed() -> None:
    """Review item 6: one bad row must not blank the report for every other record."""
    code, out = _run(
        [],
        Source(others=(row(),), unreadable=("change record 99 names no package revision",)),
        Reader({}),
        GitHub({(REPO, 71): CLOSED}),
    )
    assert code == EXIT_FINDINGS
    assert "not assessed: change record 99 names no package revision" in out
    assert "STALE" in out


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


@pytest.mark.parametrize("status", [401, 403])
def test_the_reader_names_a_refused_credential(status: int) -> None:
    with pytest.raises(GitHubCredentialRefused):
        _reader(status, {"message": "Bad credentials"}).state(REPO, 71)


@pytest.mark.parametrize(
    ("status", "body"),
    [(404, {"message": "Not Found"}), (200, {"state": "draft"}), (200, ["not", "an", "object"])],
)
def test_the_reader_refuses_what_it_cannot_interpret(status: int, body: object) -> None:
    with pytest.raises(GitHubError) as raised:
        _reader(status, body).state(REPO, 71)
    # NOT the credential class: a 404 is a fact about this pull request (deleted, or its repository
    # gone) and needs a person per record. `GitHubCredentialRefused` subclasses `GitHubError`, so
    # `raises` alone would pass a reader that paged the whole lane on a missing pull request.
    assert not isinstance(raised.value, GitHubCredentialRefused)


# --- the pipeline listing ----------------------------------------------------------------------


def test_the_pipeline_listing_names_the_source_and_leaves_status_to_the_caller() -> None:
    """Ruling B1's staleness view. `status` is NOT sent: change-manager applies it as a SQL filter,
    which makes a pending record indistinguishable from one that does not exist. Every status is
    projected, and a row from another pipeline is refused as the carry's own parse refuses one."""
    import httpx

    from work_carrier.change_manager import ChangeManagerError
    from work_watcher.pipeline import PipelineListing

    seen: list[dict[str, str]] = []
    rows = [
        {"id": 9, "source": "work", "status": "pending", "package_id": "p", "package_revision": 1},
        {
            "id": 10,
            "source": "work",
            "status": "resolved",
            "package_id": "p",
            "package_revision": 2,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=rows)

    source = PipelineListing(
        base_url="https://example.invalid",
        token="t",
        transport=httpx.MockTransport(handler),
    )
    pipeline = source.work_pipeline()

    assert seen == [{"source": "work"}]
    assert [(r.change_record_id, r.status) for r in pipeline.records] == [
        (9, "pending"),
        (10, "resolved"),
    ]
    assert pipeline.unreadable == ()

    rows.append({"id": 12, "source": "work", "status": "pending", "package_id": "p"})
    isolated = source.work_pipeline()
    assert [r.change_record_id for r in isolated.records] == [9, 10]
    assert len(isolated.unreadable) == 1 and "12" in isolated.unreadable[0]
    rows.pop()

    rows.append(
        {
            "id": 11,
            "source": "deploy",
            "status": "pending",
            "package_id": "p",
            "package_revision": 1,
        }
    )
    with pytest.raises(ChangeManagerError):
        source.work_pipeline()


def test_the_pipeline_parse_reads_only_declared_record_fields() -> None:
    """The cross-repo field check vets `RECORD_FIELDS`; a second parse reading an undeclared key
    would be a read nothing vets. Same derivation as the check on `_record` above."""
    import ast
    import inspect

    from work_carrier import change_manager
    from work_watcher import pipeline

    tree = ast.parse(inspect.getsource(pipeline._pipeline_record))
    read = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "row"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert read, "the scan found no `row.get(...)` calls; it has stopped seeing the parse"
    assert read <= set(change_manager.RECORD_FIELDS)
