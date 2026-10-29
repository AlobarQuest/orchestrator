"""Did a Dependabot bump the lane landed break a check on the default branch? Landing Part 3a.

A pure judgment over what the gateway reads. It decides; the caller acts. Five answers:

* `clean` -- every watched job passed at the merge commit, or the failure is not the bump's.
* `pending` -- a watched job has not finished, has not reported, reached no verdict, or the rerun
  this side asked for has not reported yet. Never an act.
* `flaky_retry` -- a watched job failed and nothing has been tried yet: the caller reruns that run's
  failed jobs once and records `rerun_requested`.
* `bad` -- the failure survived the rerun, and the same jobs passed on the commit before the bump.
* `unreadable` -- a read failed. Never `clean`.

## Which jobs are watched

The job names that concluded `success` at the pull request's head before it landed. A job the
pull request never ran (a default-branch-only job) or never passed says nothing about the bump,
so it is never watched: a revert needs a check that was green before the bump and red after it.

## Two failures that are not the bump's

A job that fails once and passes on its rerun is flaky, and reads `clean`. A job that also fails on
the merge commit's parent was already failing, and reads `clean` too: the parent is the default
branch without the bump, because landings are squash merges.
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

_SUCCESS: Final = "success"
_FAILURE: Final = "failure"
# Completions that are not a failure of the check: the platform reports a job its own condition
# skipped this way, and branch protection counts both as passing.
_PASSED: Final = frozenset({_SUCCESS, "skipped", "neutral"})  # not-a-vocabulary: GitHub's own


@dataclass(frozen=True)
class Verdict:
    kind: str
    failed_job_names: tuple[str, ...]
    run_id: int | None
    detail: str


def _verdict(kind: str, detail: str) -> Verdict:
    return Verdict(kind=kind, failed_job_names=(), run_id=None, detail=detail)


def _by_name(jobs: Iterable[Job], names: set[str]) -> dict[str, list[Job]]:
    found: dict[str, list[Job]] = {}
    for job in jobs:
        if job.name in names:
            found.setdefault(job.name, []).append(job)
    return found


def _watched(head_jobs: Iterable[Job]) -> set[str]:
    """Names every instance of which concluded `success` at the head; a name that also failed or
    never finished there was not green before the bump, so it cannot be turned red by it."""
    by_name: dict[str, list[Job]] = {}
    for job in head_jobs:
        by_name.setdefault(job.name, []).append(job)
    return {
        name for name, jobs in by_name.items() if all(job.conclusion == _SUCCESS for job in jobs)
    }


def _judge(
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
        watched = _watched(gateway.jobs_at(repository=repository, sha=landing.head_sha))
        if not watched:
            return _verdict(CLEAN, "the pull request passed no job, so nothing is watched")
        at_merge = _by_name(gateway.jobs_at(repository=repository, sha=merge_sha), watched)
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))

    failed = [
        job for name in sorted(at_merge) for job in at_merge[name] if job.conclusion == _FAILURE
    ]
    settled = _unfailed_verdict(watched, at_merge)
    if settled is not None:
        return settled
    names = tuple(sorted({job.name for job in failed}))
    run_id = min(job.run_id for job in failed)
    if record is None:
        return Verdict(FLAKY_RETRY, names, run_id, f"failed once: {', '.join(names)}")
    if any(job.attempt <= 1 for job in failed):
        # The merge commit still reports the first attempt: the rerun has not reported yet.
        return _verdict(PENDING, "the rerun has not reported yet")
    try:
        at_parent = _by_name(
            gateway.jobs_at(repository=repository, sha=record.parent_sha), set(names)
        )
    except EstateGatewayError as error:
        return _verdict(UNREADABLE, gateway_failure_detail(error))
    parent_green = all(
        name in at_parent and all(job.conclusion == _SUCCESS for job in at_parent[name])
        for name in names
    )
    if not parent_green:
        return _verdict(CLEAN, f"the commit before the bump did not pass: {', '.join(names)}")
    return Verdict(BAD, names, run_id, f"failed after a rerun, passed before: {', '.join(names)}")


def _unfailed_verdict(watched: set[str], at_merge: dict[str, list[Job]]) -> Verdict | None:
    """`pending` or `clean` from the merge commit's watched jobs, or None when one failed.

    A watched job that has not reported or finished outranks a failure: the platform refuses to
    rerun a run still going, and a verdict on part of a commit is not a verdict. A failure outranks
    a job that reached no verdict.
    """
    missing = sorted(watched - at_merge.keys())
    if missing:
        return _verdict(PENDING, f"not yet reported at the merge commit: {', '.join(missing)}")
    jobs = [job for name in sorted(at_merge) for job in at_merge[name]]
    running = sorted({job.name for job in jobs if job.conclusion is None})
    if running:
        return _verdict(PENDING, f"still running: {', '.join(running)}")
    if any(job.conclusion == _FAILURE for job in jobs):
        return None
    unanswered = sorted({job.name for job in jobs if job.conclusion not in _PASSED})
    if unanswered:
        return _verdict(PENDING, f"no verdict: {', '.join(unanswered)}")
    return _verdict(CLEAN, "every watched job passed at the merge commit")
