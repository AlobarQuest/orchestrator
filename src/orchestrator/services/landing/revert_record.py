"""What the landing admissions read from the revert record. Landing Part 3a.

Two questions, each asked of `landing_reverts` for one repository (stored lower-cased): is this
pull request a revert the SDS opened, and which versions has the SDS reverted from the default
branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert
from orchestrator.services.membership.holds import Bump

# The record statuses these reads key on.
REVERT_OPENED: Final = "revert_opened"
REVERT_LANDED: Final = "revert_landed"


@dataclass(frozen=True)
class RevertSubject:
    """A revert pull request the SDS opened: the bump it inverts, by number, squash merge and the
    merge's parent."""

    pr_number: int
    merge_commit_sha: str
    parent_sha: str


def recorded_revert(session: Session, repository: str, pr_number: int) -> RevertSubject | None:
    """The `revert_opened` record naming this pull request as its revert, or None."""
    row = session.scalar(
        select(LandingRevert).where(
            LandingRevert.repository == repository.lower(),
            LandingRevert.revert_pr_number == pr_number,
            LandingRevert.status == REVERT_OPENED,
        )
    )
    if row is None:
        return None
    return RevertSubject(row.pr_number, row.merge_commit_sha, row.parent_sha)


def reverted_versions(session: Session, repository: str) -> tuple[Bump, ...]:
    """Every bump a landed revert took back out of this repository, at the version it brought.

    An entry missing its version reads as empty, which matches every version of its dependency
    (`terms.version_reverted_term`): a damaged record refuses that dependency rather than admits.
    """
    rows = session.scalars(
        select(LandingRevert).where(
            LandingRevert.repository == repository.lower(),
            LandingRevert.status == REVERT_LANDED,
        )
    )
    return tuple(
        Bump(
            ecosystem=str(entry.get("ecosystem") or ""),
            directory=entry.get("directory"),
            dependency=str(entry.get("dependency") or ""),
            to_version=str(entry.get("to_version") or ""),
        )
        for row in rows
        for entry in row.bumps
    )
