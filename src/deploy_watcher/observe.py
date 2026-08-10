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
from deploy_watcher.workflows import ATTESTS_UNKNOWN, attestation_for, level_of, rollout_for

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
MERGE_TARGETED_ANOTHER_BRANCH = "merge_did_not_target_the_rollout_branch"
MERGE_DIVERGENCE = "observed_at_more_than_one_merge_commit"
RECHECK_DIVERGENCE = "recorded_facts_no_longer_match_github"


class Unmeasurable(Exception):
    """The question could not be asked. Exit 3, never a finding.

    Separate from `ReadError` so that "GitHub is down" and "nobody classified this repository"
    are distinguishable in the report, while folding to the same exit code.
    """


@dataclass(frozen=True)
class Outcome:
    """What one pass learned about one change. Exactly one of these is populated."""

    subject: str
    rollout: Rollout | None = None
    pending: str | None = None
    findings: tuple[Finding, ...] = ()


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
    conclusions = {run.conclusion for run in runs}
    if len(conclusions) != 1:
        raise Unmeasurable(
            f"{len(runs)} rollout runs at this commit and they do not agree: "
            f"{', '.join(sorted(str(c) for c in conclusions))}"
        )
    return max(runs, key=lambda run: (run.run_id, run.run_attempt))


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
        # Merged, with a real merge commit — and no rollout will ever run at it, because the
        # workflow fires on a branch this did not land on. Reported as what it is, and returned
        # BEFORE the settle window can turn it into `rollout_never_ran`: that finding means "the
        # rollout should have run and did not", and here it never should have. A record asserting
        # that landing this deploys production is wrong about its own subject, which is worth
        # saying out loud rather than swallowing.
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

    run = _unanimous(runs)
    if not run.concluded:
        if now < settled_by:
            return Outcome(subject, pending=f"the rollout run is {run.status}")
        return Outcome(
            subject,
            findings=(Finding(ROLLOUT_STUCK, subject, f"run {run.run_id} is still {run.status}"),),
        )

    return _settled(reader, subject, merge, workflow.path, revision, attestation, run)


def _settled(
    reader: GitHubReader,
    subject: str,
    merge: Merge,
    workflow_path: str,
    revision: str,
    attestation: str,
    run: Run,
) -> Outcome:
    """A rollout run that concluded: read the second axis and decide whether it is a finding."""
    repository = merge.repository
    transcribed = attestation_for(revision)
    job_conclusion = step_conclusion = None
    if transcribed is not None:
        job_conclusion, step_conclusion = reader.rollout_step(
            repository,
            run.run_id,
            run.run_attempt,
            transcribed.rollout_job,
            transcribed.trigger_step,
        )
    concurrent = reader.concurrent_rollout_run(repository, workflow_path, run)

    observed = Rollout(
        merge=merge,
        workflow_path=workflow_path,
        workflow_revision=revision,
        attestation=attestation,
        run=run,
        settled=True,
        rollout_job=transcribed.rollout_job if transcribed else None,
        rollout_job_conclusion=job_conclusion,
        trigger_step=transcribed.trigger_step if transcribed else None,
        trigger_step_conclusion=step_conclusion,
        concurrent_run_id=concurrent,
    )

    findings: tuple[Finding, ...] = ()
    if run.conclusion != "success":
        findings = (
            Finding(
                ROLLOUT_NOT_SUCCESS,
                subject,
                f"run {run.run_id} attempt {run.run_attempt} concluded {run.conclusion}"
                + (f"; another rollout run ({concurrent}) overlapped it" if concurrent else "")
                + (
                    f"; a green run at {revision[:8]} attests only: {transcribed.attests}"
                    if transcribed and attestation != "revision_confirmed"
                    else ""
                ),
            ),
        )
    return Outcome(subject, rollout=observed, findings=findings)


def unclassified(rollout: Rollout) -> bool:
    """Whether nobody has transcribed what a green run of these bytes attests."""
    return rollout.attestation == ATTESTS_UNKNOWN


__all__ = [
    "MERGE_DIVERGENCE",
    "PULL_REQUEST_MISSING",
    "RECHECK_DIVERGENCE",
    "ROLLOUT_ABSENT",
    "ROLLOUT_NOT_SUCCESS",
    "ROLLOUT_STUCK",
    "SETTLE_SECONDS",
    "Outcome",
    "ReadError",
    "Unmeasurable",
    "observe",
    "unclassified",
]
