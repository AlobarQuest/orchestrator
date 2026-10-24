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
BY = "devon (approved in session; recorded by Claude)"
CI = ".github/workflows/ci.yml"
MAIN_ACKS_PATH = f"repos/{FORK}/contents/{ACKS_PATH}?ref=main"
OPEN_ACK_PULLS = f"repos/{FORK}/pulls?state=open&head=AlobarQuest:fork-sync-ack-7-555555555555"


def _run(id_: int, status: str = "completed", path: str = CI, at: str = "2026-10-24T10:00:00Z"):
    return {"id": id_, "status": status, "path": path, "created_at": at}


def _job(name: str, conclusion: str | None, status: str = "completed") -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion}


GREEN = {"jobs": [_job("build and test", "success"), _job("security review", "failure")]}


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
        """`api` answers paths; `main_acks`, if given, is main's acknowledgements file."""
        self.main_acks: str = api.pop("main_acks", OLD_ACK)
        self.api: dict[str, Any] = {
            MAIN_ACKS_PATH: self.main_acks,
            OPEN_ACK_PULLS: [],
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
                {"workflow_runs": [_run(1, "in_progress")]},
                {"workflow_runs": [_run(1)]},
            ),
            f"repos/{FORK}/actions/runs/1/jobs": GREEN,
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
            acks.write_text(self.main_acks)
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
            answer = answer.next() if isinstance(answer, Then) else answer
            return answer if isinstance(answer, str) else json.dumps(answer)
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
    args = ["ack", FORK, "7", "--reason", "Read in session.", "--by", BY]
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
        (
            _sync_pull(head={"ref": "upstream-sync", "sha": "6" * 40, "repo": {"full_name": FORK}}),
            "stale",
        ),
        (
            _sync_pull(head={"ref": "feature", "sha": SYNC_SHA, "repo": {"full_name": FORK}}),
            "not AlobarQuest/rea's upstream-sync",
        ),
        (
            _sync_pull(
                head={"ref": "upstream-sync", "sha": SYNC_SHA, "repo": {"full_name": "x/y"}}
            ),
            "not AlobarQuest/rea's upstream-sync",
        ),
        (_sync_pull(state="closed"), "closed, not open"),
    ],
    ids=["stale-report", "not-upstream-sync", "from-another-repository", "closed"],
)
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
    assert model.by == audit.by == BY
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


def test_by_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attribution is always stated by the caller; nothing defaults to a person."""
    fork = FakeFork()
    monkeypatch.setattr(cli, "run_command", fork)
    result = CliRunner().invoke(cli.app, ["ack", FORK, "7", "--reason", "r"])
    assert result.exit_code == 2 and fork.calls == []


def test_control_characters_are_shown_escaped_and_hide_no_row(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """A finding's text, path or the pull request's title is upstream's or the model's: an ANSI
    cursor-up and erase-line sequence in it would otherwise overwrite the row above it."""
    hiding = Finding("ai", "src/\x1b[2Kc.js", "fine\x1b[1A\x1b[2K\u202e", "blob-c", "medium")
    comment = _comment(SYNC_SHA, [*SYNC_FINDINGS, hiding], MAIN_ACKS)
    fork = FakeFork(
        **{
            f"repos/{FORK}/issues/7/comments?per_page=100": [comment],
            f"repos/{FORK}/pulls/7": _sync_pull(title="sync\x9b2J v2"),
        }
    )
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 0, result.output
    assert not any(c in result.output for c in "\x1b\x9b\u202e")
    assert "src/\\x1b[2Kc.js | fine\\x1b[1A\\x1b[2K\\u202e" in result.output
    assert "sync\\x9b2J v2" in result.output
    rows = [line for line in result.output.splitlines() if line.startswith("  ")]
    assert len(rows) == 4  # medium, high and the audit row, then the hiding one, all visible
    fingerprints = f"{MEDIUM.fingerprint()},{AUDIT.fingerprint()},{hiding.fingerprint()}"
    result = _ack(monkeypatch, fork, "--yes", "--expect", fingerprints)
    assert result.exit_code == 0, result.output
    assert fork.committed is not None and "\x1b" not in fork.committed
    written = parse_acks(fork.committed)[hiding.fingerprint()].reason
    assert "\\x1b[1A" in written and "sync\\x9b2J v2" in written


LOW_ACK = (
    f'\n[[ack]]\nfingerprint = "{MEDIUM.fingerprint()}"\nby = "devon"\ndate = 2026-10-20\n'
    'severity = "low"\nreason = "read once"\n'
)


def _under(ceiling: str, rows: list[Finding]) -> FakeFork:
    """Main acknowledges MEDIUM/HIGH's content at `ceiling`; the review rates `rows`."""
    acks = {**MAIN_ACKS, MEDIUM.fingerprint(): Ack("devon", "2026-10-20", "read once", ceiling)}
    comment = _comment(SYNC_SHA, [*rows, AUDIT, BINARY], acks)
    return FakeFork(
        main_acks=OLD_ACK + LOW_ACK.replace('"low"', f'"{ceiling}"'),
        **{f"repos/{FORK}/issues/7/comments?per_page=100": [comment]},
    )


def test_a_model_acknowledgement_below_a_new_rating_is_raised_in_place(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    fork = _under("low", [MEDIUM, HIGH])
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 0, result.output
    # The raise is listed apart from the new entry, and --expect covers both.
    added, raised = result.output.split("Would add to")[1].split("Would raise in")
    assert AUDIT.fingerprint() in added and MEDIUM.fingerprint() not in added
    assert f"{MEDIUM.fingerprint()}: severity -> high; raised to high on" in raised
    assert f"--expect {AUDIT.fingerprint()},{MEDIUM.fingerprint()}" in result.output
    result = _ack(
        monkeypatch, fork, "--yes", "--expect", f"{AUDIT.fingerprint()},{MEDIUM.fingerprint()}"
    )
    assert result.exit_code == 0, result.output
    assert fork.committed is not None
    acks = parse_acks(fork.committed)
    raised_ack = acks[MEDIUM.fingerprint()]
    assert (raised_ack.severity, raised_ack.by, raised_ack.date) == ("high", "devon", "2026-10-20")
    assert raised_ack.reason == f"read once; raised to high on {date.today()} ({TITLE})"
    assert fork.committed.count(MEDIUM.fingerprint()) == 1
    assert acks[BINARY.fingerprint()] == MAIN_ACKS[BINARY.fingerprint()]
    assert AUDIT.fingerprint() in acks


@pytest.mark.parametrize(
    ("ceiling", "rows"),
    [
        # Main already accepts `high`, and a stale comment still shows the row failing at medium.
        ("high", [MEDIUM]),
        ("medium", [MEDIUM]),
    ],
    ids=["downward", "level"],
)
def test_a_raise_only_ever_raises(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], ceiling: str, rows: list[Finding]
) -> None:
    fork = _under(ceiling, rows)
    # The comment renders the row as covered at this ceiling; show it failing, as a stale one would.
    stale = Report(SYNC_SHA, [*rows, AUDIT, BINARY]).markdown(MAIN_ACKS)
    fork.api[f"repos/{FORK}/issues/7/comments?per_page=100"] = [{"user": BOT, "body": stale}]
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 1 and "already acknowledged" in result.output
    assert fork.writes() == []


def test_any_other_fingerprint_already_acknowledged_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Main acknowledges the audit row while the comment still shows it failing: not a model
    rating to raise, so it is a person's to edit."""
    audit_ack = OLD_ACK.replace(BINARY.fingerprint(), AUDIT.fingerprint())
    fork = FakeFork(main_acks=OLD_ACK + "\n" + audit_ack)
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 1 and "already acknowledged" in result.output
    assert fork.writes() == []


def test_a_severity_on_another_kind_s_acknowledgement_raises_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`severity` is ignored on every kind but `ai`, so an audit acknowledgement carrying one is
    still a person's to edit, not a ceiling to raise."""
    audit_ack = OLD_ACK.replace(BINARY.fingerprint(), AUDIT.fingerprint()) + 'severity = "low"\n'
    fork = FakeFork(main_acks=OLD_ACK + "\n" + audit_ack)
    result = _ack(monkeypatch, fork)
    assert result.exit_code == 1 and "already acknowledged (severity low)" in result.output
    assert fork.writes() == []


def test_an_open_pull_request_from_an_earlier_run_is_refused(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """A refusal leaves its pull request open; a second run names it rather than failing at
    `git push`, and deletes nothing."""
    fork = FakeFork(**{OPEN_ACK_PULLS: [{"number": 8}]})
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1
    assert "#8 from fork-sync-ack-7-555555555555 is still open" in result.output
    assert "merge or close it" in result.output
    assert fork.writes() == []


def test_main_s_acknowledgements_moving_after_they_were_read_is_refused(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    fork = FakeFork()
    fork.main_acks = OLD_ACK + "\n# edited meanwhile\n"  # what the clone sees
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1 and "changed since it was read" in result.output
    assert [w for w in fork.writes() if not w.startswith("git clone")] == []


RUNS = f"repos/{FORK}/actions/runs?head_sha={ACK_SHA}&per_page=100"


@pytest.mark.parametrize(
    ("runs", "jobs", "refusal"),
    [
        (
            [_run(1), _run(2, at="2026-10-24T11:00:00Z")],
            {1: GREEN, 2: {"jobs": [_job("build and test", "failure")]}},
            "`build and test` concluded failure",
        ),
        (
            # Another workflow's job of the same name does not stand in for the CI run's.
            [_run(1), _run(3, path=".github/workflows/other.yml", at="2026-10-24T12:00:00Z")],
            {
                1: {
                    "jobs": [_job("build and test", "failure"), _job("security review", "failure")]
                },
                3: GREEN,
            },
            "`build and test` concluded failure",
        ),
        (
            [_run(1)],
            {
                1: {
                    "jobs": [
                        _job("build and test", "success"),
                        _job("security review", "cancelled"),
                    ]
                }
            },
            "`security review` concluded cancelled",
        ),
        (
            [_run(1)],
            {1: {"jobs": [_job("build and test", "success")]}},
            "`security review` concluded nothing",
        ),
    ],
    ids=["latest-run-failed", "other-workflow-masks-nothing", "review-cancelled", "review-missing"],
)
def test_only_the_latest_ci_run_decides_and_its_review_must_have_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
    waits: list[float],
    runs: list[dict[str, Any]],
    jobs: dict[int, Any],
    refusal: str,
) -> None:
    api = {RUNS: {"workflow_runs": runs}}
    api |= {f"repos/{FORK}/actions/runs/{n}/jobs": answer for n, answer in jobs.items()}
    fork = FakeFork(**api)
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1
    assert "#8 is open and unmerged" in result.output and refusal in result.output
    assert not fork.ran("gh pr merge")


def test_a_successful_re_run_after_a_failed_run_is_the_one_that_counts(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """Control for the preceding test: with the runs the other way round, it merges."""
    failed = {"jobs": [_job("build and test", "failure"), _job("security review", "failure")]}
    fork = FakeFork(
        **{
            RUNS: {"workflow_runs": [_run(1), _run(2, at="2026-10-24T11:00:00Z")]},
            f"repos/{FORK}/actions/runs/1/jobs": failed,
            f"repos/{FORK}/actions/runs/2/jobs": GREEN,
        }
    )
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 0, result.output
    assert fork.ran("gh pr merge 8")


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (
            {
                f"repos/{FORK}/pulls/8/files?per_page=100": [
                    {"filename": ACKS_PATH},
                    {"filename": ".github/workflows/ci.yml"},
                ]
            },
            "not only .github/fork-sync-acks.toml",
        ),
        (
            {
                f"repos/{FORK}/issues/8/comments?per_page=100": [
                    _comment(ACK_SHA, [GATE_FLAG, Finding("ai", "src/c.js", "x", "b", "high")]),
                ]
            },
            "its review fails on ['src/c.js']",
        ),
        (
            {
                f"repos/{FORK}/actions/runs/1/jobs": {
                    "jobs": [_job("build and test", "failure"), _job("security review", "failure")]
                }
            },
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
)
def test_the_acknowledgement_pull_request_is_merged_only_when_it_flags_only_itself(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], change: dict[str, Any], refusal: str
) -> None:
    fork = FakeFork(**change)
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1
    assert "#8 is open and unmerged" in result.output and refusal in result.output
    assert not fork.ran("gh pr merge") and not fork.ran("gh workflow run")


@pytest.mark.parametrize(
    "runs",
    [
        {"workflow_runs": [_run(1, "in_progress")]},
        {"workflow_runs": [_run(4, path=".github/workflows/other.yml")]},
        {"workflow_runs": []},
    ],
    ids=["never-completes", "no-ci-run", "no-run"],
)
def test_checks_that_never_finish_stop_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, waits: list[float], runs: dict[str, Any]
) -> None:
    fork = FakeFork(**{RUNS: runs})
    result = _ack(monkeypatch, fork, "--yes", "--expect", EXPECT)
    assert result.exit_code == 1 and "completed in time" in result.output
    assert sum(waits) >= acknowledge.DEADLINE_SECONDS
    assert all(pause == acknowledge.POLL_SECONDS for pause in waits)
    assert not fork.ran("gh pr merge")
