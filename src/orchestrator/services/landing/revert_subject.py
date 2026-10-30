"""May the SDS's own revert of a bump land? The terms a revert subject is asked in both lanes.

Landing Part 3a wrote these for the inert lane; Part 3b's estate lane asks the same ones of a
revert record's pull request, so they live here once. A revert is not a bump: it is asked none of
a bump's questions (author, ecosystem, hold, reverted version) and these three instead, in order:

1. Is it the one the record names: a `revert_opened` record whose revert this pull request is, on
   that record's own branch, opened by a machine account. The record is what proves the SDS opened
   it. The binding is the pull request NUMBER the record wrote when the SDS opened it, so
   `author_is_bot` need not name which bot: no other account can be the author of that number, and
   the exact-inverse check below decides what it changes whoever pushed to it. (The estate lane
   also asks that the author is one its served conditions permit; the inert lane has none.)
2. Is its head current with its base. Always required here, and asked BEFORE the inverse: a
   merely-behind revert differs from its base on every path the base moved since, and reporting that
   as not-an-inverse would never be freshened.
3. Is its head, against the default branch's head, exactly the inverse of the bump's squash merge
   (`revert_build.is_exact_inverse`), which also refuses a base that moved a touched path.

The default branch's head is read here, separately from the freshness read; if it moves between the
two, the landing meets the same race every landing does.
"""

from __future__ import annotations

from typing import Final

from orchestrator.services.landing.interfaces import (
    EstateGatewayError,
    EstatePullRequest,
    EstateReadGateway,
    Term,
)
from orchestrator.services.landing.revert_build import REVERT_BRANCH_PREFIX, is_exact_inverse
from orchestrator.services.landing.revert_record import RevertSubject
from orchestrator.services.landing.terms import freshness_term

# A pull request from a revert branch (`REVERT_BRANCH_PREFIX`) is a revert subject only when a
# machine account opened it and a `revert_opened` record names it as its revert on that record's
# own branch. Any other one is refused by this name: nobody but the SDS opens those branches, so it
# is a finding.
LANDING_REVERT_NOT_RECORDED: Final = "landing_revert_not_recorded"
# The revert's head, against the default branch's head, no longer changes exactly the paths the
# bump touched back to the bump's parent, or the default branch moved one of them since.
LANDING_REVERT_NOT_EXACT_INVERSE: Final = "landing_revert_not_exact_inverse"
# A tree or the default branch's head could not be read, so the inverse check was not asked.
LANDING_REVERT_UNREADABLE: Final = "landing_revert_unreadable"
# A landing in this repository is on its way to a revert (`revert_record.revert_in_progress`), so
# no other bump lands here until it has: a sibling landed on top of the bad bump would touch the
# paths the revert restores and make it an overlap. Asked of a bump only: the revert subject is
# what it waits for. The landers report it `waiting`, and it clears when the revert lands. The
# branch-update scan counts it releasing, so it never withholds the revert from being freshened.
LANDING_REPOSITORY_REVERTING: Final = "landing_repository_reverting"


def revert_subject_term(
    repository: str,
    pull: EstatePullRequest,
    revert: RevertSubject | None,
    gateway: EstateReadGateway,
) -> Term:
    """May this revert pull request land? See the module docstring for the three questions.

    Every App read here is bounded as every read of the lane asking is: its population and the
    grant term are asked by the caller for the same answer, and nothing here writes -- the one
    write, the landing, happens only on a satisfied answer, which needs the grant.
    """
    expected = f"{REVERT_BRANCH_PREFIX}pr-{revert.pr_number}" if revert is not None else None
    if revert is None or pull.head_ref != expected or not pull.author_is_bot:
        return Term(False, (LANDING_REVERT_NOT_RECORDED,))
    fresh = freshness_term(repository, pull, gateway, required=True)
    if not fresh.met:
        return fresh
    try:
        base = gateway.default_branch_head(repository=repository)
        exact = is_exact_inverse(
            gateway,
            repository,
            revert_head_sha=pull.head_sha,
            base_sha=base.sha,
            merge_sha=revert.merge_commit_sha,
            parent_sha=revert.parent_sha,
        )
    except EstateGatewayError:
        return Term(False, (LANDING_REVERT_UNREADABLE,))
    if not exact:
        return Term(False, (LANDING_REVERT_NOT_EXACT_INVERSE,))
    return Term(True, ())
