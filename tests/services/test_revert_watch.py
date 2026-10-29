"""Judging a landed Dependabot bump by its own checks on the default branch (Landing Part 3a).

The judgment is pure over what the gateway reads, so every case here runs against a double that
answers `jobs_at` per commit. The review-focus cases this file pins: a job that fails once and
passes on its rerun never reads as bad, a failure the commit before the bump shares is never the
bump's, and a job the pull request never ran, a `pull_request`-only workflow or the update bot's
own runs are never watched.
"""

from __future__ import annotations

from orchestrator.persistence.models import EstatePrMerge, LandingRevert
from orchestrator.services.landing.interfaces import EstateGatewayError, Job

# `_judge` is private until Task 5 gives it a production caller (test_unreachable_guards).
from orchestrator.services.landing.revert_watch import WATCH_HOURS, Verdict
from orchestrator.services.landing.revert_watch import _judge as judge

REPOSITORY = "alobarquest/veritok"
HEAD = "1111111111111111111111111111111111111111"
MERGE = "2222222222222222222222222222222222222222"
PARENT = "3333333333333333333333333333333333333333"
RUN = 18_000_000_001  # above 2^31, as real run ids are
OTHER_RUN = RUN + 5


class _Gateway:
    """Answers `jobs_at` from a table keyed by commit; a listed broken commit raises."""

    def __init__(self, jobs: dict[str, tuple[Job, ...]], *, broken: frozenset[str] = frozenset()):
        self._jobs = jobs
        self._broken = broken
        self.asked: list[str] = []

    def jobs_at(self, *, repository: str, sha: str) -> tuple[Job, ...]:
        assert repository == REPOSITORY
        self.asked.append(sha)
        if sha in self._broken:
            raise EstateGatewayError("read_status", 502)
        return self._jobs.get(sha, ())

    def parent_of(self, *, repository: str, sha: str) -> str:
        raise AssertionError("judging reads the parent from the record")

    def rerun_failed_jobs(self, *, repository: str, run_id: int) -> None:
        raise AssertionError("judging never acts")


def _job(
    name: str,
    conclusion: str | None,
    *,
    run_id: int = RUN,
    attempt: int = 1,
    workflow: str = "CI",
    event: str = "push",
) -> Job:
    return Job(
        name=name,
        conclusion=conclusion,
        run_id=run_id,
        attempt=attempt,
        workflow=workflow,
        event=event,
    )


def _pr(name: str, conclusion: str | None = "success", *, workflow: str = "CI") -> Job:
    """A job at the pull request's head, from its `pull_request` run."""
    return _job(name, conclusion, run_id=7, workflow=workflow, event="pull_request")


def _landing(*, merge_commit_sha: str | None = MERGE) -> EstatePrMerge:
    return EstatePrMerge(
        repository=REPOSITORY,
        pr_number=42,
        head_sha=HEAD,
        status="merged",
        merge_commit_sha=merge_commit_sha,
    )


def _record(
    status: str = "rerun_requested", rerun_attempts: dict[str, int] | None = None
) -> LandingRevert:
    return LandingRevert(
        repository=REPOSITORY,
        pr_number=42,
        merge_commit_sha=MERGE,
        parent_sha=PARENT,
        failed_job_names=["test"],
        failed_run_id=RUN,
        rerun_attempts={str(RUN): 1} if rerun_attempts is None else rerun_attempts,
        status=status,
        bumps=[],
    )


GREEN_HEAD = (_pr("lint"), _pr("test"))
GREEN_PARENT = (_job("lint", "success", run_id=9), _job("test", "success", run_id=9))


def test_the_watch_window_is_a_plain_int_with_a_real_default() -> None:
    assert WATCH_HOURS == 48
    assert type(WATCH_HOURS) is int


def test_a_clean_merge_commit_is_clean() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "success"))}
    )

    verdict = judge(gateway, _landing(), None)

    assert verdict == Verdict(kind="clean", failed_job_names=(), run_id=None, detail=verdict.detail)


def test_a_running_job_is_pending() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", None))})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_running_job_outranks_a_failed_one() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("lint", None), _job("test", "failure"))})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_no_push_run_for_a_watched_workflow_yet_is_pending() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: ()})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_watched_job_absent_from_a_present_push_run_is_not_watched() -> None:
    """A job its workflow runs only for pull requests (an `if:` on the event) cannot hold the
    landing open: its workflow's push run is there, and the job is not in it."""
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"),)})

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_pull_request_only_workflow_is_never_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: (*GREEN_HEAD, _pr("label", workflow="Labeler")),
            MERGE: (
                _job("lint", "success"),
                _job("test", "success"),
                _job(
                    "label", "failure", run_id=OTHER_RUN, workflow="Labeler", event="pull_request"
                ),
            ),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_only_a_pull_request_only_workflow_is_pending_not_clean() -> None:
    gateway = _Gateway({HEAD: (_pr("label", workflow="Labeler"),), MERGE: ()})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_the_update_bots_own_dynamic_runs_are_never_watched() -> None:
    bot = "Dependabot Updates"
    gateway = _Gateway(
        {
            HEAD: (
                *GREEN_HEAD,
                _job("Dependabot", "success", run_id=3, workflow=bot, event="dynamic"),
            ),
            MERGE: (
                _job("lint", "success"),
                _job("test", "success"),
                _job("Dependabot", "failure", run_id=OTHER_RUN, workflow=bot, event="dynamic"),
            ),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_jobs_are_keyed_on_workflow_and_name() -> None:
    """`test` in `Release` was never green on the pull request, so its failure is not watched."""
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (
                _job("lint", "success"),
                _job("test", "success"),
                _job("test", "failure", run_id=OTHER_RUN, workflow="Release"),
            ),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_skipped_instance_at_the_head_beside_a_pass_is_ignored() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint"), _pr("test"), _pr("test", "skipped")),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "flaky_retry"


def test_a_job_that_only_skipped_at_the_head_is_not_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint"), _pr("test", "skipped")),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_watched_job_its_own_condition_skipped_is_not_a_failure() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "skipped"))}
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_job_name_that_both_passed_and_failed_on_the_pull_request_is_not_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint"), _pr("test"), _pr("test", "failure")),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_job_that_failed_on_the_pull_request_is_not_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint"), _pr("test", "failure")),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_job_the_pull_request_never_ran_is_not_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (
                _job("lint", "success"),
                _job("test", "success"),
                _job("publish-docs", "failure"),
            ),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_first_failure_asks_for_one_rerun() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "failure"))}
    )

    verdict = judge(gateway, _landing(), None)

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id, verdict.failed_runs) == (
        "flaky_retry",
        ("test",),
        RUN,
        ((RUN, 1),),
    )
    assert PARENT not in gateway.asked


def test_failures_across_two_runs_name_both_runs() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint"), _pr("test", workflow="Tests")),
            MERGE: (
                _job("lint", "failure", run_id=OTHER_RUN, attempt=2),
                _job("test", "failure", workflow="Tests"),
            ),
        }
    )

    verdict = judge(gateway, _landing(), None)

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id, verdict.failed_runs) == (
        "flaky_retry",
        ("lint", "test"),
        RUN,
        ((RUN, 1), (OTHER_RUN, 2)),
    )


def test_timed_out_and_cancelled_ask_for_a_rerun_like_a_failure() -> None:
    for conclusion in ("timed_out", "cancelled"):
        gateway = _Gateway(
            {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", conclusion))}
        )
        assert judge(gateway, _landing(), None).kind == "flaky_retry", conclusion


def test_a_watched_job_with_no_verdict_is_pending() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "action_required"))}
    )

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_failure_that_passes_on_rerun_is_clean() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "success", attempt=2))}
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"
    assert PARENT not in gateway.asked


def test_a_rerun_the_remote_has_not_started_yet_is_pending_not_bad() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=1)),
            PARENT: GREEN_PARENT,
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "pending"


def test_a_pre_existing_second_attempt_is_not_our_rerun() -> None:
    """Someone reran the run before this side first saw it: the failure seen first is attempt 2.
    Our rerun is recorded against attempt 2, so attempt 2 failing again is not our rerun's answer;
    attempt 3 is."""
    first = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("test", "failure", attempt=2),)})
    assert judge(first, _landing(), None).failed_runs == ((RUN, 2),)

    record = _record(rerun_attempts={str(RUN): 2})
    unreported = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("test", "failure", attempt=2),),
            PARENT: GREEN_PARENT,
        }
    )
    assert judge(unreported, _landing(), record).kind == "pending"

    reported = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("test", "failure", attempt=3),),
            PARENT: GREEN_PARENT,
        }
    )
    assert judge(reported, _landing(), record).kind == "bad"


def test_a_failure_in_a_run_not_yet_rerun_asks_for_that_run_only() -> None:
    gateway = _Gateway(
        {
            HEAD: (_pr("lint", workflow="Lint"), _pr("test")),
            MERGE: (
                _job("lint", "failure", run_id=OTHER_RUN, workflow="Lint"),
                _job("test", "failure", attempt=2),
            ),
            PARENT: GREEN_PARENT,
        }
    )

    verdict = judge(gateway, _landing(), _record())

    assert (verdict.kind, verdict.failed_job_names, verdict.failed_runs) == (
        "flaky_retry",
        ("lint",),
        ((OTHER_RUN, 1),),
    )


def test_still_cancelled_after_the_rerun_is_pending_never_bad() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "cancelled", attempt=2)),
            PARENT: GREEN_PARENT,
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "pending"


def test_a_failure_that_survives_the_rerun_with_a_green_parent_is_bad() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: GREEN_PARENT,
        }
    )

    verdict = judge(gateway, _landing(), _record())

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id, verdict.failed_runs) == (
        "bad",
        ("test",),
        RUN,
        ((RUN, 2),),
    )


def test_a_failure_the_parent_shares_is_not_the_bumps() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("lint", "success", run_id=9), _job("test", "failure", run_id=9)),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"


def test_a_job_cancelled_at_the_parent_is_not_green() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("lint", "success", run_id=9), _job("test", "cancelled", run_id=9)),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"


def test_a_failed_job_the_parent_never_ran_is_not_the_bumps() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("lint", "success", run_id=9),),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"


def test_the_parent_is_read_from_its_push_runs_only() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("test", "success", run_id=9, event="pull_request"),),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"


def test_a_parent_where_the_job_also_failed_in_another_run_is_not_green() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("test", "success", run_id=9), _job("test", "failure", run_id=10)),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"


def test_an_unreadable_read_is_unreadable_never_clean() -> None:
    for broken in (HEAD, MERGE):
        gateway = _Gateway(
            {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "success"))},
            broken=frozenset({broken}),
        )
        assert judge(gateway, _landing(), None).kind == "unreadable", broken

    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2))},
        broken=frozenset({PARENT}),
    )
    assert judge(gateway, _landing(), _record()).kind == "unreadable"


def test_a_landing_with_no_merge_commit_is_unreadable() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD})

    assert judge(gateway, _landing(merge_commit_sha=None), None).kind == "unreadable"


def test_a_record_past_the_rerun_is_never_judged_again() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("test", "failure", attempt=2),)})

    for status in ("revert_opened", "revert_landed", "refused_overlap", "revert_failed"):
        verdict = judge(gateway, _landing(), _record(status))
        assert verdict.kind == "pending", status
        assert status in verdict.detail
    assert gateway.asked == []


def test_a_run_is_named_with_its_highest_failing_attempt() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "failure", attempt=2), _job("test", "failure", attempt=1)),
        }
    )

    assert judge(gateway, _landing(), None).failed_runs == ((RUN, 2),)
