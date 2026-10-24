"""`fork-sync watch`, the stuck-lane alarm, against a fake `gh` keyed by API path.

Every class and reason is shown, each threshold from both sides, and the three silent holds of
2026-10-21/22 are replayed as fixtures: each must come out `stuck` (or `unreadable`), which is the
whole reason the watcher exists.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fork_sync import cli, fork_sync_review, watch
from fork_sync.fork_sync_review import COMMENT_MARKER
from fork_sync.gh import CommandFailed

FORK = "AlobarQuest/rea"
UPSTREAM = "morluto/rea"
SHA = "a" * 40
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CONFIG = f"""upstream = "{UPSTREAM}"
ecosystem = "npm"
release_tag_pattern = '^rea-agents-(\\d+\\.\\d+\\.\\d+)$'
"""
RATE_LIMITED = CommandFailed(
    ["gh", "api"], 1, "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
)


def ago(**delta: float) -> str:
    return (NOW - timedelta(**delta)).isoformat().replace("+00:00", "Z")


def registry(*repos: str) -> str:
    consumers = "".join(
        f'\n  [[credential.consumer]]\n  kind = "gh-actions-secret"\n  repo = "{repo}"\n'
        '  name = "APP_PRIVATE_KEY"\n'
        for repo in repos
    )
    return (
        '[[credential]]\nid = "fork-sync-app-private-key"\n\n'
        '  [[credential.consumer]]\n  kind = "bws-secret"\n  uuid = "x"\n'
        f"{consumers}\n"
        '[[credential]]\nid = "anthropic-api-key"\n\n'
        '  [[credential.consumer]]\n  kind = "gh-actions-secret"\n'
        '  repo = "AlobarQuest/not-a-fork"\n  name = "ANTHROPIC_API_KEY"\n'
    )


def review_comment(*, unavailable: bool = False, head: str = SHA) -> dict[str, Any]:
    report = fork_sync_review.Report(head)
    if unavailable:
        report.unavailable = "API error"
    else:
        report.findings.append(
            fork_sync_review.Finding("watched", "install.sh", "changed", "b" * 40)
        )
    return {
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "body": report.markdown({}),
    }


class FakeGh:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        argv = list(argv)
        self.calls.append(argv)
        assert argv[:2] == ["gh", "api"] and "-X" not in argv, f"a write: {argv}"
        answer = self.answers.get(argv[-1], NOT_FOUND)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


NOT_FOUND = CommandFailed(["gh", "api"], 1, "gh: Not Found (HTTP 404)")

REGISTRY = "repos/AlobarQuest/factory-runner/contents/.cred-consumers.toml?ref=main"
DECLARATION = f"repos/{FORK}/contents/.github/fork-sync.toml?ref=main"
SYNC_RUNS = f"repos/{FORK}/actions/workflows/upstream-sync.yml/runs?per_page=1"
PULLS = f"repos/{FORK}/pulls?state=open&head=AlobarQuest:upstream-sync"
MANIFEST = f"repos/{FORK}/contents/package.json?ref=main"
RELEASES = f"repos/{UPSTREAM}/releases?per_page=100"
COMMIT = f"repos/{FORK}/commits/{SHA}"
CI_RUNS = f"repos/{FORK}/actions/runs?head_sha={SHA}&per_page=100"
JOBS = f"repos/{FORK}/actions/runs/55/jobs"
ISSUES = f"repos/{FORK}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100"
COMMENTS = f"repos/{FORK}/issues/7/comments?per_page=100"


def lane(
    *,
    sync: dict[str, Any] | None = None,
    pull: bool = False,
    pushed: str | None = None,
    opened: str | None = None,
    ci: str | None = "completed",
    build: str = "success",
    review: str = "success",
    checks_done: str | None = None,
    issue: bool = False,
    comment: dict[str, Any] | None = None,
    main_version: str = "1.2.0",
    releases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One fork on the lane: by default current with upstream, its daily sync green."""
    answers: dict[str, Any] = {
        REGISTRY: registry(FORK),
        DECLARATION: CONFIG,
        SYNC_RUNS: {
            "workflow_runs": [
                sync if sync is not None else {"created_at": ago(hours=6), "conclusion": "success"}
            ]
        },
        PULLS: [],
        MANIFEST: json.dumps({"version": main_version}),
        RELEASES: releases
        if releases is not None
        else [{"tag_name": "rea-agents-1.2.0", "published_at": ago(days=9)}],
    }
    if pull:
        answers[PULLS] = [
            {"number": 7, "head": {"sha": SHA}, "created_at": opened or ago(hours=30)}
        ]
        answers[COMMIT] = {"commit": {"committer": {"date": pushed or ago(hours=30)}}}
        runs = []
        if ci is not None:
            runs = [
                {
                    "id": 54,
                    "path": ".github/workflows/upstream-sync.yml",
                    "created_at": ago(hours=1),
                },
                {
                    "id": 55,
                    "path": ".github/workflows/ci.yml",
                    "status": ci,
                    "created_at": ago(hours=29),
                },
            ]
        answers[CI_RUNS] = {"workflow_runs": runs}
        done = checks_done or ago(hours=1)
        answers[JOBS] = {
            "jobs": [
                {"name": "build and test", "conclusion": build, "completed_at": done},
                {"name": "security review", "conclusion": review, "completed_at": done},
                {"name": "notify", "conclusion": "success", "completed_at": done},
            ]
        }
        issues = [{"number": 3, "pull_request": {}, "body": watch.ALERT_MARKER}]
        if issue:
            issues.append(
                {
                    "number": 9,
                    "body": f"{watch.ALERT_MARKER}\n@AlobarQuest held",
                    "updated_at": ago(hours=1),
                }
            )
        answers[ISSUES] = issues
        answers[COMMENTS] = [comment] if comment is not None else []
    return answers


def verdict(answers: dict[str, Any]) -> watch.Verdict:
    result = watch.watch(FakeGh(answers), NOW)
    assert result.unreadable is None, result.unreadable
    [only] = result.verdicts
    return only


# ---- the three incidents ---------------------------------------------------------------------


def test_incident_1_a_conflicted_sync_pull_request_with_no_checks_is_stuck() -> None:
    """GitHub runs no `pull_request` workflow on a conflicted pull request, so no check reports."""
    found = verdict(lane(pull=True, ci=None, pushed=ago(minutes=61), opened=ago(minutes=61)))
    assert (found.status, found.reasons) == (watch.STUCK, (watch.NO_CHECKS,))


def test_incident_2_a_stale_bearer_holding_a_green_sync_is_stuck() -> None:
    """Both checks green, open seven hours: the inert landing lane held it unreadable."""
    found = verdict(lane(pull=True, checks_done=ago(hours=7)))
    assert (found.status, found.reasons) == (watch.STUCK, (watch.NOT_LANDING,))


def test_incident_3_an_exhausted_rate_limit_is_unreadable() -> None:
    answers = lane(pull=True)
    answers[SYNC_RUNS] = RATE_LIMITED
    result = watch.watch(FakeGh(answers), NOW)
    assert result.verdicts == []
    assert result.unreadable is not None and "HTTP 403" in result.unreadable
    assert result.exit_code() == watch.EXIT_UNREADABLE


def test_a_rate_limited_registry_read_is_unreadable_and_reads_nothing_else() -> None:
    gh = FakeGh({REGISTRY: RATE_LIMITED})
    result = watch.watch(gh, NOW)
    assert result.unreadable is not None and result.exit_code() == watch.EXIT_UNREADABLE
    assert len(gh.calls) == 1 and result.calls == 1


def test_the_first_failed_read_ends_the_pass() -> None:
    """A rate-limited API is not read again for the forks after it."""
    answers = lane()
    answers[REGISTRY] = registry(FORK, "AlobarQuest/zzz")
    answers[DECLARATION] = RATE_LIMITED
    gh = FakeGh(answers)
    result = watch.watch(gh, NOW)
    assert result.unreadable is not None and result.unreadable.startswith(FORK)
    assert not any("zzz" in argv[-1] for argv in gh.calls)


# ---- ok and waiting_on_person ----------------------------------------------------------------


def test_a_fork_current_with_upstream_is_ok() -> None:
    found = verdict(lane())
    assert (found.status, found.reasons) == (watch.OK, ())
    assert "current at 1.2.0" in found.detail


def test_a_held_review_with_its_alert_open_is_waiting_on_person_not_stuck() -> None:
    answers = lane(pull=True, review="failure", issue=True, comment=review_comment())
    gh = FakeGh(answers)
    [found] = watch.watch(gh, NOW).verdicts
    assert found.status == watch.WAITING_ON_PERSON
    assert "alert issue #9 is open" in found.detail
    # The alert already says what the review found, so its comment isn't read.
    assert not any(argv[-1] == COMMENTS for argv in gh.calls)


def test_a_review_that_could_not_run_with_its_alert_open_is_waiting_on_person() -> None:
    found = verdict(
        lane(pull=True, review="failure", issue=True, comment=review_comment(unavailable=True))
    )
    assert found.status == watch.WAITING_ON_PERSON


def test_an_alert_left_open_from_an_older_sync_does_not_count() -> None:
    """`notify` comments on its issue for each new head, so an issue untouched since this head
    arrived is an older sync's, and a cancelled review now is told to nobody."""
    answers = lane(pull=True, review="cancelled", issue=True, pushed=ago(minutes=30))
    found = verdict(answers)
    assert (found.status, found.reasons) == (watch.STUCK, (watch.REVIEW_UNAVAILABLE,))


def test_an_open_alert_does_not_hide_a_failing_sync() -> None:
    found = verdict(
        lane(
            pull=True,
            review="failure",
            issue=True,
            comment=review_comment(),
            sync={"created_at": ago(hours=2), "conclusion": "failure"},
        )
    )
    assert (found.status, found.reasons) == (watch.STUCK, (watch.SYNC_FAILING,))


# ---- each stuck reason, and each threshold from both sides -----------------------------------


@pytest.mark.parametrize(("minutes", "stuck"), [(59, False), (61, True)])
def test_no_checks_threshold(minutes: int, stuck: bool) -> None:
    found = verdict(lane(pull=True, ci=None, pushed=ago(minutes=minutes), opened=ago(days=2)))
    assert found.reasons == ((watch.NO_CHECKS,) if stuck else ())


def test_no_checks_counts_from_the_later_of_push_and_opening() -> None:
    found = verdict(lane(pull=True, ci=None, pushed=ago(days=2), opened=ago(minutes=30)))
    assert found.status == watch.OK


def test_a_ci_run_still_in_progress_past_the_threshold_is_no_checks() -> None:
    found = verdict(lane(pull=True, ci="in_progress", pushed=ago(minutes=90)))
    assert found.reasons == (watch.NO_CHECKS,)


def test_a_ci_run_in_progress_within_the_threshold_is_ok() -> None:
    found = verdict(lane(pull=True, ci="in_progress", pushed=ago(minutes=20)))
    assert found.status == watch.OK


@pytest.mark.parametrize(("hours", "stuck"), [(5.9, False), (6.1, True)])
def test_not_landing_threshold(hours: float, stuck: bool) -> None:
    found = verdict(lane(pull=True, checks_done=ago(hours=hours)))
    assert found.reasons == ((watch.NOT_LANDING,) if stuck else ())


def test_a_review_that_could_not_run_without_an_alert_is_stuck() -> None:
    found = verdict(lane(pull=True, review="failure", comment=review_comment(unavailable=True)))
    assert (found.status, found.reasons) == (watch.STUCK, (watch.REVIEW_UNAVAILABLE,))


def test_a_review_comment_of_an_older_head_means_the_review_could_not_run() -> None:
    found = verdict(lane(pull=True, review="failure", comment=review_comment(head="c" * 40)))
    assert found.reasons == (watch.REVIEW_UNAVAILABLE,)


def test_a_failed_review_with_no_comment_could_not_run() -> None:
    found = verdict(lane(pull=True, review="failure"))
    assert found.reasons == (watch.REVIEW_UNAVAILABLE,)


def test_a_cancelled_review_without_an_alert_is_review_unavailable() -> None:
    found = verdict(lane(pull=True, review="cancelled", comment=review_comment(unavailable=True)))
    assert found.reasons == (watch.REVIEW_UNAVAILABLE,)


def test_a_held_review_whose_alert_never_opened_is_stuck() -> None:
    """`notify` failed: findings hold the sync and nobody was told."""
    found = verdict(lane(pull=True, review="failure", comment=review_comment()))
    assert (found.status, found.reasons) == (watch.STUCK, (watch.HELD_WITHOUT_ALERT,))


def test_an_alert_on_a_pull_request_is_not_the_alert_issue() -> None:
    answers = lane(pull=True, review="failure", comment=review_comment())
    # Fresh and marked, so only its being a pull request keeps it from counting.
    answers[ISSUES] = [
        {"number": 3, "pull_request": {}, "body": watch.ALERT_MARKER, "updated_at": ago(hours=1)}
    ]
    assert verdict(answers).reasons == (watch.HELD_WITHOUT_ALERT,)


def test_a_failing_build_is_stuck_since_nothing_alerts_on_it() -> None:
    found = verdict(lane(pull=True, build="failure"))
    assert (found.status, found.reasons) == (watch.STUCK, (watch.BUILD_FAILING,))


def test_a_failing_build_is_stuck_even_beside_an_open_alert() -> None:
    found = verdict(
        lane(pull=True, build="failure", review="failure", issue=True, comment=review_comment())
    )
    assert (found.status, found.reasons) == (watch.STUCK, (watch.BUILD_FAILING,))


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_a_sync_run_that_failed_is_sync_failing(conclusion: str) -> None:
    found = verdict(lane(sync={"created_at": ago(hours=2), "conclusion": conclusion}))
    assert found.reasons == (watch.SYNC_FAILING,)


@pytest.mark.parametrize("conclusion", ["success", "cancelled", None])
def test_a_recent_sync_run_that_did_not_fail_is_not_sync_failing(conclusion: str | None) -> None:
    found = verdict(lane(sync={"created_at": ago(hours=2), "conclusion": conclusion}))
    assert found.status == watch.OK


@pytest.mark.parametrize(("hours", "stuck"), [(35, False), (37, True)])
def test_sync_silence_threshold(hours: int, stuck: bool) -> None:
    found = verdict(lane(sync={"created_at": ago(hours=hours), "conclusion": "success"}))
    assert found.reasons == ((watch.SYNC_FAILING,) if stuck else ())


def test_a_sync_that_never_ran_is_sync_failing() -> None:
    answers = lane()
    answers[SYNC_RUNS] = {"workflow_runs": []}
    assert verdict(answers).reasons == (watch.SYNC_FAILING,)


@pytest.mark.parametrize(("hours", "stuck"), [(35, False), (37, True)])
def test_not_syncing_threshold(hours: int, stuck: bool) -> None:
    releases = [
        {"tag_name": "rea-agents-1.3.0", "published_at": ago(hours=hours)},
        {"tag_name": "rea-agents-1.2.0", "published_at": ago(days=9)},
    ]
    found = verdict(lane(releases=releases))
    assert found.reasons == ((watch.NOT_SYNCING,) if stuck else ())


def test_not_syncing_reuses_the_review_scripts_release_selection() -> None:
    """A prerelease, a tag the pattern doesn't match and an older version are never synced to,
    so none of them is a release the fork is behind."""
    releases = [
        {"tag_name": "rea-agents-9.0.0", "prerelease": True, "published_at": ago(days=5)},
        {"tag_name": "other-9.0.0", "published_at": ago(days=5)},
        {"tag_name": "rea-agents-1.1.0", "published_at": ago(days=5)},
    ]
    assert verdict(lane(releases=releases)).status == watch.OK


def test_an_open_sync_pull_request_is_never_not_syncing() -> None:
    releases = [{"tag_name": "rea-agents-1.3.0", "published_at": ago(days=5)}]
    assert verdict(lane(pull=True, releases=releases)).status == watch.OK


def test_every_reason_is_reported_together() -> None:
    found = verdict(
        lane(
            pull=True,
            ci=None,
            pushed=ago(hours=3),
            sync={"created_at": ago(hours=40), "conclusion": "success"},
        )
    )
    assert found.reasons == (watch.SYNC_FAILING, watch.NO_CHECKS)


# ---- discovery, output, the read bound --------------------------------------------------------


def test_forks_are_the_registered_app_key_copies() -> None:
    text = registry("AlobarQuest/rtk", "alobarquest/RTK", "AlobarQuest/claude-octopus")
    gh = FakeGh({REGISTRY: text})
    assert watch.forks(gh) == ["AlobarQuest/claude-octopus", "AlobarQuest/rtk"]


def test_a_registered_repository_without_a_declaration_is_not_on_the_lane() -> None:
    answers = lane()
    del answers[DECLARATION]
    answers[f"repos/{FORK}"] = {"full_name": FORK}
    found = verdict(answers)
    assert found.status == watch.NOT_ON_LANE
    assert watch.watch(FakeGh(answers), NOW).exit_code() == watch.EXIT_OK


def test_a_registered_repository_that_is_not_visible_is_unreadable() -> None:
    """Renamed, deleted or out of reach: every path 404s, and that is not mid-onboarding."""
    answers = lane()
    del answers[DECLARATION]
    found = verdict(answers)
    assert found.status == watch.UNREADABLE
    assert watch.watch(FakeGh(answers), NOW).exit_code() == watch.EXIT_UNREADABLE


@pytest.mark.parametrize(
    ("path", "served"),
    [(DECLARATION, 'upstream = "x/y"\n'), (MANIFEST, "{not json"), (MANIFEST, "{}")],
)
def test_content_one_fork_serves_badly_makes_that_fork_unreadable_and_the_pass_goes_on(
    path: str, served: str
) -> None:
    answers = lane()
    other = "AlobarQuest/zzz"
    answers[REGISTRY] = registry(FORK, other)
    for key, value in list(answers.items()):
        if key.startswith(f"repos/{FORK}/"):
            answers[key.replace(FORK, other)] = value
    answers[path] = served
    result = watch.watch(FakeGh(answers), NOW)
    assert result.unreadable is None
    assert [(v.fork, v.status) for v in result.verdicts] == [
        (FORK, watch.UNREADABLE),
        (other, watch.OK),
    ]
    assert result.exit_code() == watch.EXIT_UNREADABLE


def test_stuck_outranks_unreadable_in_the_exit_code() -> None:
    answers = lane(pull=True, checks_done=ago(hours=7))
    answers[REGISTRY] = registry(FORK, "AlobarQuest/zzz")
    answers["repos/AlobarQuest/zzz/contents/.github/fork-sync.toml?ref=main"] = RATE_LIMITED
    result = watch.watch(FakeGh(answers), NOW)
    assert result.unreadable is not None
    assert result.exit_code() == watch.EXIT_STUCK


def test_a_consumer_with_no_repository_is_skipped() -> None:
    blank = (
        '  [[credential.consumer]]\n  kind = "gh-actions-secret"\n  name = "APP_PRIVATE_KEY"\n\n'
    )
    text = registry("AlobarQuest/rtk").replace(
        '[[credential]]\nid = "anthropic-api-key"',
        blank + '[[credential]]\nid = "anthropic-api-key"',
    )
    assert watch.forks(FakeGh({REGISTRY: text})) == ["AlobarQuest/rtk"]


def test_the_alert_marker_is_the_one_notify_writes() -> None:
    env = {
        "GITHUB_REPOSITORY_OWNER": "o",
        "RUN_URL": "u",
        "HEAD_SHA": SHA,
        "GITHUB_SERVER_URL": "s",
        "GITHUB_REPOSITORY": FORK,
        "PR_NUMBER": "7",
    }
    assert fork_sync_review.alert_body("review", env).startswith(watch.ALERT_MARKER)
    assert COMMENT_MARKER in review_comment()["body"]


@pytest.mark.parametrize(
    "answers",
    [
        lane(),
        lane(pull=True),
        lane(pull=True, review="failure", issue=True, comment=review_comment()),
        lane(pull=True, ci=None, pushed=ago(hours=2)),
    ],
)
def test_a_pass_reads_a_bounded_number_of_times_and_writes_nothing(answers: dict) -> None:
    gh = FakeGh(answers)
    result = watch.watch(gh, NOW)
    assert result.calls == len(gh.calls) <= 1 + watch.READS_PER_FORK
    assert all("-X" not in argv for argv in gh.calls)


def test_stuck_exits_3_and_ok_exits_0() -> None:
    assert watch.watch(FakeGh(lane()), NOW).exit_code() == watch.EXIT_OK
    stuck = watch.watch(FakeGh(lane(pull=True, checks_done=ago(hours=7))), NOW)
    assert stuck.exit_code() == watch.EXIT_STUCK


# ---- through the real entrypoint -------------------------------------------------------------


def _cli(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> Any:
    monkeypatch.setattr(cli, "run_command", FakeGh(answers))
    monkeypatch.setattr(watch, "clock", lambda: NOW)
    return CliRunner().invoke(cli.app, ["watch"])


def test_cli_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(monkeypatch, lane())
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith(f"ok                {FORK}")
    assert "1 fork(s), 0 stuck, 6 GitHub API call(s)" in result.output


def test_cli_stuck(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(monkeypatch, lane(pull=True, checks_done=ago(hours=7)))
    assert result.exit_code == 3
    assert f"stuck             {FORK} not_landing -- #7: both checks passed" in result.output


def test_cli_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _cli(monkeypatch, {REGISTRY: RATE_LIMITED})
    assert result.exit_code == 2
    assert result.output.startswith("unreadable")
    assert "1 GitHub API call(s)" in result.output
