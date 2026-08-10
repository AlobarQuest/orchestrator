"""The facts this program reads, as frozen records. Nothing here decides anything."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# A git object name as GitHub serves it. Anchored, because a prefix match would accept a ref.
SHA = re.compile(r"[0-9a-f]{40}")


def is_sha(value: str | None) -> bool:
    return bool(value) and SHA.fullmatch(str(value)) is not None


@dataclass(frozen=True)
class ChangeRecord:
    """A deploying-merge change as change-manager serves it."""

    item_id: int
    identity: str
    status: str
    target_repository: str
    pull_request_number: int
    acceptance_criteria: tuple[str, ...]
    observed_merge_commit: str | None = None


@dataclass(frozen=True)
class Merge:
    """What GitHub says about a pull request that claims to have landed.

    `merge_commit_sha` is populated on OPEN pull requests too -- GitHub puts a throwaway
    test-merge commit there -- so `merged` is the field that decides, and a reader that trusts
    the sha alone will hand every hop below a commit that is on no branch.
    """

    repository: str
    number: int
    merged: bool
    merge_commit_sha: str | None
    base_ref: str | None
    merged_at: datetime | None


@dataclass(frozen=True)
class Run:
    """One workflow run at a head, reduced to what an observation records.

    `concluded_at` is GitHub's `updated_at`, which is the closest thing a run object has to a
    finish time and which keeps moving afterwards. It is carried as context and is deliberately
    NOT part of what `recheck` compares: a field that changes on its own trains its reader to
    ignore divergence.
    """

    run_id: int
    run_attempt: int
    run_url: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    concluded_at: datetime | None

    @property
    def concluded(self) -> bool:
        """A run has settled when GitHub says it completed AND names how.

        `status` is one of queued / in_progress / waiting / requested / pending / completed, and
        `conclusion` is documented nullable — so both halves are required, and anything else is
        `pending`: come back, not a verdict.
        """
        return self.status == "completed" and bool(self.conclusion)


@dataclass(frozen=True)
class Rollout:
    """Everything observed about the rollout one landed pull request caused.

    `run` is None in exactly two situations and they are not the same answer: the workflow has
    not produced a run at this head yet (`settled` False -- come back), or it never will
    (`settled` True -- a finding). The caller distinguishes them; this record only carries them.
    """

    merge: Merge
    workflow_path: str
    workflow_revision: str | None
    attestation: str
    run: Run | None
    settled: bool
    rollout_job: str | None = None
    rollout_job_conclusion: str | None = None
    trigger_step: str | None = None
    trigger_step_conclusion: str | None = None
    concurrent_run_id: int | None = None


@dataclass(frozen=True)
class Finding:
    """Something the pass found. Reported, never acted on."""

    kind: str
    subject: str
    detail: str
