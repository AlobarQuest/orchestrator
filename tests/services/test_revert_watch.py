"""Judging a landed Dependabot bump by its own checks on the default branch (Landing Part 3a).

The judgment is pure over what the gateway reads, so every case here runs against a double that
answers `jobs_at` per commit. The review-focus cases this file pins: a job that fails once and
passes on its rerun never reads as bad, a failure the commit before the bump shares is never the
bump's, and a job the pull request never ran, a `pull_request`-only workflow or the update bot's
own runs are never watched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orchestrator.persistence.models import EstatePrMerge, LandingRevert
from orchestrator.services.landing.interfaces import EstateGatewayError, Job
from orchestrator.services.landing.revert_watch import (
    ROLLOUT_SETTLE_MINUTES,
    ROLLOUT_STUCK_HOURS,
    WATCH_HOURS,
    WATCHER_SILENT_HOURS,
    Production,
    ServedReading,
    Verdict,
    judge,
    relation_of,
)
from tests.services.estate_landing_doubles import job_id_of

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

    def rerun_job(self, *, repository: str, job_id: int) -> None:
        raise AssertionError("judging never acts")

    def compare_status(self, *, repository: str, base: str, head: str) -> str:
        raise AssertionError("an inert landing is judged by its checks alone")


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
        job_id=job_id_of(name, run_id, attempt),
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


def test_a_job_failing_for_the_first_time_after_the_rerun_asks_for_a_rerun_not_a_revert() -> None:
    """`test` failed and was rerun; on the rerun it passed and `lint` (skipped until then, since
    it needs `test`) failed for the first time. That is a first failure of its own, not one that
    survived a rerun."""
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("test", "success", attempt=2), _job("lint", "failure", attempt=2)),
            PARENT: GREEN_PARENT,
        }
    )

    verdict = judge(gateway, _landing(), _record())

    assert (verdict.kind, verdict.failed_job_names, verdict.failed_runs) == (
        "flaky_retry",
        ("lint",),
        ((RUN, 2),),
    )


def test_a_job_that_failed_again_after_its_own_rerun_is_bad() -> None:
    """The same job, once its run was rerun past the attempt it first failed at."""
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("test", "success", attempt=3), _job("lint", "failure", attempt=3)),
            PARENT: GREEN_PARENT,
        }
    )
    record = _record(rerun_attempts={str(RUN): 2})
    record.failed_job_names = ["lint", "test"]

    assert judge(gateway, _landing(), record).kind == "bad"


# --------------------------------------------------------------------------------------------
# Landing Part 3b: a deploying landing is judged by what production serves, too.
#
# Real shapes, from the replay (`docs/superpowers/plans/2026-10-30-landing-p3b-signal-replay.md`):
# brain's four applications as the member record names them, brain #79 (sqlalchemy 2.1.1, an image
# that crashed at import in every brain) and brain #70 (app-brain alone left on the build before).
# --------------------------------------------------------------------------------------------

BRAIN = "alobarquest/brain"
BRAINS = ("app-brain", "code-brain", "infra-brain", "open-brain")
# Brain #79's merge commit, the build before it (#80's), and the person's fix forward after it.
MERGE_79 = "271a2ac8de79a1dbc09bc5c05d4be73d08472449"
BEFORE_79 = "80b620ce1799c1eeeaac83f494e6cd9a553d5503"
FIX_FORWARD = "e6cd48ac781eccbebc151d8334be95a8e3e38523"
UNRELATED = "9" * 40
# A commit off a side branch from #79's parent: it diverged from the merge commit, not from main's
# history before the bump.
SIDE = "c" * 40
# A build from before #79's parent: production already stale before the bump.
OLDER = "a2f8233236" + "0" * 30
LANDED_79 = datetime(2026, 10, 3, 6, 15, 19, tzinfo=UTC)
# GitHub's compare of the merge commit against each served commit, as the replay measured it.
COMPARES_79 = {
    MERGE_79: "identical",
    BEFORE_79: "behind",
    FIX_FORWARD: "ahead",
    UNRELATED: "diverged",
    OLDER: "behind",
    SIDE: "diverged",
}
# GitHub's compare of the bump's parent (#80's merge) against each served commit.
PARENT_COMPARES_79 = {
    BEFORE_79: "identical",
    UNRELATED: "diverged",
    OLDER: "behind",
    SIDE: "ahead",
}
GREEN_HEAD_79 = (_pr("test"),)
GREEN_MERGE_79 = (_job("test", "success"),)


class _BrainGateway:
    """Answers `jobs_at`, `parent_of` and `compare_status` for brain. A compare is keyed by its
    base (the merge commit, or its parent) and the served head; an unlisted pair is a 404."""

    def __init__(
        self,
        jobs: dict[str, tuple[Job, ...]] | None = None,
        compares: dict[str, str] | None = None,
        *,
        merge: str = MERGE_79,
        parent: str = BEFORE_79,
        parent_compares: dict[str, str] | None = None,
    ) -> None:
        self._jobs = jobs if jobs is not None else {HEAD: GREEN_HEAD_79, merge: GREEN_MERGE_79}
        self._merge, self._parent = merge, parent
        self._compares = {
            merge: COMPARES_79 if compares is None else compares,
            parent: PARENT_COMPARES_79 if parent_compares is None else parent_compares,
        }
        self.compared: list[tuple[str, str]] = []

    def jobs_at(self, *, repository: str, sha: str) -> tuple[Job, ...]:
        assert repository == BRAIN
        return self._jobs.get(sha, ())

    def parent_of(self, *, repository: str, sha: str) -> str:
        assert (repository, sha) == (BRAIN, self._merge)
        return self._parent

    def rerun_job(self, *, repository: str, job_id: int) -> None:
        raise AssertionError("judging never acts")

    def compare_status(self, *, repository: str, base: str, head: str) -> str:
        assert repository == BRAIN and base in self._compares
        self.compared.append((base, head))
        if head not in self._compares[base]:
            raise EstateGatewayError("commit_not_found", 404)
        return self._compares[base][head]


def _brain_landing(merge: str = MERGE_79) -> EstatePrMerge:
    return EstatePrMerge(
        repository=BRAIN,
        pr_number=79,
        head_sha=HEAD,
        status="merged",
        merge_commit_sha=merge,
        created_at=LANDED_79,
    )


def _at(minutes: float) -> datetime:
    """`minutes` after brain #79 landed."""
    return LANDED_79 + timedelta(minutes=minutes)


def _reading(
    application: str, serving: str | None, minutes: float, tip: str = MERGE_79
) -> ServedReading:
    """A reading received `minutes` after the landing, measured against branch tip `tip` (the
    watcher's `branch_names`): by default the merge commit, i.e. taken after the landing."""
    return ServedReading(
        application=application, serving=serving, received_at=_at(minutes), measured_against=tip
    )


def _everywhere(
    serving: str | None, minutes: float, tip: str = MERGE_79
) -> tuple[ServedReading, ...]:
    return tuple(_reading(app, serving, minutes, tip) for app in BRAINS)


def _production(
    readings: tuple[ServedReading, ...],
    *,
    now: float,
    seen: float | None = None,
    applications: tuple[str, ...] = BRAINS,
    heard: dict[str, float] | None = None,
    landed_at: datetime = LANDED_79,
) -> Production:
    """What the store says at `now` minutes after the landing. The watcher last posted about every
    application at `seen` (default: its newest reading of these applications), unless `heard`
    names an application's own time."""
    if seen is None and readings:
        seen = max((r.received_at - LANDED_79).total_seconds() / 60 for r in readings)
    times = {} if seen is None else {app: seen for app in applications}
    times.update(heard or {})
    return Production(
        applications=applications,
        readings=readings,
        heard={app: _at(minutes) for app, minutes in times.items()},
        landed_at=landed_at,
        now=_at(now),
    )


def _judged(production: Production, gateway: _BrainGateway | None = None) -> Verdict:
    return judge(gateway or _BrainGateway(), _brain_landing(), None, production)


def test_the_served_rule_constants_are_plain_ints_with_real_defaults() -> None:
    assert (ROLLOUT_SETTLE_MINUTES, ROLLOUT_STUCK_HOURS, WATCHER_SILENT_HOURS) == (30, 2, 2)
    assert {
        type(ROLLOUT_SETTLE_MINUTES),
        type(ROLLOUT_STUCK_HOURS),
        type(WATCHER_SILENT_HOURS),
    } == {int}


def test_every_application_serving_the_merge_commit_is_clean() -> None:
    verdict = _judged(_production(_everywhere(MERGE_79, 37), now=180))

    assert (verdict.kind, verdict.reason) == ("clean", None)


def test_a_slow_rollout_behind_first_then_current_is_never_bad() -> None:
    """Review focus 1: `behind` on the first reading after the rollout and `current` on the next."""
    readings = _everywhere(BEFORE_79, 37) + _everywhere(MERGE_79, 97)

    for now in (98, 37 + 60 * 2, 37 + 60 * 5, 60 * 47):
        assert _judged(_production(readings, now=now, seen=now)).kind == "clean"


def test_a_descendant_of_the_merge_commit_is_fine() -> None:
    """Review focus 2: an application serving a later landing serves this one too."""
    gateway = _BrainGateway()

    verdict = _judged(_production(_everywhere(FIX_FORWARD, 40), now=300, seen=299), gateway)

    assert verdict.kind == "clean"
    # Four brains on one commit, measured against the merge commit itself: no self-compare.
    assert gateway.compared == [(MERGE_79, FIX_FORWARD)]


def test_every_application_behind_for_two_hours_is_bad_by_what_production_serves() -> None:
    verdict = _judged(_production(_everywhere(BEFORE_79, 37), now=37 + 120, seen=37 + 119))

    assert (verdict.kind, verdict.reason) == ("bad", "served")
    assert verdict.failed_job_names == ()
    assert "app-brain" in verdict.detail and BEFORE_79[:12] in verdict.detail


def test_every_application_behind_for_just_under_two_hours_is_pending() -> None:
    verdict = _judged(_production(_everywhere(BEFORE_79, 37), now=37 + 119, seen=37 + 118))

    assert verdict.kind == "pending"


def test_every_application_on_a_side_branch_from_the_parent_is_pending() -> None:
    """Diverged from the merge commit but ahead of its parent: a build of main before the bump
    plus something else. Production runs a stray build, which is not the bump's fault: a served
    `bad` needs every application on exactly the bump's parent (the #79 shape)."""
    verdict = _judged(_production(_everywhere(SIDE, 40), now=300, seen=299))

    assert verdict.kind == "pending" and "other than the commit before the bump" in verdict.detail


def test_three_on_the_parent_and_one_on_a_side_branch_is_pending() -> None:
    readings = tuple(_reading(app, BEFORE_79, 37) for app in BRAINS if app != "open-brain") + (
        _reading("open-brain", SIDE, 37),
    )

    verdict = _judged(_production(readings, now=600, seen=599))

    assert verdict.kind == "pending" and SIDE[:12] in verdict.detail


def test_a_served_bad_names_the_parent_production_serves() -> None:
    """The act re-points production at the parent's image, so the verdict says which commit that
    is: the one every application serves."""
    verdict = _judged(_production(_everywhere(BEFORE_79, 37), now=600, seen=599))

    assert (verdict.kind, verdict.parent_sha) == ("bad", BEFORE_79)


def test_production_on_a_commit_not_on_the_default_branch_is_pending() -> None:
    """Diverged from both the merge commit and the bump's parent: production serves something
    that never was main. Whatever it is, it is not this bump's doing."""
    verdict = _judged(_production(_everywhere(UNRELATED, 40), now=300, seen=299))

    assert verdict.kind == "pending" and "other than the commit before the bump" in verdict.detail


def test_a_reading_measured_against_an_older_tip_never_counts() -> None:
    """An `already_merged` landing whose merge commit was committed hours before it was pushed:
    readings taken before the push were measured against the parent and correctly showed it.
    They say nothing about this landing, however early the committer date anchors it."""
    committed = LANDED_79 - timedelta(hours=3)
    before_the_push = _everywhere(BEFORE_79, -120, tip=BEFORE_79)

    early = _judged(_production(before_the_push, now=10, seen=9, landed_at=committed))
    after = before_the_push + _everywhere(BEFORE_79, 7)
    stuck = _judged(_production(after, now=7 + 120, seen=7 + 119, landed_at=committed))
    not_yet = _judged(_production(after, now=7 + 119, seen=7 + 118, landed_at=committed))

    assert early.kind == "pending"
    assert not_yet.kind == "pending"
    assert (stuck.kind, stuck.reason) == ("bad", "served")


def test_a_reading_measured_against_a_later_tip_still_counts() -> None:
    """The branch moved on past the merge commit (a later landing): the reading was still taken
    after this landing."""
    verdict = _judged(_production(_everywhere(BEFORE_79, 37, tip=FIX_FORWARD), now=600, seen=599))

    assert (verdict.kind, verdict.reason) == ("bad", "served")


def test_one_application_of_four_behind_is_never_bad() -> None:
    """Brain #70's shape: app-brain alone stayed on the build before, for days, on an image the
    other three served. Reverting it would have reverted a good bump."""
    readings = (
        _reading("infra-brain", MERGE_79, 41),
        _reading("open-brain", MERGE_79, 41),
        _reading("app-brain", BEFORE_79, 41),
        _reading("code-brain", MERGE_79, 41),
    )

    for now in (180, 60 * 30, 60 * 47):
        verdict = _judged(_production(readings, now=now, seen=now))
        assert verdict.kind == "pending"
        assert "app-brain" in verdict.detail


def test_a_silent_watcher_holds_a_stuck_landing_pending() -> None:
    """The watcher runs on the operator's machine and has had overnight gaps; a reading that has
    "stood" through a gap was never asked again."""
    readings = _everywhere(BEFORE_79, 37)

    stale = _judged(_production(readings, now=37 + 300, seen=37 + 300 - 121))
    live = _judged(_production(readings, now=37 + 300, seen=37 + 300 - 119))

    assert stale.kind == "pending" and "watcher" in stale.detail
    assert (live.kind, live.reason) == ("bad", "served")


def test_no_word_from_the_watcher_at_all_is_pending() -> None:
    verdict = _judged(
        Production(
            applications=BRAINS,
            readings=_everywhere(BEFORE_79, 37),
            heard={},
            landed_at=LANDED_79,
            now=_at(600),
        )
    )

    assert verdict.kind == "pending"


def test_one_application_the_watcher_has_not_posted_about_holds_it_pending() -> None:
    """Liveness is per application: the watcher heard from about three brains but not the fourth
    has not asked the fourth again."""
    readings = _everywhere(BEFORE_79, 37)

    stale = _judged(_production(readings, now=600, seen=590, heard={"open-brain": 600 - 121}))

    assert stale.kind == "pending" and "open-brain" in stale.detail


def test_brain_79_is_pending_through_the_watchers_gap_and_bad_once_it_reports_again() -> None:
    """The replay's brain #79: all four brains behind at 06:52, and nothing new from them until
    15:22. The store holds no heartbeat for that day, so the watcher's next row of any kind (an
    orchestrator reading at 12:54) stands in for it here: silent until then, heard from then."""
    readings = _everywhere(BEFORE_79, 37.4)
    orchestrator_row = (datetime(2026, 10, 3, 12, 54, 13, tzinfo=UTC) - LANDED_79).total_seconds()

    in_the_gap = _judged(_production(readings, now=60 * 4, seen=37.4))
    reported = _judged(_production(readings, now=orchestrator_row / 60, seen=orchestrator_row / 60))

    assert in_the_gap.kind == "pending"
    assert (reported.kind, reported.reason) == ("bad", "served")


def test_an_unstamped_reading_never_counts() -> None:
    """A reading naming no commit says nothing about this landing, either way."""
    behind_then_unstamped = _everywhere(BEFORE_79, 37) + _everywhere(None, 60)
    only_unstamped = _everywhere(None, 37)

    assert _judged(_production(only_unstamped, now=600, seen=599)).kind == "pending"
    stuck = _judged(_production(behind_then_unstamped, now=600, seen=599))
    assert (stuck.kind, stuck.reason) == ("bad", "served")


def test_a_reading_inside_the_settle_time_still_counts_once_the_settle_has_passed() -> None:
    """The settle time delays the verdict; it never drops a reading. A stuck application writes
    ONE reading, on the first pass after the landing; dropping it for arriving early would leave
    the rule nothing to fire on. Stuck is timed from the end of the settle time."""
    readings = _everywhere(BEFORE_79, 29)

    early = _judged(_production(readings, now=30 + 119, seen=30 + 118))
    stuck = _judged(_production(readings, now=150, seen=149))

    assert early.kind == "pending"
    assert (stuck.kind, stuck.reason) == ("bad", "served")


def test_a_reading_after_the_settle_time_is_timed_from_itself() -> None:
    readings = _everywhere(BEFORE_79, 37)

    assert _judged(_production(readings, now=37 + 119, seen=37 + 118)).kind == "pending"
    assert _judged(_production(readings, now=37 + 120, seen=37 + 119)).kind == "bad"


def test_a_reading_from_before_the_landing_never_counts() -> None:
    readings = _everywhere(BEFORE_79, -5)

    assert _judged(_production(readings, now=600, seen=599)).kind == "pending"


def test_production_already_behind_the_bumps_parent_is_not_the_bumps_fault() -> None:
    """A pre-broken main: every brain stuck on a build older than the commit before the bump.
    Production was stale before this bump; reverting it would fix nothing."""
    verdict = _judged(_production(_everywhere(OLDER, 37), now=600, seen=599))

    assert verdict.kind == "pending" and "before the bump" in verdict.detail


def test_a_bump_landed_during_another_bumps_outage_is_not_bad() -> None:
    """L2 lands while every brain is still stuck on the build before #79: L2's parent is #79's
    merge commit, and production is behind that too. #79 is the bad bump, not L2."""
    later = "b" * 40
    gateway = _BrainGateway(
        jobs={HEAD: GREEN_HEAD_79, later: GREEN_MERGE_79},
        compares={BEFORE_79: "behind", later: "identical"},
        merge=later,
        parent=MERGE_79,
        parent_compares={BEFORE_79: "behind"},
    )

    verdict = judge(
        gateway,
        _brain_landing(merge=later),
        None,
        _production(_everywhere(BEFORE_79, 37, tip=later), now=600, seen=599),
    )

    assert verdict.kind == "pending"


def test_brain_79_serving_its_own_parent_is_still_bad_and_compares_each_commit_once() -> None:
    gateway = _BrainGateway()

    verdict = _judged(_production(_everywhere(BEFORE_79, 37), now=600, seen=599), gateway)

    assert (verdict.kind, verdict.reason) == ("bad", "served")
    assert gateway.compared == [(MERGE_79, BEFORE_79)]


def test_an_application_with_no_reading_yet_is_pending() -> None:
    readings = tuple(r for r in _everywhere(BEFORE_79, 37) if r.application != "open-brain")

    verdict = _judged(_production(readings, now=600, seen=599))

    assert verdict.kind == "pending" and "open-brain" in verdict.detail


def test_a_repository_the_member_record_names_no_application_for_is_pending() -> None:
    verdict = _judged(_production((), now=600, seen=599, applications=()))

    assert verdict.kind == "pending"


def test_a_served_commit_the_remote_cannot_compare_is_unreadable_never_clean() -> None:
    gateway = _BrainGateway(compares={})

    verdict = _judged(_production(_everywhere(BEFORE_79, 37), now=600, seen=599), gateway)

    assert verdict.kind == "unreadable"


def test_a_check_failure_on_a_deploying_landing_still_follows_the_check_rule() -> None:
    """Production serving the merge commit does not excuse a check it broke on the default branch:
    one rerun first, then bad by the checks once it survives with a green parent."""
    serving = _production(_everywhere(MERGE_79, 37), now=600, seen=599)
    once = _BrainGateway(jobs={HEAD: GREEN_HEAD_79, MERGE_79: (_job("test", "failure"),)})
    survived = _BrainGateway(
        jobs={
            HEAD: GREEN_HEAD_79,
            MERGE_79: (_job("test", "failure", attempt=2),),
            PARENT: (_job("test", "success", run_id=9),),
        }
    )
    record = LandingRevert(
        repository=BRAIN,
        pr_number=79,
        merge_commit_sha=MERGE_79,
        parent_sha=PARENT,
        failed_job_names=["test"],
        failed_run_id=RUN,
        rerun_attempts={str(RUN): 1},
        status="rerun_requested",
        bumps=[],
    )

    assert judge(once, _brain_landing(), None, serving).kind == "flaky_retry"
    bad = judge(survived, _brain_landing(), record, serving)
    assert (bad.kind, bad.reason, bad.failed_job_names) == ("bad", "checks", ("test",))


def test_a_check_rule_bad_on_an_inert_landing_carries_its_reason() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("test", "failure", attempt=2), _job("lint", "success")),
            PARENT: GREEN_PARENT,
        }
    )

    assert judge(gateway, _landing(), _record()).reason == "checks"


def test_a_served_bad_stands_only_when_the_checks_are_clean() -> None:
    """A check still running, or a rerun this side asked for that has not reported: production
    stuck for three hours is not yet `bad`."""
    stuck = _production(_everywhere(BEFORE_79, 37), now=37 + 180, seen=37 + 179)
    running = _BrainGateway(jobs={HEAD: GREEN_HEAD_79, MERGE_79: (_job("test", None),)})
    unreported = _BrainGateway(jobs={HEAD: GREEN_HEAD_79, MERGE_79: (_job("test", "failure"),)})
    record = LandingRevert(
        repository=BRAIN,
        pr_number=79,
        merge_commit_sha=MERGE_79,
        parent_sha=BEFORE_79,
        failed_job_names=["test"],
        failed_run_id=RUN,
        rerun_attempts={str(RUN): 1},
        status="rerun_requested",
        bumps=[],
    )

    assert _judged(stuck, running).kind == "pending"
    assert judge(unreported, _brain_landing(), record, stuck).kind == "pending"


def test_clean_needs_both_rules() -> None:
    running = _BrainGateway(jobs={HEAD: GREEN_HEAD_79, MERGE_79: (_job("test", None),)})
    serving = _production(_everywhere(MERGE_79, 37), now=600, seen=599)

    assert _judged(serving, running).kind == "pending"


def test_the_served_relation_reads_githubs_compare_of_merge_against_served() -> None:
    assert [relation_of(s) for s in ("identical", "ahead", "behind", "diverged")] == [
        "fine",
        "fine",
        "behind",
        "diverged",
    ]


def test_a_stuck_landing_is_timed_from_the_newest_standing_reading() -> None:
    """Three brains behind for five hours and one that moved to another earlier build an hour ago:
    production has not stood still for two hours, so it is not yet `bad`."""
    readings = tuple(_reading(app, BEFORE_79, 37) for app in BRAINS if app != "open-brain") + (
        _reading("open-brain", BEFORE_79, 37),
        _reading("open-brain", SIDE, 37 + 240),
    )

    verdict = _judged(_production(readings, now=37 + 300, seen=37 + 299))

    assert verdict.kind == "pending"


def test_a_failure_names_each_failed_watched_job_for_its_own_rerun() -> None:
    gateway = _Gateway(
        {
            HEAD: GREEN_HEAD,
            MERGE: (_job("lint", "failure"), _job("test", "failure"), _job("rollout", "failure")),
        }
    )

    verdict = judge(gateway, _landing(), None)

    assert verdict.kind == "flaky_retry"
    assert {(job.name, job.job_id) for job in verdict.failed_jobs} == {
        ("lint", job_id_of("lint", RUN, 1)),
        ("test", job_id_of("test", RUN, 1)),
    }


def test_a_reading_that_names_no_tip_never_counts() -> None:
    """Without the tip it was measured against, nothing says the reading was taken after the
    landing."""
    readings = tuple(
        ServedReading(
            application=app, serving=BEFORE_79, received_at=_at(37), measured_against=None
        )
        for app in BRAINS
    )

    assert _judged(_production(readings, now=600, seen=599)).kind == "pending"
