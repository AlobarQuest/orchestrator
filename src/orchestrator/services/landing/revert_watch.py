"""Did a Dependabot bump the lane landed break a check on the default branch? Landing Part 3a.

A pure judgment over what the gateway reads. It decides; the caller acts. Five answers:

* `clean` -- every watched job passed at the merge commit, or the failure is not the bump's.
* `pending` -- no watched workflow has a push run at the merge commit yet, a watched job is still
  running or reached no verdict, or a rerun this side asked for has not reported. Never an act.
* `flaky_retry` -- a watched job failed in a run not yet rerun: the caller reruns the failed jobs of
  every run in `failed_runs` and records each run's attempt in `rerun_attempts`.
* `bad` -- the failure survived the rerun, and the same jobs passed on the commit before the bump.
* `unreadable` -- a read failed. Never `clean`.

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
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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

CLEAN: Final = "clean"
PENDING: Final = "pending"
FLAKY_RETRY: Final = "flaky_retry"
BAD: Final = "bad"
UNREADABLE: Final = "unreadable"

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
    gateway: RevertWatchGateway, landing: EstatePrMerge, record: LandingRevert | None
) -> Verdict:
    """The verdict on one landing, from its head, its merge commit and, after a rerun, its parent.

    Judged only with no record or a `rerun_requested` one; a record past the rerun is reported by
    its own status, and answering it `pending` here means no verdict can ask for a second act.
    """
    if record is not None and record.status != _RERUN_REQUESTED:
        return _verdict(PENDING, f"already handled: {record.status}")
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
    new = [job for job in failed if str(job.run_id) not in asked]
    if new:
        return _failed_verdict(FLAKY_RETRY, new, "failed in a run not yet rerun")
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
