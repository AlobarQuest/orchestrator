"""Read one landed pull request's rollout from GitHub. Decides nothing; refuses a lot.

The verdict is derived by change-manager from the facts this module gathers. What lives here is
the harder half: knowing when the facts are not gatherable, and saying so instead of producing a
confident answer from an unanswered question. Three of the refusals below exist because a
reviewer showed that their absence manufactures a finding out of a non-event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from deploy_watcher.github import GitHubReader, ReadError
from deploy_watcher.model import Finding, Merge, Rollout, Run
from deploy_watcher.workflows import (
    ATTESTS_REVISION,
    ATTESTS_UNKNOWN,
    RolloutWorkflow,
    attestation_for,
    level_of,
    rollout_for,
)

# How long after a merge a missing rollout run stops meaning "GitHub has not indexed it yet" and
# starts meaning "it never ran". A plain int with a real default and NO off value: a reporting
# obligation that can be switched off is one that will be. Waiting longer than the acceptance
# criteria allow can only ever suppress a false alarm, never create one -- change-manager's own
# criterion allows ten minutes and brain's job finishes in about two.
SETTLE_SECONDS = 1800

# Findings this module can produce. Each is a fact about the estate, never an action.
PULL_REQUEST_MISSING = "pull_request_missing"
ROLLOUT_ABSENT = "rollout_never_ran"
ROLLOUT_STUCK = "rollout_run_never_concluded"
ROLLOUT_NOT_SUCCESS = "rollout_did_not_succeed"
ROLLOUT_JOB_NOT_FOUND = "rollout_job_named_by_the_registry_is_not_in_the_run"
MERGE_TARGETED_ANOTHER_BRANCH = "merge_did_not_target_the_rollout_branch"
MERGE_DIVERGENCE = "observed_at_more_than_one_merge_commit"
RECHECK_DIVERGENCE = "recorded_facts_no_longer_match_github"

# ADR-0044. Reported as an EXCEPTION rather than a finding: the rollout did fail, and a later
# rollout that production is serving has moved past it, so there is nothing left for a reader to
# do. Deliberately NOT spelled `rollout_did_not_succeed_but_...`: a kind that CONTAINS another
# breaks every substring reader, including this estate's own discriminating tests.
SUPERSEDED_ROLLOUT = "production_has_moved_past_this_failed_rollout"


class NotSettled(Exception):
    """Not an answer yet, and not a failure to measure. Reported as `pending`, exit 0.

    Distinct from `Unmeasurable` because the two have opposite meanings for a scheduled pass:
    "come back in an hour" versus "something is broken and the answer is missing". Folding a
    still-running sibling run into `Unmeasurable` exits 3 for that run's whole duration, under a
    diagnosis that names the wrong problem.
    """


class Unmeasurable(Exception):
    """The question could not be asked. Exit 3, never a finding.

    Separate from `ReadError` so that "GitHub is down" and "nobody classified this repository"
    are distinguishable in the report, while folding to the same exit code.
    """


@dataclass(frozen=True)
class Outcome:
    """What one pass learned about one change.

    `rollout`, `pending` and `findings` were once mutually exclusive and are not: a settled
    rollout carries both an observation and whatever it found. `exceptions` is ADR-0044's, and it
    holds findings that were MEASURED and then EXCUSED -- same `Finding` type, deliberately,
    because an exception is not a lesser fact. It is the same fact with a reason a reader can
    dispute.
    """

    subject: str
    rollout: Rollout | None = None
    pending: str | None = None
    findings: tuple[Finding, ...] = ()
    exceptions: tuple[Finding, ...] = ()


def _unanimous(runs: list[Run]) -> Run:
    """Reduce the runs at a head to one, or refuse.

    Measured over both repositories' entire history, every merge produced exactly one rollout
    run -- 67 of 67 -- so this is a guard against a future that has not happened rather than a
    routine reduction. Unanimity rather than newest-wins, copying `services/github_checks.py`:
    picking either the newest or the first would let a genuinely failing rollout be resolved by
    a green sibling.
    """
    if len(runs) == 1:
        return runs[0]
    if any(not run.concluded for run in runs):
        # Not disagreement — one of them has not finished. Reporting `Unmeasurable` here would
        # exit 3 for the sibling's whole duration, with a diagnosis ("they do not agree") that
        # names the wrong problem.
        raise NotSettled(f"{len(runs)} rollout runs at this commit and one is still running")
    conclusions = {run.conclusion for run in runs}
    if len(conclusions) != 1:
        raise Unmeasurable(
            f"{len(runs)} rollout runs at this commit and they do not agree: "
            f"{', '.join(sorted(str(c) for c in conclusions))}"
        )
    return max(runs, key=lambda run: (run.run_id, run.run_attempt))


def superseded_by(
    reader: GitHubReader,
    repository: str,
    workflow: RolloutWorkflow,
    merge_commit_sha: str,
) -> str | None:
    """Why production has moved past this failed rollout, or None if it has not.

    ADR-0044. Devon ruled on 2026-09-09 that a rollout which failed, and which production has
    since moved past, is an EXCEPTION rather than a finding -- the same ruling the landing lane
    got on 2026-08-13. A finding a reader can do nothing about is one that trains its reader to
    ignore the report.

    FOUR CLAUSES, AND ALL FOUR MUST HOLD. Each excludes a way of being confidently wrong:

    1. There is a NEWEST successful push run on the rollout branch. Newest, not any: "A failed,
       B succeeded, C failed" supersedes A and must not supersede C.
    2. Its head is strictly `ahead` of this merge commit. `identical` is the failed rollout's own
       commit -- a green sibling run at the same head is a disagreement for `_unanimous` to
       refuse, not proof that anything moved -- and `diverged` means the two are on different
       lines of history, so the success says nothing about this merge's build.
    3. The superseding run's OWN workflow revision is ATTESTS_REVISION. This is the clause that
       is easy to leave out and it is the one that makes the excuse worth anything: a green run
       of a workflow that only pokes a webhook does not establish that production is serving
       anything, so excusing a real failure with it would be excusing it with nothing. The excuse
       must be at least as strong as what would SETTLE a record under change-manager's own rule.
    4. The runs at that head reduce to one, and it concluded success. A head carrying both a
       failed and a passed run is ambiguous, and `_unanimous` is the existing rule for that --
       reused rather than re-decided, because a second copy of a reduction rule is drift.

    **THE FAIL DIRECTION IS THE WHOLE DESIGN OF THIS FUNCTION.** The finding is ALREADY
    ESTABLISHED by the time anyone asks for an excuse, so every way of failing to find one means
    "not superseded" and the finding stands. `ReadError`, `NotSettled` and `Unmeasurable` are
    therefore caught HERE and never propagate: letting them reach `_watch_one`'s
    `except (Unmeasurable, ReadError)` would turn a measured failure into `incomplete` and exit 3
    on a GitHub hiccup, which is a strictly worse answer than the finding it replaced. A reader
    told "this rollout failed" can act; a reader told "something could not be read" cannot.

    Returns the reason as prose, so the caller can put the superseding run, its head and its
    workflow revision in front of a reader who wants to dispute the excuse.
    """
    try:
        newest = reader.newest_successful_push_run(
            repository, workflow.path, workflow.trigger_branch
        )
        if newest is None or newest.head_sha is None:
            return None
        head = newest.head_sha
        if reader.compare_status(repository, merge_commit_sha, head) != "ahead":
            return None
        revision = reader.blob_revision(repository, workflow.path, head)
        if level_of(revision) != ATTESTS_REVISION:
            return None
        later = _unanimous(reader.runs_at_head(repository, workflow.path, head))
    except (ReadError, NotSettled, Unmeasurable):
        # Not "we could not tell". The finding was measured and stands; only the EXCUSE is
        # missing, and an absent excuse is exactly a finding that is not excused.
        return None
    if not later.concluded or later.conclusion != "success":
        return None
    assert revision is not None  # level_of only returns ATTESTS_REVISION for a transcribed sha
    return (
        f"run {later.run_id} of {workflow.path} succeeded at {head[:8]}, which is ahead of this "
        f"merge, at workflow revision {revision[:8]} — production has moved past this rollout"
    )


def _nothing_ran(
    subject: str,
    merge: Merge,
    workflow_path: str,
    revision: str,
    attestation: str,
    *,
    early: bool,
) -> Outcome:
    """No run exists at the merge commit. `early` is the whole difference between two answers.

    Inside the settle window this is "GitHub has not indexed it yet" — come back. Outside it, it
    is the finding this program exists to produce: the merge landed and the rollout never ran.
    """
    if early:
        return Outcome(subject, pending="no rollout run yet, and the merge is recent")
    assert merge.merge_commit_sha is not None and merge.merged_at is not None
    return Outcome(
        subject,
        rollout=Rollout(
            merge=merge,
            workflow_path=workflow_path,
            workflow_revision=revision,
            attestation=attestation,
            run=None,
            settled=True,
        ),
        findings=(
            Finding(
                ROLLOUT_ABSENT,
                subject,
                f"merged at {merge.merged_at.isoformat()} and no run of {workflow_path} "
                f"exists at {merge.merge_commit_sha[:8]}",
            ),
        ),
    )


def _wrong_branch(subject: str, merge: Merge, workflow: RolloutWorkflow) -> Outcome:
    """Merged, with a real merge commit — and no rollout will ever run at it.

    Returned BEFORE the settle window can turn it into `rollout_never_ran`: that finding means
    "the rollout should have run and did not", and here it never should have. A change record
    asserting that landing this deploys production is wrong about its own subject, which is
    worth saying out loud rather than swallowing.
    """
    return Outcome(
        subject,
        findings=(
            Finding(
                MERGE_TARGETED_ANOTHER_BRANCH,
                subject,
                f"merged into {merge.base_ref!r}, and {workflow.path} fires on "
                f"{workflow.trigger_branch!r} — landing this did not deploy anything",
            ),
        ),
    )


def observe(
    reader: GitHubReader,
    repository: str,
    pull_request_number: int,
    *,
    now: datetime,
    settle_seconds: int = SETTLE_SECONDS,
) -> Outcome:
    """Everything observable about the rollout one pull request caused."""
    subject = f"{repository}#{pull_request_number}"

    workflow = rollout_for(repository)
    if workflow is None:
        # Refused rather than guessed. "The workflow that looks like a deploy" is the parser this
        # program does not have, and guessing wrong points every later hop at the wrong run.
        raise Unmeasurable(f"no rollout workflow is declared for {repository}")

    merge = reader.read_merge(repository, pull_request_number)
    if merge is None:
        return Outcome(
            subject,
            findings=(Finding(PULL_REQUEST_MISSING, subject, "GitHub has no such pull request"),),
        )
    if not merge.merged or merge.merge_commit_sha is None or merge.merged_at is None:
        # NOT a finding. Before the merge lanes open this is the ordinary state of every deploy
        # change, and GitHub puts a throwaway test-merge commit in `merge_commit_sha` on an open
        # pull request -- so treating the sha as evidence of a landing would fabricate the
        # `rollout_never_ran` finding against a change that never merged.
        return Outcome(subject, pending="the pull request has not merged")

    if merge.base_ref != workflow.trigger_branch:
        return _wrong_branch(subject, merge, workflow)

    if not reader.workflow_is_addressable(repository, workflow.path, workflow.workflow_id):
        raise Unmeasurable(
            f"{workflow.path} in {repository} is not the active workflow {workflow.workflow_id}"
        )

    revision = reader.blob_revision(repository, workflow.path, merge.merge_commit_sha)
    if revision is None:
        raise Unmeasurable(
            f"{workflow.path} did not exist in {repository} at {merge.merge_commit_sha[:8]}"
        )
    attestation = level_of(revision)

    runs = reader.runs_at_head(repository, workflow.path, merge.merge_commit_sha)
    settled_by = merge.merged_at + timedelta(seconds=settle_seconds)

    if not runs:
        return _nothing_ran(
            subject, merge, workflow.path, revision, attestation, early=now < settled_by
        )

    try:
        run = _unanimous(runs)
    except NotSettled as not_yet:
        return _not_settled_yet(subject, str(not_yet), early=now < settled_by)
    if not run.concluded:
        return _not_settled_yet(
            subject, f"run {run.run_id} is still {run.status}", early=now < settled_by
        )

    return _settled(reader, subject, merge, workflow, revision, attestation, run)


def _not_settled_yet(subject: str, why: str, *, early: bool) -> Outcome:
    """A run that has not concluded. Inside the window that is `pending`; outside it, a finding."""
    if early:
        return Outcome(subject, pending=why)
    return Outcome(subject, findings=(Finding(ROLLOUT_STUCK, subject, why),))


def _settled(
    reader: GitHubReader,
    subject: str,
    merge: Merge,
    workflow: RolloutWorkflow,
    revision: str,
    attestation: str,
    run: Run,
) -> Outcome:
    """A rollout run that concluded: read the second axis and decide whether it is a finding."""
    repository = merge.repository
    transcribed = attestation_for(revision)
    job_name = job_conclusion = step_conclusion = None
    drifted: tuple[Finding, ...] = ()
    if transcribed is not None:
        seen = reader.rollout_step(
            repository,
            run.run_id,
            run.run_attempt,
            transcribed.rollout_job,
            transcribed.trigger_step,
        )
        if seen is None:
            # The transcription names a job this run does not have. Reported, and the job name
            # is deliberately NOT carried onto the observation: sending it would assert that the
            # watcher looked at that job and found nothing, which the server reads as evidence.
            drifted = (
                Finding(
                    ROLLOUT_JOB_NOT_FOUND,
                    subject,
                    f"the registry names job {transcribed.rollout_job!r} for revision "
                    f"{revision[:8]}, and run {run.run_id} attempt {run.run_attempt} has no such "
                    f"job — the transcription has drifted from the workflow",
                ),
            )
        else:
            job_name = transcribed.rollout_job
            job_conclusion, step_conclusion = seen
    concurrent = reader.concurrent_rollout_run(repository, workflow.path, run)

    observed = Rollout(
        merge=merge,
        workflow_path=workflow.path,
        workflow_revision=revision,
        attestation=attestation,
        run=run,
        settled=True,
        rollout_job=job_name,
        rollout_job_conclusion=job_conclusion,
        trigger_step=transcribed.trigger_step if transcribed and job_name else None,
        trigger_step_conclusion=step_conclusion,
        concurrent_run_id=concurrent,
    )

    findings: tuple[Finding, ...] = drifted
    exceptions: tuple[Finding, ...] = ()
    if run.conclusion != "success":
        failed = Finding(
            ROLLOUT_NOT_SUCCESS,
            subject,
            f"run {run.run_id} attempt {run.run_attempt} concluded {run.conclusion}"
            + (f"; another rollout run ({concurrent}) overlapped it" if concurrent else "")
            + (
                f"; a green run at {revision[:8]} attests only: {transcribed.attests}"
                if transcribed and attestation != "revision_confirmed"
                else ""
            ),
        )
        assert merge.merge_commit_sha is not None  # `_settled` is only reached for a landing
        moved_on = superseded_by(reader, repository, workflow, merge.merge_commit_sha)
        if moved_on is None:
            findings += (failed,)
        else:
            # ADR-0044. The SAME fact, carried under a kind that says a reader has nothing to do
            # about it, with the excuse spelled out so it can be disputed. Only THIS finding
            # moves: `drifted` is a live transcription defect whatever production is serving.
            exceptions += (Finding(SUPERSEDED_ROLLOUT, subject, f"{failed.detail}; {moved_on}"),)
    return Outcome(subject, rollout=observed, findings=findings, exceptions=exceptions)


def superseded_exception(outcome: Outcome) -> Finding | None:
    """The excuse this pass found for a failed rollout, if it found one.

    THE ONE PLACE THAT KEYS ON `SUPERSEDED_ROLLOUT`, and that is the whole reason it exists. The
    supersession is decided once, in `_settled`; a second consumer needs the answer, and the two
    ways of getting it without asking again are both traps. Reading `outcome.exceptions[0]` keys on
    POSITION, and testing `bool(outcome.exceptions)` keys on this being the only exception kind
    there will ever be -- each correct today and each silently wrong the day a second kind joins.
    Filtering by the kind is correct under both futures, and confining that filter here means the
    kind is named in one module rather than in every consumer.
    """
    return next(
        (finding for finding in outcome.exceptions if finding.kind == SUPERSEDED_ROLLOUT), None
    )


def unclassified(rollout: Rollout) -> bool:
    """Whether nobody has transcribed what a green run of these bytes attests."""
    return rollout.attestation == ATTESTS_UNKNOWN


__all__ = [
    "MERGE_DIVERGENCE",
    "MERGE_TARGETED_ANOTHER_BRANCH",
    "PULL_REQUEST_MISSING",
    "RECHECK_DIVERGENCE",
    "ROLLOUT_ABSENT",
    "ROLLOUT_NOT_SUCCESS",
    "ROLLOUT_JOB_NOT_FOUND",
    "ROLLOUT_STUCK",
    "SETTLE_SECONDS",
    "SUPERSEDED_ROLLOUT",
    "NotSettled",
    "Outcome",
    "ReadError",
    "Unmeasurable",
    "observe",
    "superseded_by",
    "superseded_exception",
    "unclassified",
]
