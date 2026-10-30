"""What the landing admissions read from the revert record. Landing Part 3a.

Four questions, each asked of `landing_reverts` for one repository (stored lower-cased): is this
pull request a revert the SDS opened, which versions has the SDS reverted from the default branch,
and is a revert in progress there; and, for every repository, which reverts are open (Part 3b).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert
from orchestrator.services.landing.revert_watch import WATCH_HOURS
from orchestrator.services.membership.holds import Bump

# The record statuses these reads key on.
REVERT_OPENED: Final = "revert_opened"
REVERT_LANDED: Final = "revert_landed"
RERUN_REQUESTED: Final = "rerun_requested"
# Landing Part 3b: production points at the bump's parent's image and the revert is not yet open;
# and, after the revert landed, the moving tag is back. A restored revert has landed too: every
# read of a landed revert reads both.
REPOINTED: Final = "repointed"
RESTORED: Final = "restored"
LANDED_REVERTS: Final = (REVERT_LANDED, RESTORED)


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


@dataclass(frozen=True)
class OpenedRevert:
    """A revert the SDS opened and has not landed: the bump's pull request and the revert's."""

    repository: str
    pr_number: int
    revert_pr_number: int


def opened_reverts(session: Session) -> tuple[OpenedRevert, ...]:
    """Every `revert_opened` record, ordered. Landing Part 3b: `change_proposer` files a revert
    pull request's change record only when one of these names it, so a pull request on a revert
    branch that no record names is never filed as anything. Database rows only: no App read.
    """
    rows = session.scalars(
        select(LandingRevert)
        .where(
            LandingRevert.status == REVERT_OPENED,
            LandingRevert.revert_pr_number.is_not(None),
        )
        .order_by(LandingRevert.repository, LandingRevert.pr_number)
    )
    return tuple(
        OpenedRevert(row.repository, row.pr_number, row.revert_pr_number)
        for row in rows
        if row.revert_pr_number is not None
    )


def reverted_versions(session: Session, repository: str) -> tuple[Bump, ...]:
    """Every bump a landed revert took back out of this repository, at the version it brought.

    An entry missing its version reads as empty, which matches every version of its dependency
    (`terms.version_reverted_term`): a damaged record refuses that dependency rather than admits.
    """
    rows = session.scalars(
        select(LandingRevert).where(
            LandingRevert.repository == repository.lower(),
            LandingRevert.status.in_(LANDED_REVERTS),
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


def revert_in_progress(session: Session, repository: str) -> bool:
    """Is a landing in this repository on its way to a revert: a rerun asked for and still in the
    watch, production pointed back at the build before the bump (`repointed`, Part 3b), or a revert
    opened and not yet landed?

    A `rerun_requested` record counts only while it is in the watch (`WATCH_HOURS` since it last
    changed), as `revert_act` keeps it: a rerun that passed leaves its record in that status for
    good, so an unbounded match would hold the repository forever after one flaky job. A
    `revert_opened` record counts however old, as the watch keeps it.
    """
    window = func.now() - timedelta(hours=WATCH_HOURS)
    return bool(
        session.scalar(
            select(
                exists().where(
                    LandingRevert.repository == repository.lower(),
                    or_(
                        LandingRevert.status.in_((REVERT_OPENED, REPOINTED)),
                        (LandingRevert.status == RERUN_REQUESTED)
                        & (LandingRevert.updated_at >= window),
                    ),
                )
            )
        )
    )
