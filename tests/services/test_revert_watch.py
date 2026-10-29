"""Judging a landed Dependabot bump by its own checks on the default branch (Landing Part 3a).

The judgment is pure over what the gateway reads, so every case here runs against a double that
answers `jobs_at` per commit. The two review-focus cases this file pins: a job that fails once and
passes on its rerun never reads as bad, and a failure the commit before the bump shares, or a job
the pull request never ran, is never the bump's.
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


class _Gateway:
    """Answers `jobs_at` from a table keyed by commit; a missing commit raises like the remote."""

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

    def rerun_failed_jobs(self, *, repository: str, run_id: int) -> None:
        raise AssertionError("judging never acts")


def _job(name: str, conclusion: str | None, *, run_id: int = RUN, attempt: int = 1) -> Job:
    return Job(name=name, conclusion=conclusion, run_id=run_id, attempt=attempt)


def _landing(*, merge_commit_sha: str | None = MERGE) -> EstatePrMerge:
    return EstatePrMerge(
        repository=REPOSITORY,
        pr_number=42,
        head_sha=HEAD,
        status="merged",
        merge_commit_sha=merge_commit_sha,
    )


def _record(status: str = "rerun_requested") -> LandingRevert:
    return LandingRevert(
        repository=REPOSITORY,
        pr_number=42,
        merge_commit_sha=MERGE,
        parent_sha=PARENT,
        failed_job_names=["test"],
        failed_run_id=RUN,
        status=status,
        bumps=[],
    )


GREEN_HEAD = (_job("lint", "success", run_id=7), _job("test", "success", run_id=7))


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
    """Rerunning a run's failed jobs is refused while that run is still going, and a verdict taken
    before every watched job has finished is a verdict on part of the commit."""
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("lint", None), _job("test", "failure"))})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_watched_job_not_yet_at_the_merge_commit_is_pending_never_clean() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"),)})

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_cancelled_watched_job_is_never_clean() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "cancelled"))}
    )

    assert judge(gateway, _landing(), None).kind == "pending"


def test_a_first_failure_asks_for_one_rerun() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "failure"))}
    )

    verdict = judge(gateway, _landing(), None)

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id) == (
        "flaky_retry",
        ("test",),
        RUN,
    )
    assert PARENT not in gateway.asked


def test_failures_in_two_runs_name_every_job_and_the_lowest_run() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "failure", run_id=RUN + 5), _job("test", "failure", run_id=RUN)),
        }
    )

    verdict = judge(gateway, _landing(), None)

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id) == (
        "flaky_retry",
        ("lint", "test"),
        RUN,
    )


def test_a_failure_that_passes_on_rerun_is_clean() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "success", attempt=2))}
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"
    assert PARENT not in gateway.asked


def test_a_rerun_the_remote_has_not_started_yet_is_pending_not_bad() -> None:
    """Straight after the rerun is asked for, the merge commit still reports the first attempt's
    failure. That is the same answer as before the rerun, not the rerun's."""
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=1)),
            PARENT: (_job("lint", "success"), _job("test", "success")),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "pending"


def test_a_failure_that_survives_the_rerun_with_a_green_parent_is_bad() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("lint", "success", run_id=9), _job("test", "success", run_id=9)),
        }
    )

    verdict = judge(gateway, _landing(), _record())

    assert (verdict.kind, verdict.failed_job_names, verdict.run_id) == ("bad", ("test",), RUN)


def test_a_failure_the_parent_shares_is_not_the_bumps() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("lint", "success", run_id=9), _job("test", "failure", run_id=9)),
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


def test_a_job_the_pull_request_never_ran_is_not_watched() -> None:
    """`deploy-docs` runs only on the default branch; its failure says nothing about the bump."""
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


def test_a_job_that_failed_on_the_pull_request_is_not_watched() -> None:
    gateway = _Gateway(
        {
            HEAD: (_job("lint", "success", run_id=7), _job("test", "failure", run_id=7)),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_an_unreadable_read_is_unreadable_never_clean() -> None:
    for broken in (HEAD, MERGE):
        gateway = _Gateway(
            {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "success"))},
            broken=frozenset({broken}),
        )
        assert judge(gateway, _landing(), None).kind == "unreadable", broken

    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
        },
        broken=frozenset({PARENT}),
    )
    assert judge(gateway, _landing(), _record()).kind == "unreadable"


def test_a_landing_with_no_merge_commit_is_unreadable() -> None:
    gateway = _Gateway({HEAD: GREEN_HEAD})

    assert judge(gateway, _landing(merge_commit_sha=None), None).kind == "unreadable"


def test_a_record_past_the_rerun_is_never_judged_again() -> None:
    """Only no record and `rerun_requested` are judged; a revert in flight or refused is reported
    by its own status, and a verdict that could ask for a second act would be a loop."""
    gateway = _Gateway({HEAD: GREEN_HEAD, MERGE: (_job("test", "failure", attempt=2),)})

    for status in ("revert_opened", "revert_landed", "refused_overlap", "revert_failed"):
        verdict = judge(gateway, _landing(), _record(status))
        assert verdict.kind == "pending", status
        assert status in verdict.detail
    assert gateway.asked == []


def test_a_watched_job_its_own_condition_skipped_is_not_a_failure() -> None:
    gateway = _Gateway(
        {HEAD: GREEN_HEAD, MERGE: (_job("lint", "success"), _job("test", "skipped"))}
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_job_name_that_both_passed_and_failed_on_the_pull_request_is_not_watched() -> None:
    """Two workflows can carry a job of one name; green in one and red in the other was not green
    before the bump."""
    gateway = _Gateway(
        {
            HEAD: (
                _job("lint", "success", run_id=7),
                _job("test", "success", run_id=7),
                _job("test", "failure", run_id=8),
            ),
            MERGE: (_job("lint", "success"), _job("test", "failure")),
        }
    )

    assert judge(gateway, _landing(), None).kind == "clean"


def test_a_parent_where_the_job_also_failed_in_another_run_is_not_green() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "success"), _job("test", "failure", attempt=2)),
            PARENT: (_job("test", "success", run_id=9), _job("test", "failure", run_id=10)),
        }
    )

    assert judge(gateway, _landing(), _record()).kind == "clean"
