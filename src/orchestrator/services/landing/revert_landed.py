"""A revert the SDS opened has landed: record it, and hold the dependency. Landing Parts 3a and 3b.

Both landing lanes' merge paths call this as their `on_landed`, in the transaction that records the
landing: the inert lane's (Part 3a) and the estate lane's, whose landings redeploy (Part 3b). It is
the one caller of `revert_hold.place`, the member record's one automatic writer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorContext
from orchestrator.persistence.models import EstatePrMerge, LandingRevert
from orchestrator.services.landing.revert_record import REVERT_LANDED, REVERT_OPENED
from orchestrator.services.membership.revert_hold import place as place_revert_hold


def record_revert_landed(session: Session, record: EstatePrMerge, actor: ActorContext) -> None:
    """If the pull request that just landed is a revert the SDS opened, record it landed and hold
    the dependency, in the transaction that records the landing.

    The record is the `revert_opened` one naming this pull request as its revert -- the same record
    the admission found -- read under the landing's repository lock, which is already held. Every
    other landing finds none and does nothing. On `already_merged` the commit is the confirming
    re-read's; it stays empty only when that read named none.
    """
    revert = session.scalar(
        select(LandingRevert)
        .where(
            LandingRevert.repository == record.repository,
            LandingRevert.revert_pr_number == record.pr_number,
            LandingRevert.status == REVERT_OPENED,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if revert is None:
        return
    revert.status = REVERT_LANDED
    revert.revert_merge_sha = record.merge_commit_sha
    revert.updated_at = datetime.now(UTC)
    revert.hold_outcome = place_revert_hold(session, revert, actor=actor)
