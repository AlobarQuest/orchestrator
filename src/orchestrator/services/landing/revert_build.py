"""Build a revert pull request that is exactly the inverse of a squash merge. Landing Part 3a.

Landings are squash merges, so the default branch without the bump is the merge commit's single
parent, and the inverse of the bump is that parent's entry on every path the merge touched: restored
where the parent has it, deleted where it does not. Built through the git data API from trees, so
nothing is checked out and nothing merges.

## Touched paths come from trees, not from the comparison API

The parent's and the merge commit's recursive trees, compared leaf by leaf as (mode, type, sha). A
rename falls out as the old path restored and the new one deleted; a mode change alone (an
executable bit) or a submodule moving counts as touched; and nothing is capped, where the
comparison API lists at most 300 files and a short list would hide a path from the check below.

## A revert never removes more than the bump

If any touched path differs between the merge commit and the default branch's current head,
something landed on top of the bump there, and restoring the parent's entry would undo that too.
That is an `Overlap`, and nothing is built.

## Every call is bounded by the inert lane's grant term

These functions act through the Dispatch App, which can reach more repositories than it works on.
Their only caller is the revert route (Landing Part 3a, Task 5), which asks the inert lane's grant
term first; the landing-time check runs inside the revert subject's admission, after the same term.
They are private until that caller exists (`test_unreachable_guards`), except `is_exact_inverse`,
which the inert lane's admission asks of a revert subject (Task 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from orchestrator.services.landing.interfaces import (
    EstateGatewayError,
    RevertBuildGateway,
    RevertReadGateway,
    TreeChange,
    TreeEntry,
)

# Every revert branch starts with this, and only revert branches do: the inert lane's revert
# subject is recognised by it (Task 4). The branch is `sds-revert/pr-<pr_number>`.
REVERT_BRANCH_PREFIX: Final = "sds-revert/"

# Raised when the merge commit's tree equals its parent's: there is nothing to invert, and a commit
# that changes nothing is not a revert.
REVERT_TOUCHED_NOTHING: Final = "revert_touched_nothing"

# Raised before any write when the revert would name no failed job: a revert says why it exists.
REVERT_NAMES_NO_FAILED_JOB: Final = "revert_names_no_failed_job"

# GitHub refuses a pull request title longer than this, in characters.
_TITLE_LIMIT: Final = 256
_TITLE_ELISION: Final = "\u2026"


@dataclass(frozen=True)
class Overlap:
    """Touched paths that changed on the default branch after the landing, sorted."""

    paths: tuple[str, ...]


@dataclass(frozen=True)
class RevertPlan:
    """What `_open_revert` writes: the parent's entries on the touched paths, on top of the head.

    `base_ref` is the default branch the pull request targets, `base_sha` its head when planned (the
    new commit's parent) and `base_tree_sha` that head's tree (the new tree's `base_tree`).
    `changes` is sorted by path.
    """

    base_ref: str
    base_sha: str
    base_tree_sha: str
    merge_sha: str
    changes: tuple[TreeChange, ...]


@dataclass(frozen=True)
class RevertOf:
    """The pull request being reverted, as the revert's title and message name it."""

    number: int
    title: str
    failed_job_names: tuple[str, ...]


def _touched(before: dict[str, TreeEntry], after: dict[str, TreeEntry]) -> set[str]:
    """Every path whose entry differs between two trees, including one present in only one."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def _plan_revert(
    gateway: RevertBuildGateway, repository: str, merge_sha: str, parent_sha: str
) -> RevertPlan | Overlap:
    """The inverse of the squash merge `merge_sha` on the default branch's current head, or the
    touched paths that changed since it landed.

    Raises `EstateGatewayError` when a read fails, or with `REVERT_TOUCHED_NOTHING` when the merge
    changed nothing.
    """
    parent = gateway.tree(repository=repository, commit_sha=parent_sha).entries
    merged = gateway.tree(repository=repository, commit_sha=merge_sha).entries
    touched = sorted(_touched(parent, merged))
    if not touched:
        raise EstateGatewayError(REVERT_TOUCHED_NOTHING)
    head = gateway.default_branch_head(repository=repository)
    head_tree = gateway.tree(repository=repository, commit_sha=head.sha)
    moved = tuple(path for path in touched if head_tree.entries.get(path) != merged.get(path))
    if moved:
        return Overlap(paths=moved)
    return RevertPlan(
        base_ref=head.name,
        base_sha=head.sha,
        base_tree_sha=head_tree.sha,
        merge_sha=merge_sha,
        changes=tuple(_restore(path, parent, merged) for path in touched),
    )


def _restore(path: str, parent: dict[str, TreeEntry], merged: dict[str, TreeEntry]) -> TreeChange:
    """The parent's entry, or a delete carrying the merge tree's mode and type.

    A touched path the parent lacks is one the merge added, so the merge tree has it.
    """
    before = parent.get(path)
    if before is not None:
        return TreeChange(path=path, mode=before.mode, type=before.type, sha=before.sha)
    added = merged[path]
    return TreeChange(path=path, mode=added.mode, type=added.type, sha=None)


def _open_revert(
    gateway: RevertBuildGateway, repository: str, plan: RevertPlan, original: RevertOf
) -> tuple[int, str]:
    """Write the plan as a tree, a commit on the planned head, the revert branch and its pull
    request. Answers the pull request's number and head.

    Four writes, not atomic: a failure after the branch exists leaves it behind, and a second call
    for the same pull request is refused when the branch is created. The caller records the act once
    either way.

    Everything that can refuse is decided before the first write: a revert with no failed job
    raises `REVERT_NAMES_NO_FAILED_JOB`, and a title that would pass GitHub's limit is shortened.
    """
    if not original.failed_job_names:
        raise EstateGatewayError(REVERT_NAMES_NO_FAILED_JOB)
    title = _revert_title(original.title)
    body = (
        f"Reverts #{original.number}: {', '.join(original.failed_job_names)} "
        f"failed on {plan.merge_sha}."
    )
    tree = gateway.create_tree(
        repository=repository, base_tree=plan.base_tree_sha, changes=plan.changes
    )
    commit = gateway.create_commit(
        repository=repository, message=f"{title}\n\n{body}", tree=tree, parent=plan.base_sha
    )
    branch = f"{REVERT_BRANCH_PREFIX}pr-{original.number}"
    gateway.create_branch(repository=repository, branch=branch, sha=commit)
    number = gateway.open_pull_request(
        repository=repository, title=title, body=body, head=branch, base=plan.base_ref
    )
    return number, commit


def _revert_title(title: str) -> str:
    """`Revert "<title>"`, the original title elided so the whole fits GitHub's limit."""
    room = _TITLE_LIMIT - len('Revert ""')
    if len(title) > room:
        title = title[: room - len(_TITLE_ELISION)] + _TITLE_ELISION
    return f'Revert "{title}"'


def is_exact_inverse(
    gateway: RevertReadGateway,
    repository: str,
    revert_head_sha: str,
    base_sha: str,
    merge_sha: str,
    parent_sha: str,
) -> bool:
    """Whether the revert's head, against `base_sha`, changes exactly the merge's touched paths,
    each to the parent's entry (absent where the parent lacks it), while the base still holds the
    merge's entry on each of them.

    `base_sha` is the default branch's head the revert would land on, which the caller already reads
    for freshness. It is passed rather than derived from the revert's own parents, because a branch
    update makes the head a two-parent merge and neither parent is reliably the base.

    Raises `EstateGatewayError` when a read fails, so the caller can refuse it by its own name.
    """
    parent = gateway.tree(repository=repository, commit_sha=parent_sha).entries
    merged = gateway.tree(repository=repository, commit_sha=merge_sha).entries
    touched = _touched(parent, merged)
    base = gateway.tree(repository=repository, commit_sha=base_sha).entries
    revert = gateway.tree(repository=repository, commit_sha=revert_head_sha).entries
    if _touched(base, revert) != touched:
        return False
    # The base must still hold the merge's entry on every touched path. Otherwise something landed
    # on top there, and a revert hand-resolved back to the parent's entry would discard it: the
    # overlap `_plan_revert` refuses, asked again at landing time.
    return all(
        base.get(path) == merged.get(path) and revert.get(path) == parent.get(path)
        for path in touched
    )
