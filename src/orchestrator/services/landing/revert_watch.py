"""Did a Dependabot bump a lane landed break the default branch, or production? Landing Part 3.

A pure judgment over what the gateway reads and, for a repository whose landing redeploys, what the
store says production served. It decides; the caller acts. Five answers:

* `clean` -- every watched job passed at the merge commit, or the failure is not the bump's; and,
  where production is judged, every application serves the merge commit or a descendant.
* `pending` -- no watched workflow has a push run at the merge commit yet, a watched job is still
  running or reached no verdict, a rerun this side asked for has not reported, or production has
  not settled either way. Never an act.
* `flaky_retry` -- a watched job failed in a run not yet rerun: the caller reruns the failed jobs of
  every run in `failed_runs` and records each run's attempt in `rerun_attempts`.
* `bad` -- the failure survived the rerun and the same jobs passed on the commit before the bump
  (`reason` `checks`), or production is stuck on something other than the bump (`reason`
  `served`).
* `unreadable` -- a read failed. Never `clean`.

## The check rule and the served rule (Landing Part 3b)

An inert landing is judged by the check rule alone. A landing that redeploys is judged by both,
and they combine in this order: the check rule's `unreadable`, `flaky_retry` or `bad` stands (a
rerun is asked for whatever production serves); then the served rule's `unreadable` or `bad`
stands, even while a check is still running, since whether the checks finish says nothing about
what production serves; then `clean` only when both rules are `clean`; else `pending`.

## Which jobs are watched

A job is identified by (workflow name, job name). Watched are the jobs that concluded `success` at
the pull request's head, in a workflow that has a `push` run at the merge commit. Instances at the
head that were `skipped` or `neutral` are ignored when another instance passed; a job that failed
or never finished at the head was not green before the bump, so it is not watched.

So a `pull_request`-only workflow, and the update bot's own `dynamic` runs, are never watched: they
have no push run on the default branch. A watched workflow's job that is absent from its push run
is not watched either, so it cannot hold the landing open. Until any watched workflow has a push run
at the merge commit, the answer is `pending`.

## What counts as failed

`failure`, `timed_out` and `cancelled`. A cancelled job asks for a rerun like a failure does,
because the platform's rerun of failed jobs reruns cancelled ones too. Still cancelled after that
rerun is `pending`, never `bad`: a cancellation says nothing about the bump. At the parent, a
cancelled job is not green, so a failure beside it reads `clean`.

## Two failures that are not the bump's

A job that fails once and passes on its rerun is flaky, and reads `clean`. A failure counts as
having survived the rerun only at an attempt later than the one recorded when the rerun was asked
for, so a run someone else had already rerun is not mistaken for ours. A job that also fails on
the merge commit's parent was already failing, and reads `clean`: the parent is the default branch
without the bump, because landings are squash merges.

## The served rule

What production serves is `revision_watcher`'s `production_revision` reading of each application
the member record lists for the repository, passed in as `Production`. A reading counts when it
names a commit (an `unstamped` one never counts) and was RECEIVED at least `ROLLOUT_SETTLE_MINUTES`
after the landing. Its relation to the merge commit is GitHub's compare of the merge commit against
the served one: identical or ahead is `fine` (a later landing serves this one too), behind is
`behind`, anything else `diverged`.

* `clean` when every application's latest counted reading is `fine`. So a slow rollout -- behind on
  the first reading, current on the next -- is never bad.
* `bad` when every application's latest counted reading is `behind` or `diverged`, and has stood
  for at least `ROLLOUT_STUCK_HOURS`, and the watcher has reported within `WATCHER_SILENT_HOURS`.
  One application of several behind is never bad: app-brain alone has stayed on a build its three
  siblings left, for days, on bumps that were fine.
* `pending` otherwise, including when an application has no counted reading yet.

**Time is `received_at`, never `observed_at`**, which the watcher sets to the served commit's
committer date. The readings are content-addressed, so an application stuck on one commit leaves
ONE row however many passes repeat it: how long a reading has stood is measured from when it was
received, and never by counting rows.

**The watcher is live when it wrote any reading within `WATCHER_SILENT_HOURS`, of any
application.** Because a repeated reading writes nothing, nothing in the store can say that this
application was asked again in the last two hours: a reading that had to be that recent could
never have stood two hours. The newest reading of any application is the evidence the store has
that a pass ran; it misses a pass that changed nothing anywhere, which holds a stuck landing
`pending` (never `bad`) until something in the estate moves. Brain #79 (2026-10-03) is the measured
case: all four brains behind from 06:52, the watcher silent until a row at 12:54, `bad` then.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from orchestrator.persistence.models import EstatePrMerge, LandingRevert
from orchestrator.services.landing.interfaces import (
    EstateGatewayError,
    Job,
    RevertWatchGateway,
    gateway_failure_detail,
)

# How long after landing a bump is watched. A plain int with a real default and no off value.
WATCH_HOURS: Final = 48
# The served rule's three times, each a plain int with a real default and no off value: a reading
# counts this long after the landing, makes `bad` once it has stood this long, and the watcher must
# have written a reading this recently for a standing one to count as asked again.
ROLLOUT_SETTLE_MINUTES: Final = 30
ROLLOUT_STUCK_HOURS: Final = 2
WATCHER_SILENT_HOURS: Final = 2

CLEAN: Final = "clean"
PENDING: Final = "pending"
FLAKY_RETRY: Final = "flaky_retry"
BAD: Final = "bad"
UNREADABLE: Final = "unreadable"

# Why a landing is `bad`: which rule found it. The act chooses its path by it.
CHECKS: Final = "checks"
SERVED: Final = "served"

# Where a served commit stands against the merge commit.
FINE: Final = "fine"
BEHIND: Final = "behind"
DIVERGED: Final = "diverged"
# GitHub's compare statuses that mean the served commit holds the merge commit.
_HOLDS_THE_MERGE: Final = frozenset({"identical", "ahead"})  # not-a-vocabulary: GitHub's statuses
_GITHUB_BEHIND: Final = "behind"

# The one record status this judgment reads on; every later status is reported by itself.
_RERUN_REQUESTED: Final = "rerun_requested"

_PUSH: Final = "push"
_SUCCESS: Final = "success"
_CANCELLED: Final = "cancelled"
# GitHub's own conclusions. Not failures: a job its own condition skipped, which branch protection
# counts as passing.
_NOT_RUN: Final = frozenset({"skipped", "neutral"})  # not-a-vocabulary: GitHub's conclusions
_FAILED: Final = frozenset({"failure", "timed_out", _CANCELLED})  # not-a-vocabulary: GitHub's

_Key = tuple[str, str]  # (workflow name, job name)


@dataclass(frozen=True)
class Verdict:
    kind: str
    failed_job_names: tuple[str, ...]
    run_id: int | None
    detail: str
    # (run id, highest failing attempt) for every run with a failed watched job, lowest run first.
    failed_runs: tuple[tuple[int, int], ...] = ()
    # `checks` or `served` on a `bad` verdict; None on every other.
    reason: str | None = None


@dataclass(frozen=True)
class ServedReading:
    """One `production_revision` reading: what an application served, and when it was received.
    `serving` is None for an `unstamped` reading."""

    application: str
    serving: str | None
    received_at: datetime


@dataclass(frozen=True)
class Production:
    """What the store says production served, for one repository whose landing redeploys.

    `applications` are the member record's apps for the repository; `readings` every reading of
    them, in any order; `watcher_seen_at` the newest reading of ANY application (None: none at all);
    `now` the instant the judgment is made.
    """

    applications: tuple[str, ...]
    readings: tuple[ServedReading, ...]
    watcher_seen_at: datetime | None
    now: datetime


def _verdict(kind: str, detail: str) -> Verdict:
    return Verdict(kind=kind, failed_job_names=(), run_id=None, detail=detail)


def _failed_verdict(kind: str, failed: list[Job], detail: str) -> Verdict:
    runs: dict[int, int] = {}
    for job in failed:
        runs[job.run_id] = max(job.attempt, runs.get(job.run_id, 0))
    names = tuple(sorted({job.name for job in failed}))
    return Verdict(
        kind=kind,
        failed_job_names=names,
        run_id=min(runs),
        detail=f"{detail}: {', '.join(names)}",
        failed_runs=tuple(sorted(runs.items())),
        reason=CHECKS if kind == BAD else None,
    )


def _grouped(jobs: Iterable[Job]) -> dict[_Key, list[Job]]:
    found: dict[_Key, list[Job]] = {}
    for job in jobs:
        found.setdefault((job.workflow, job.name), []).append(job)
    return found


def _green_at_head(head_jobs: Iterable[Job]) -> set[_Key]:
    """Jobs that passed at the head and did nothing else there, skipped instances aside."""
    green: set[_Key] = set()
    for key, jobs in _grouped(head_jobs).items():
        ran = [job for job in jobs if job.conclusion not in _NOT_RUN]
        if ran and all(job.conclusion == _SUCCESS for job in ran):
            green.add(key)
    return green


def judge(
    gateway: RevertWatchGateway,
    landing: EstatePrMerge,
    record: LandingRevert | None,
    production: Production | None = None,
) -> Verdict:
    """The verdict on one landing, from its head, its merge commit and, after a rerun, its parent;
    and from what production served, when `production` is given (a landing that redeploys).

    Judged only with no record or a `rerun_requested` one; a record past the rerun is reported by
    its own status, and answering it `pending` here means no verdict can ask for a second act.
    """
    if record is not None and record.status != _RERUN_REQUESTED:
        return _verdict(PENDING, f"already handled: {record.status}")
    checks = _judge_checks(gateway, landing, record)
    if production is None or checks.kind in (UNREADABLE, FLAKY_RETRY, BAD):
        return checks
    merge_sha = landing.merge_commit_sha or ""
    compared: dict[str, str] = {}

    def relation(served: str) -> str:
        if served not in compared:
            compared[served] = relation_of(
                gateway.compare_status(repository=landing.repository, base=merge_sha, head=served)
            )
        return compared[served]

    try:
        served = served_verdict(production, landing.created_at, relation)
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))
    if served.kind == BAD:
        return served
    if served.kind == CLEAN and checks.kind == CLEAN:
        return _verdict(CLEAN, f"{checks.detail}; {served.detail}")
    return checks if checks.kind == PENDING else served


def relation_of(compare_status: str) -> str:
    """Where a served commit stands, from GitHub's compare of the merge commit against it."""
    if compare_status in _HOLDS_THE_MERGE:
        return FINE
    return BEHIND if compare_status == _GITHUB_BEHIND else DIVERGED


def served_verdict(
    production: Production, landed_at: datetime, relation: Callable[[str], str]
) -> Verdict:
    """The served rule alone: `clean`, `pending` or `bad` (see the module docstring).

    `relation` answers `relation_of` for a served commit; whatever it raises is the caller's.
    """
    if not production.applications:
        return _verdict(PENDING, "the member record names no application for the repository")
    settled = landed_at + timedelta(minutes=ROLLOUT_SETTLE_MINUTES)
    latest: dict[str, ServedReading] = {}
    for reading in production.readings:
        if reading.serving is None or reading.received_at < settled:
            continue
        held = latest.get(reading.application)
        if held is None or reading.received_at > held.received_at:
            latest[reading.application] = reading
    unread = [app for app in production.applications if app not in latest]
    if unread:
        return _verdict(PENDING, f"no reading since the rollout settled: {', '.join(unread)}")
    standing = {app: latest[app] for app in production.applications}
    relations = {app: relation(str(reading.serving)) for app, reading in standing.items()}
    if all(found == FINE for found in relations.values()):
        return _verdict(CLEAN, "every application serves the merge commit or a later one")
    off = [app for app, found in relations.items() if found != FINE]
    if len(off) < len(relations):
        return _verdict(PENDING, f"not serving the merge commit yet: {', '.join(off)}")
    seen = production.watcher_seen_at
    if seen is None or production.now - seen > timedelta(hours=WATCHER_SILENT_HOURS):
        return _verdict(
            PENDING,
            f"every application serves an earlier build, and the watcher has not reported in "
            f"{WATCHER_SILENT_HOURS} hours",
        )
    stood = production.now - max(reading.received_at for reading in standing.values())
    serving = ", ".join(
        f"{app} {relations[app]} on {str(reading.serving)[:12]}"
        for app, reading in standing.items()
    )
    if stood < timedelta(hours=ROLLOUT_STUCK_HOURS):
        return _verdict(PENDING, f"not serving the merge commit yet: {serving}")
    return Verdict(
        kind=BAD,
        failed_job_names=(),
        run_id=None,
        detail=f"no application has served the merge commit for "
        f"{stood.total_seconds() / 3600:.1f} hours: {serving}",
        reason=SERVED,
    )


def _judge_checks(
    gateway: RevertWatchGateway, landing: EstatePrMerge, record: LandingRevert | None
) -> Verdict:
    """The check rule alone, on a landing with no record or a `rerun_requested` one."""
    merge_sha = landing.merge_commit_sha
    if not merge_sha:
        return _verdict(UNREADABLE, "the landing recorded no merge commit")
    repository = landing.repository
    try:
        green = _green_at_head(gateway.jobs_at(repository=repository, sha=landing.head_sha))
        if not green:
            return _verdict(CLEAN, "the pull request passed no job, so nothing is watched")
        pushed = [
            job
            for job in gateway.jobs_at(repository=repository, sha=merge_sha)
            if job.event == _PUSH
        ]
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))

    workflows = {job.workflow for job in pushed}
    if not any(workflow in workflows for workflow, _ in green):
        return _verdict(PENDING, "no watched workflow has a push run at the merge commit yet")
    watched = [job for job in pushed if (job.workflow, job.name) in green]
    settled = _unfailed_verdict(watched)
    if settled is not None:
        return settled
    failed = [job for job in watched if job.conclusion in _FAILED]
    if record is None:
        return _failed_verdict(FLAKY_RETRY, failed, "failed once")
    asked = record.rerun_attempts or {}
    # Survival is per job: a failure counts toward `bad` only for a job the record already names,
    # in a run already rerun. A job failing for the first time -- one skipped until the job it
    # needs passed on the rerun -- is a first failure of its own, and asks for its own rerun.
    known = set(record.failed_job_names)
    new = [job for job in failed if str(job.run_id) not in asked or job.name not in known]
    if new:
        return _failed_verdict(FLAKY_RETRY, new, "failed in a run or job not yet rerun")
    if any(job.attempt <= asked[str(job.run_id)] or job.conclusion == _CANCELLED for job in failed):
        return _verdict(PENDING, "the rerun has not reported a failure of its own yet")
    return _after_rerun(gateway, repository, record.parent_sha, failed)


def _unfailed_verdict(watched: list[Job]) -> Verdict | None:
    """`pending` or `clean` from the merge commit's watched jobs, or None when one failed.

    A watched job still running outranks a failure: the platform refuses to rerun a run still
    going, and a verdict on part of a commit is not a verdict. A failure outranks a job that reached
    no verdict.
    """
    running = sorted({job.name for job in watched if job.conclusion is None})
    if running:
        return _verdict(PENDING, f"still running: {', '.join(running)}")
    if any(job.conclusion in _FAILED for job in watched):
        return None
    unanswered = sorted(
        {
            job.name
            for job in watched
            if job.conclusion != _SUCCESS and job.conclusion not in _NOT_RUN
        }
    )
    if unanswered:
        return _verdict(PENDING, f"no verdict: {', '.join(unanswered)}")
    return _verdict(CLEAN, "every watched job passed at the merge commit")


def _after_rerun(
    gateway: RevertWatchGateway, repository: str, parent_sha: str, failed: list[Job]
) -> Verdict:
    """`bad` when every job that survived the rerun passed at the parent's push runs, else clean."""
    keys = {(job.workflow, job.name) for job in failed}
    try:
        at_parent = _grouped(
            job
            for job in gateway.jobs_at(repository=repository, sha=parent_sha)
            if job.event == _PUSH and (job.workflow, job.name) in keys
        )
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))
    parent_green = all(
        key in at_parent and all(job.conclusion == _SUCCESS for job in at_parent[key])
        for key in keys
    )
    if not parent_green:
        return _failed_verdict(CLEAN, failed, "the commit before the bump did not pass either")
    return _failed_verdict(BAD, failed, "failed after a rerun, passed before")
