"""What a deploying merge must be held to, and how it would be undone.

ADR-0019 requires a change record to carry acceptance criteria and a rollback plan, and increment
1 refuses a record without them. This module derives both rather than asking an author for them,
because a rule requiring hand-written fields is incompatible with anything unattended.

**The acceptance criteria are TRANSCRIBED, never aspirational.** They are read from
`deploy_watcher.workflows`, whose registry records what a green rollout run at those exact bytes
actually attests -- a judgment made per revision, and one adversarial review already corrected
once by measurement. Writing "production serves the merged commit" into a record for a repository
whose workflow only proves *a webhook returned 2xx* would be a record that lies, and it would lie
in the direction that matters: the rollback plan is the remedy attached to these criteria, so
criteria nobody checks are a remedy nobody triggers.

**An unclassified workflow revision refuses.** `attestation_for` returns `None` for bytes nobody
transcribed, and this module turns that into a refusal rather than a weaker criterion. A record
whose criteria were guessed is worse than no record, because the guess is what a later reader
would hold the deploy to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from deploy_watcher.workflows import ATTESTS_REVISION, Attestation


@dataclass(frozen=True)
class Rollback:
    """How this repository's production is put back, and why it is spelled this way.

    `always_revert` is not a preference. Re-pointing an image tag without reverting the commit
    leaves `main` and production disagreeing silently, and the next unrelated merge re-deploys the
    broken code -- ADR-0019 names this and it is the reason the fast path is never the whole plan.
    """

    steps: tuple[str, ...]
    target: str


# Keyed by repository, lowercased. Fail closed: a repository with no entry gets no record, because
# a rollback plan invented at proposal time is a plan nobody has tried.
#
# Both repositories push a per-SHA tag alongside a moving tag and run the moving one, so the fast
# path is to re-point the moving tag. They differ in WHAT the rollback target has to be, and the
# difference is load-bearing rather than stylistic:
#
#   * `brain` builds from `requirements.txt` with NO lockfile, so rebuilding the same commit can
#     resolve a different dependency set. The target is therefore the IMAGE. ADR-0019 states this
#     explicitly and it is the one place the two repositories cannot share a plan.
#   * `change-manager` is uv-locked, so a commit identifies its dependencies and either target
#     works; the image is still named, because it is faster and does not wait on a build.
_ROLLBACKS: Final[dict[str, Rollback]] = {
    "alobarquest/change-manager": Rollback(
        steps=(
            "re-point the moving image tag at the previous per-SHA tag and redeploy",
            "revert the merge commit on main, so main and production agree again",
        ),
        target="image",
    ),
    "alobarquest/brain": Rollback(
        steps=(
            "re-point each affected app's moving image tag at the previous per-SHA tag "
            "and redeploy",
            "revert the merge commit on main, so main and production agree again",
        ),
        # Not "commit": brain has no lockfile, so the same commit can rebuild to a different
        # dependency set. Rolling back to a commit would be rolling forward into an untested one.
        target="image",
    ),
}


class CriteriaUnavailable(Exception):
    """Nothing here can honestly say what this merge must be held to, so nothing is proposed."""


def rollback_for(repository: str) -> Rollback:
    """The plan for this repository, matched case-insensitively.

    GitHub answers `AlobarQuest/change-manager` while this table is keyed lowercase, and
    `_consider` already folds case for the workflow lookup — so a case-sensitive match here would
    make the module tolerate GitHub's own casing for one lookup and refuse it for the other. A
    mutation pass found the inconsistency; no control passed mixed case.
    """
    plan = _ROLLBACKS.get(repository.lower())
    if plan is None:
        raise CriteriaUnavailable(
            f"no rollback plan is transcribed for {repository}; "
            "a plan invented now is a plan nobody has tried"
        )
    return plan


def acceptance_criteria(repository: str, attestation: Attestation | None) -> tuple[str, ...]:
    """What a green rollout of THIS repository, at THESE workflow bytes, would actually prove.

    Two criteria, and the second one is the one a person would forget. The first is what the
    workflow attests; the second is that it ran at all, which is a separate fact -- a rollout that
    never fired leaves production on the previous build with `main` already moved, and that is the
    divergence the rollback plan exists to close.
    """
    if attestation is None:
        raise CriteriaUnavailable(
            f"the rollout workflow revision for {repository} is not transcribed, so what a green "
            "run would prove is unknown; refusing to guess"
        )
    criteria = [
        f"the rollout runs for this merge on {repository}, and its production step concludes "
        f"success (job '{attestation.rollout_job}', step '{attestation.trigger_step}')",
        attestation.attests,
    ]
    if attestation.level != ATTESTS_REVISION:
        # Said out loud in the record rather than left for a reader to infer from the level, so
        # the ceiling on what this record can ever confirm travels with it.
        criteria.append(
            "NOTE: at this workflow revision a green rollout does NOT prove the merged build is "
            "the one serving production; confirm the running revision by hand before relying on "
            "these criteria having held"
        )
    return tuple(criteria)
