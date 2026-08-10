"""What one landing is, as this adapter reads it from GitHub.

A LANDING is a commit that reached a repository's default branch by any route. FOUR routes exist
in this estate and their permission bases differ, which is the whole reason the ledger records a
basis rather than a boolean. The fourth arrived on 2026-08-10, when the factory landed its own
pull request for the first time (ADR-0020).
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
class FactoryClaim:
    """What a landing SAYS about the work unit behind it -- a hint, never evidence.

    Read from the `SDS-Unit:` / `SDS-Package-Rev:` trailers factory-runner writes into the COMMIT
    MESSAGE, which is the same place `UpdateMetadata` reads Dependabot's trailers from and is
    chosen for the same reason: a commit message is immutable, while a pull-request body -- which
    carries the identical values plus the authority fingerprint -- can be edited after the landing
    and would make an unchanged reality re-encode to different facts on every pass.

    It selects WHICH unit to ask the orchestrator about and nothing more. Everything it asserts is
    written by the runner, so the ledger records it as a claim and the audit checks it against the
    orchestrator's own durable record (ADR-0020; `audit.audit_factory_landing`).
    """

    work_unit: str
    package_revision: int | None = None


@dataclass(frozen=True)
class PendingUpdate:
    """An OPEN pull request from the upstream update bot -- a landing that has not happened.

    The ledger records landings, so a pull request that never merges leaves no trace in it at
    all. That absence is the quiet failure this shape exists to make visible, and it is why it
    is read from GitHub rather than from the ledger.

    `armed` is whether the repository has been asked to land this pull request once its required
    checks pass. `checks` is every job that has CONCLUDED at the head -- the same standard the
    landing record uses, and the same disclaimer applies: it is not a claim that any of them was
    required. `last_concluded_at` is the newest of those conclusions, so a caller can tell a pull
    request that has been sitting green for a day from one whose checks finished a second ago.
    """

    repository: str
    number: int
    head_commit: str
    opened_at: datetime
    armed: bool
    title: str
    checks: tuple[Check, ...] = ()
    update: UpdateMetadata | None = None
    last_concluded_at: datetime | None = None


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
    claim: FactoryClaim | None = None
