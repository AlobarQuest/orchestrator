"""What one landing is, as this adapter reads it from GitHub.

A LANDING is a commit that reached a repository's default branch by any route. Three routes exist
in this estate and their permission bases differ, which is the whole reason the ledger records a
basis rather than a boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Check:
    """One workflow job that had concluded at the pull request's head before it landed.

    This is NOT a claim that the job was REQUIRED. GitHub keeps no history of a branch's
    required-status-check list, so which of these actually gated a past landing is unknowable
    after the fact, and a record asserting it would be a reconstruction of the kind ADR-0014
    names. `name` is the JOB name, never the workflow name -- branch protection matches job
    names, and the two differ (`Quality` the workflow, `Lint, type-check, and test` the job).
    """

    name: str
    conclusion: str
    run: int


@dataclass(frozen=True)
class RuleApplication:
    """The gate workflow that ran on the pull request, pinned to the exact bytes that applied.

    `revision` is the file's git blob sha AT THE LANDING COMMIT, not at the branch tip. The rule
    changes -- intent-packages' gate was 1530 bytes on 2026-08-07 at 12:42 and 3202 bytes four
    hours later -- so a record that does not pin the version cannot say which rule it was.
    """

    path: str
    revision: str
    run: int
    outcome: str


@dataclass(frozen=True)
class UpdateMetadata:
    """The three values the gate's condition is written against.

    Read from the `updated-dependencies` trailer Dependabot writes into the commit message, which
    is what `dependabot/fetch-metadata` itself parses -- not from the pull request title. The
    ecosystem is the second segment of the branch name, which is where fetch-metadata gets it and
    why it spells it `github_actions` with an underscore.
    """

    dependency: str
    ecosystem: str | None
    update_type: str


@dataclass(frozen=True)
class Landing:
    """One commit on the default branch, with everything observable about how it got there.

    `pull_request is None` means it was pushed straight at the branch. That case has no
    permission basis at all, and surfacing it is the point.
    """

    repository: str
    base_ref: str
    commit: str
    landed_at: datetime
    title: str
    files: tuple[str, ...]
    files_changed: int
    pull_request: int | None = None
    head_commit: str | None = None
    landed_by: str | None = None
    checks: tuple[Check, ...] = ()
    rule: RuleApplication | None = None
    update: UpdateMetadata | None = None
