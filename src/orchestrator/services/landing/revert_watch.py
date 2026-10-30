"""Did a Dependabot bump a lane landed break the default branch, or production? Landing Part 3.

A pure judgment over what the gateway reads and, for a repository whose landing redeploys, what the
store says production served. It decides; the caller acts. Five answers:

* `clean` -- every watched job passed at the merge commit, or the failure is not the bump's; and,
  where production is judged, every application serves the merge commit or a descendant.
* `pending` -- no watched workflow has a push run at the merge commit yet, a watched job is still
  running or reached no verdict, a rerun this side asked for has not reported, or production has
  not settled either way. Never an act.
* `flaky_retry` -- a watched job failed in a run not yet rerun: the caller reruns each job in
  `failed_jobs`, one at a time by its id, and records each run's attempt in `rerun_attempts`.
* `bad` -- the failure survived the rerun and the same jobs passed on the commit before the bump
  (`reason` `checks`), or production is stuck on something other than the bump (`reason`
  `served`).
* `unreadable` -- a read failed. Never `clean`.

## The check rule and the served rule (Landing Part 3b)

An inert landing is judged by the check rule alone. A landing that redeploys is judged by both,
and they combine in this order: the check rule's `unreadable`, `flaky_retry` or `bad` stands (a
rerun is asked for whatever production serves); then the served rule's `unreadable`; then
`clean` only when both rules are `clean`, and the served rule's `bad` only when the check rule is
`clean` -- a check still running, or a rerun not yet reported, may yet be the cause, so the
answer is `pending`; else `pending`.

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
and is rerun by its id like one. Still cancelled after that
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
the member record lists for the repository on its default branch, passed in as `Production`. Each
application's latest reading that names a commit (an `unstamped` one never counts) and was TAKEN
AFTER THE LANDING stands: one the watcher measured against the merge commit or a later tip of the
branch (`branch_names`). That is clock-free: a merge commit's committer date can precede the
landing by hours (a branch pushed straight to the default branch), and a reading measured against
an older tip predates the landing whatever its time. Its relation to the merge commit is
GitHub's compare of the merge commit against the served one: identical or ahead is `fine` (a later
landing serves this one too), behind is `behind`, anything else `diverged`.

* `clean` when every application's standing reading is `fine`. So a slow rollout -- behind on the
  first reading, current on the next -- is never bad.
* `bad` when every application's standing reading is `behind` or `diverged`; the watcher has
  posted about every application within `WATCHER_SILENT_HOURS`; production has stood still for
  `ROLLOUT_STUCK_HOURS`, timed from the newest standing reading or from the end of
  `ROLLOUT_SETTLE_MINUTES` after the landing, whichever is later; and every application serves
  EXACTLY the bump's parent (brain #79's shape: the bump's image never came up, so production kept
  the build before it). One application of several behind is never bad: app-brain alone has
  stayed on a build its three siblings left, for days, on bumps that were fine.
* `pending` otherwise, including when an application has no reading since the landing.

**The settle time delays the verdict; it never drops a reading.** A stuck application writes ONE
reading, on the first pass after the landing (the branch it is measured against moved), and
nothing after; dropping that reading for arriving early would leave the rule nothing to fire on.

**Anything but the parent is not this bump's fault.** If any application serves a commit older
than the commit before the bump, production was already stale before it landed -- a bump landed
during another's outage, or a default branch already broken -- and reverting this one would fix
nothing. Nor is a side-branch build, or a commit that was never on the default branch: production
runs a stray build, and pointing it back at the parent would change what serves for a reason this
bump did not cause. Each is `pending`, never `bad`. A served `bad` names that parent
(`parent_sha`), which the act points production back at.

A commit compared with itself is `identical` without asking GitHub: the merge commit as the tip a
reading was measured against, and the parent as what an application serves.

**Time is `received_at`, never `observed_at`**, which the watcher sets to the served commit's
committer date. The readings are content-addressed, so an application stuck on one commit leaves
ONE row however many passes repeat it: how long a reading has stood is measured from when it was
received, and never by counting rows.

**The watcher is live for an application when the observation ingest accepted a post about it
within `WATCHER_SILENT_HOURS`** (`observation_sources.last_seen_at`), a repeat of a stored reading
included. A repeated reading writes no row, so the readings alone cannot tell a stuck application
from a watcher that stopped; the heartbeat can. The watcher runs on the operator's machine and has
had overnight gaps: through one, a stuck landing stays `pending`, never `bad`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
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
_IDENTICAL: Final = "identical"

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
    # Every failed watched job, which a `flaky_retry` reruns one at a time by its id.
    failed_jobs: tuple[Job, ...] = ()
    # On a served `bad`: the bump's parent, which every application serves.
    parent_sha: str | None = None


@dataclass(frozen=True)
class ServedReading:
    """One `production_revision` reading: what an application served, when it was received, and
    the branch tip the watcher measured it against (`branch_names`). `serving` is None for an
    `unstamped` reading."""

    application: str
    serving: str | None
    received_at: datetime
    measured_against: str | None


@dataclass(frozen=True)
class Production:
    """What the store says production served, for one repository whose landing redeploys.

    `applications` are the member record's apps for the repository on its default branch;
    `readings` every reading of them, in any order; `heard` when the watcher last posted about
    each application (absent: never); `landed_at` the merge commit's time; `now` the instant the
    judgment is made.
    """

    applications: tuple[str, ...]
    readings: tuple[ServedReading, ...]
    heard: Mapping[str, datetime]
    landed_at: datetime
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
        failed_jobs=tuple(failed),
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
    compared: dict[tuple[str, str], str] = {}

    def compare(base: str, served: str) -> str:
        if base.lower() == served.lower():
            return _IDENTICAL
        if (base, served) not in compared:
            compared[(base, served)] = gateway.compare_status(
                repository=landing.repository, base=base, head=served
            )
        return compared[(base, served)]

    parent: list[str] = [] if record is None else [record.parent_sha]

    def against_parent(served: str) -> str:
        if not parent:
            parent.append(gateway.parent_of(repository=landing.repository, sha=merge_sha))
        return compare(parent[0], served)

    try:
        served = served_verdict(
            production, lambda found: relation_of(compare(merge_sha, found)), against_parent
        )
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))
    if served.kind == BAD:
        served = replace(served, parent_sha=parent[0])
    return _both(checks, served)


def _both(checks: Verdict, served: Verdict) -> Verdict:
    """The two rules combined, the check rule `clean` or `pending`: `clean` only when both are,
    and the served rule's `bad` only beside clean checks (a check still running, or a rerun not
    yet reported, may yet be the cause)."""
    if checks.kind == CLEAN and served.kind in (CLEAN, BAD):
        return (
            served if served.kind == BAD else _verdict(CLEAN, f"{checks.detail}; {served.detail}")
        )
    if served.kind == BAD:
        return _verdict(PENDING, f"{served.detail}; but the checks are not clean: {checks.detail}")
    return checks if checks.kind == PENDING else served


def relation_of(compare_status: str) -> str:
    """Where a served commit stands, from GitHub's compare of the merge commit against it."""
    if compare_status in _HOLDS_THE_MERGE:
        return FINE
    return BEHIND if compare_status == _GITHUB_BEHIND else DIVERGED


def served_verdict(
    production: Production,
    relation: Callable[[str], str],
    against_parent: Callable[[str], str],
) -> Verdict:
    """The served rule alone: `clean`, `pending` or `bad` (see the module docstring).

    `relation` answers `relation_of` for a commit against the merge commit, and `against_parent`
    GitHub's compare status of a served commit against the bump's parent; whatever either raises
    is the caller's.
    """
    if not production.applications:
        return _verdict(PENDING, "the member record names no application for the repository")
    latest = _latest(production, relation)
    unread = [app for app in production.applications if app not in latest]
    if unread:
        return _verdict(PENDING, f"no reading since the landing: {', '.join(unread)}")
    standing = {app: latest[app] for app in production.applications}
    relations = {app: relation(str(reading.serving)) for app, reading in standing.items()}
    if all(found == FINE for found in relations.values()):
        return _verdict(CLEAN, "every application serves the merge commit or a later one")
    off = [app for app, found in relations.items() if found != FINE]
    if len(off) < len(relations):
        return _verdict(PENDING, f"not serving the merge commit yet: {', '.join(off)}")
    silent = _silent(production)
    if silent:
        return _verdict(
            PENDING,
            f"every application serves an earlier build, and the watcher has not posted about "
            f"{', '.join(silent)} in {WATCHER_SILENT_HOURS} hours",
        )
    serving = ", ".join(
        f"{app} {relations[app]} on {str(reading.serving)[:12]}"
        for app, reading in standing.items()
    )
    settled = production.landed_at + timedelta(minutes=ROLLOUT_SETTLE_MINUTES)
    stood = production.now - max(settled, *(reading.received_at for reading in standing.values()))
    if stood < timedelta(hours=ROLLOUT_STUCK_HOURS):
        return _verdict(PENDING, f"not serving the merge commit yet: {serving}")
    elsewhere = sorted(
        {
            str(reading.serving)[:12]
            for reading in standing.values()
            if against_parent(str(reading.serving)) != _IDENTICAL
        }
    )
    if elsewhere:
        return _verdict(
            PENDING,
            f"production serves a build other than the commit before the bump "
            f"({', '.join(elsewhere)}): not this bump's doing",
        )
    return Verdict(
        kind=BAD,
        failed_job_names=(),
        run_id=None,
        detail=f"no application has served the merge commit for "
        f"{stood.total_seconds() / 3600:.1f} hours: {serving}",
        reason=SERVED,
    )


def _latest(production: Production, relation: Callable[[str], str]) -> dict[str, ServedReading]:
    """Each application's latest stamped reading taken after the landing: measured against the
    merge commit or a later tip of the branch. Which tip the watcher measured against says when
    the reading was taken without a clock; a reading against an older tip predates the landing."""
    latest: dict[str, ServedReading] = {}
    newest_first = sorted(production.readings, key=lambda r: r.received_at, reverse=True)
    for reading in newest_first:
        if reading.application in latest or reading.serving is None:
            continue
        if reading.received_at < production.landed_at or reading.measured_against is None:
            continue
        if relation(reading.measured_against) == FINE:
            latest[reading.application] = reading
    return latest


def _silent(production: Production) -> list[str]:
    """The applications the watcher has not posted about within `WATCHER_SILENT_HOURS`."""
    after = production.now - timedelta(hours=WATCHER_SILENT_HOURS)
    return [
        app
        for app in production.applications
        if (heard := production.heard.get(app)) is None or heard < after
    ]


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
