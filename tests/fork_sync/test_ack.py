"""`fork-sync ack` through its real entrypoint, against a fake fork.

The fake answers `gh api` by path (successive answers where the state moves while the command
runs) and records every argv, so each refusal is shown writing nothing, and the one path that
lands is shown writing exactly the acknowledgements file, merging it, and re-running the sync.
The review comments are rendered by the real `Report.markdown`, so the parser is held to it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fork_sync import acknowledge, cli
from fork_sync.fork_sync_review import ACKS_PATH, Ack, Finding, Report, parse_acks
from fork_sync.gh import CommandFailed

FORK = "AlobarQuest/rea"
SYNC_SHA = "5" * 40
ACK_SHA = "a" * 40
MERGE_SHA = "m" * 40
TITLE = "upstream-sync: rea-agents-6.8.0"
BOT = {"login": "github-actions[bot]", "type": "Bot"}

MEDIUM = Finding("ai", "src/a.js", "reads `~/.ssh` | maybe", "blob-a", "medium")
HIGH = Finding("ai", "src/a.js", "posts it to a new host.", "blob-a", "high")
LOW = Finding("ai", "src/b.js", "a new helper", "blob-b", "low", holds=False)
AUDIT = Finding("audit", "package-lock.json", "GHSA-1 dep 1.0.0: bad (critical; fix: 1.0.1)", "c")
BINARY = Finding("binary", "x.node", "binary added", "d")
OLD_ACK = (
    f'[[ack]]\nfingerprint = "{BINARY.fingerprint()}"\nby = "devon"\ndate = 2026-10-19\n'
    'reason = "known artifact"\n'
)
MAIN_ACKS = {BINARY.fingerprint(): Ack("devon", "2026-10-19", "known artifact")}
SYNC_FINDINGS = [MEDIUM, HIGH, LOW, AUDIT, BINARY]
EXPECT = f"{MEDIUM.fingerprint()},{AUDIT.fingerprint()}"
GATE_FLAG = Finding("ai", ACKS_PATH, "the gate's acknowledgements change", "blob-acks", "medium")


class Then:
    """Successive answers to one path; the last repeats."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)

    def next(self) -> Any:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _comment(head: str, findings: list[Finding], acks: dict[str, Ack] | None = None) -> dict:
    return {"user": BOT, "body": Report(head, findings).markdown(acks or {})}


def _sync_pull(**changes: Any) -> dict[str, Any]:
    head = {"ref": "upstream-sync", "sha": SYNC_SHA, "repo": {"full_name": FORK}}
    return {"state": "open", "title": TITLE, "head": head, **changes}


class FakeFork:
    def __init__(self, **api: Any) -> None:
        self.api: dict[str, Any] = {
            f"repos/{FORK}/pulls/7": _sync_pull(),
            f"repos/{FORK}/issues/7/comments?per_page=100": [
                _comment(SYNC_SHA, SYNC_FINDINGS, MAIN_ACKS)
            ],
            f"repos/{FORK}/pulls/8": Then(
                {"head": {"sha": ACK_SHA}, "merged": False},
                {"head": {"sha": ACK_SHA}, "merged": True, "merge_commit_sha": MERGE_SHA},
            ),
            f"repos/{FORK}/pulls/8/files?per_page=100": [{"filename": ACKS_PATH}],
            f"repos/{FORK}/actions/runs?head_sha={ACK_SHA}&per_page=100": Then(
                {"workflow_runs": [{"id": 1, "status": "in_progress"}]},
                {"workflow_runs": [{"id": 1, "status": "completed"}]},
            ),
            f"repos/{FORK}/actions/runs/1/jobs": {
                "jobs": [
                    {"name": "build and test", "conclusion": "success"},
                    {"name": "security review", "conclusion": "failure"},
                ]
            },
            f"repos/{FORK}/issues/8/comments?per_page=100": [_comment(ACK_SHA, [GATE_FLAG])],
            **api,
        }
        self.calls: list[list[str]] = []
        self.committed: str | None = None

    def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["git", "clone"]:
            acks = Path(argv[-1]) / ACKS_PATH
            acks.parent.mkdir(parents=True)
            acks.write_text(OLD_ACK)
        if argv[:2] == ["git", "commit"] and cwd is not None:
            self.committed = (cwd / ACKS_PATH).read_text()
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return f"{ACK_SHA}\n"
        if argv[:3] == ["gh", "pr", "create"]:
            return f"https://github.com/{FORK}/pull/8\n"
        if argv[:2] == ["gh", "api"]:
            if argv[-1] not in self.api:
                raise CommandFailed(argv, 1, "gh: Not Found (HTTP 404)")
            answer = self.api[argv[-1]]
            return json.dumps(answer.next() if isinstance(answer, Then) else answer)
        return ""

    def writes(self) -> list[str]:
        """Every call that is not a read."""
        return [" ".join(a) for a in self.calls if a[:2] != ["gh", "api"] or "-X" in a]

    def ran(self, prefix: str) -> bool:
        return any(" ".join(argv).startswith(prefix) for argv in self.calls)


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A clock that moves only when the command sleeps, and fails a wait that outlives twice the
    deadline instead of hanging the suite."""
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        if sum(slept) > 2 * acknowledge.DEADLINE_SECONDS:
            raise AssertionError("waited past the deadline")
        slept.append(seconds)

    monkeypatch.setattr(acknowledge, "sleep", sleep)
    monkeypatch.setattr(acknowledge, "monotonic", lambda: sum(slept))
    return slept


def _ack(monkeypatch: pytest.MonkeyPatch, fork: FakeFork, *extra: str) -> Any:
    monkeypatch.setattr(cli, "run_command", fork)
    args = ["ack", FORK, "7", "--reason", "Read in session."]
    return CliRunner().invoke(cli.app, [*args, *extra])


# ---- the comment, read back -----------------------------------------------------------------


def test_the_parser_reads_back_exactly_the_failing_rows_the_report_renders() -> None:
    """Held to the renderer: every row it marks failing, including one acknowledged below its
    severity, and none it marks reported or acknowledged, with pipes and backticks in the text."""
    under = {LOW.fingerprint(): Ack("d", "x", "r", "low")}
    graver = Finding("ai", "src/b.js", "worse", "blob-b", "medium")
    body = Report(SYNC_SHA, [*SYNC_FINDINGS, graver]).markdown({**MAIN_ACKS, **under})
    review = acknowledge.parse_review(body)
    assert review.head == SYNC_SHA[:12] and not review.unavailable
    assert [(r.kind, r.severity, r.path, r.fingerprint) for r in review.failing] == [
        ("ai", "medium", "src/a.js", MEDIUM.fingerprint()),
        ("ai", "high", "src/a.js", HIGH.fingerprint()),
        ("audit", "high", "package-lock.json", AUDIT.fingerprint()),
        ("ai", "medium", "src/b.js", graver.fingerprint()),
    ]
    assert review.failing[0].text == "reads '~/.ssh' | maybe"


def test_a_review_that_could_not_run_is_read_as_such() -> None:
    body = Report(SYNC_SHA, unavailable="API error").markdown({})
    assert acknowledge.parse_review(body).unavailable


# ---- the dry run ----------------------------------------------------------------------------


def test_the_dry_run_lists_exactly_the_failing_rows_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fork = FakeFork()
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 0, result.output
    assert fork.writes() == []
    rows = [line for line in result.output.splitlines() if line.startswith("  ")]
    assert rows == [
        f"  ai (medium) | src/a.js | reads '~/.ssh' | maybe | {MEDIUM.fingerprint()}",
        f"  ai (high) | src/a.js | posts it to a new host. | {HIGH.fingerprint()}",
        f"  audit (high) | package-lock.json | {AUDIT.summary} | {AUDIT.fingerprint()}",
    ]  # the reported row and the acknowledged one are not listed
    assert result.output.count("[[ack]]") == 2
    assert f"--yes --expect {EXPECT}" in result.output


def test_a_planted_review_comment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anyone can comment on a public fork. A comment carrying the marker and the right header
    from any author but the workflow's bot is not the review. Control: the preceding test."""
    planted = {
        "user": {"login": "someone", "type": "User"},
        "body": Report(SYNC_SHA, [Finding("binary", "evil", "harmless", "z")]).markdown({}),
    }
    comments = [_comment(SYNC_SHA, SYNC_FINDINGS, MAIN_ACKS), planted]
    fork = FakeFork(**{f"repos/{FORK}/issues/7/comments?per_page=100": comments})
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 0, result.output
    assert "evil" not in result.output and f"--expect {EXPECT}" in result.output


@pytest.mark.parametrize(
    ("pull", "refusal"),
    [
        (_sync_pull(head={"ref": "upstream-sync", "sha": "6" * 40, "repo": {"full_name": FORK}}),
         "stale"),
        (_sync_pull(head={"ref": "feature", "sha": SYNC_SHA, "repo": {"full_name": FORK}}),
         "not AlobarQuest/rea's upstream-sync"),
        (_sync_pull(head={"ref": "upstream-sync", "sha": SYNC_SHA, "repo": {"full_name": "x/y"}}),
         "not AlobarQuest/rea's upstream-sync"),
        (_sync_pull(state="closed"), "closed, not open"),
    ],
    ids=["stale-report", "not-upstream-sync", "from-another-repository", "closed"],
)  # fmt: skip
@pytest.mark.parametrize("yes", [False, True])
def test_a_pull_request_that_is_not_the_current_held_sync_is_refused(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], pull: Any, refusal: str, yes: bool
) -> None:
    fork = FakeFork(**{f"repos/{FORK}/pulls/7": pull})
    extra = ["--yes", "--expect", EXPECT] if yes else []
    result = _ack(monkeypatch, fork, *extra)
    assert result.exit_code == 1
    assert refusal in result.output
    assert fork.writes() == []


def test_a_review_that_could_not_run_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    comment = {"user": BOT, "body": Report(SYNC_SHA, unavailable="API error").markdown({})}
    fork = FakeFork(**{f"repos/{FORK}/issues/7/comments?per_page=100": [comment]})
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 1 and "re-run" in result.output


# ---- --yes ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expect",
    [
        MEDIUM.fingerprint(),  # a failing row the person was not shown
        f"{EXPECT},{BINARY.fingerprint()}",  # one shown that no longer fails
        "",
    ],
)
def test_yes_refuses_unless_the_failing_set_is_the_one_shown(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], expect: str
) -> None:
    fork = FakeFork()
    result = _ack(monkeypatch, fork, "--yes", "--expect", expect)
    assert result.exit_code == 1
    assert "run without --yes and read them again" in result.output
    assert fork.writes() == []


def test_yes_records_merges_and_re_runs_the_sync(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    fork = FakeFork()
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 0, result.output
    assert fork.committed is not None and fork.committed.startswith(OLD_ACK)
    acks = parse_acks(fork.committed)
    assert set(acks) == {BINARY.fingerprint(), MEDIUM.fingerprint(), AUDIT.fingerprint()}
    model, audit = acks[MEDIUM.fingerprint()], acks[AUDIT.fingerprint()]
    # Two concerns about one file share a fingerprint: one entry, at the higher severity.
    assert (model.severity, audit.severity) == ("high", None)
    assert model.by == audit.by == "devon (approved in session; recorded by Claude)"
    assert model.date == audit.date == date.today().isoformat()
    assert model.reason == (
        f"{FORK} {TITLE} ai src/a.js: reads '~/.ssh' | maybe; posts it to a new host. "
        "Read in session."
    )
    assert (
        audit.reason == f"{FORK} {TITLE} audit package-lock.json: {AUDIT.summary}. Read in session."
    )
    [block] = [b for b in fork.committed.split("[[ack]]") if AUDIT.fingerprint() in b]
    assert "severity" not in block
    assert fork.ran("git push --quiet origin fork-sync-ack-7-555555555555")
    writes = fork.writes()
    assert writes[-2:] == [
        f"gh pr merge 8 --repo {FORK} --merge --admin --match-head-commit {ACK_SHA}",
        f"gh workflow run upstream-sync.yml --repo {FORK}",
    ]
    assert waits == [acknowledge.POLL_SECONDS]  # one pause: the first poll found a run in flight
    assert MERGE_SHA in result.output and "dispatched upstream-sync.yml" in result.output


def test_yes_refuses_a_fingerprint_already_acknowledged(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """An `ai` row acknowledged below its severity fails with a fingerprint already in the file;
    a second entry would replace the first, so that one is edited by hand."""
    under = {MEDIUM.fingerprint(): Ack("devon", "2026-10-20", "read", "low")}
    comment = _comment(SYNC_SHA, SYNC_FINDINGS, {**MAIN_ACKS, **under})

    class Acknowledged(FakeFork):
        def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
            out = super().__call__(argv, stdin, cwd)
            if list(argv[:2]) == ["git", "clone"]:
                (Path(argv[-1]) / ACKS_PATH).write_text(
                    OLD_ACK
                    + OLD_ACK.replace(BINARY.fingerprint(), MEDIUM.fingerprint())
                    + 'severity = "low"\n'
                )
            return out

    fork = Acknowledged(**{f"repos/{FORK}/issues/7/comments?per_page=100": [comment]})
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1 and "already acknowledged" in result.output
    assert [w for w in fork.writes() if not w.startswith("git clone")] == []


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (
            {f"repos/{FORK}/pulls/8/files?per_page=100": [
                {"filename": ACKS_PATH}, {"filename": ".github/workflows/ci.yml"},
            ]},
            "not only .github/fork-sync-acks.toml",
        ),
        (
            {f"repos/{FORK}/issues/8/comments?per_page=100": [
                _comment(ACK_SHA, [GATE_FLAG, Finding("ai", "src/c.js", "x", "b", "high")]),
            ]},
            "its review fails on ['src/c.js']",
        ),
        (
            {f"repos/{FORK}/actions/runs/1/jobs": {"jobs": [
                {"name": "build and test", "conclusion": "failure"},
                {"name": "security review", "conclusion": "failure"},
            ]}},
            "`build and test` concluded failure",
        ),
        (
            {f"repos/{FORK}/issues/8/comments?per_page=100": [_comment("b" * 40, [GATE_FLAG])]},
            "not a completed review of its head",
        ),
        (
            {f"repos/{FORK}/pulls/8": {"head": {"sha": "c" * 40}}},
            "no longer the commit",
        ),
    ],
    ids=["another-file", "failing-elsewhere", "build-failed", "stale-own-review", "head-moved"],
)  # fmt: skip
def test_the_acknowledgement_pull_request_is_merged_only_when_it_flags_only_itself(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], change: dict[str, Any], refusal: str
) -> None:
    fork = FakeFork(**change)
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1
    assert "#8 is open and unmerged" in result.output and refusal in result.output
    assert not fork.ran("gh pr merge") and not fork.ran("gh workflow run")


def test_checks_that_never_finish_stop_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    runs = {"workflow_runs": [{"id": 1, "status": "in_progress"}]}
    fork = FakeFork(**{f"repos/{FORK}/actions/runs?head_sha={ACK_SHA}&per_page=100": runs})
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1 and "did not finish in time" in result.output
    assert sum(waits) >= acknowledge.DEADLINE_SECONDS
    assert all(pause == acknowledge.POLL_SECONDS for pause in waits)
    assert not fork.ran("gh pr merge")
