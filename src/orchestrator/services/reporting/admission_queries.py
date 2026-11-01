"""What waits on a person before a unit exists. One copy, read by the queue and the alert lane.

`pending_decisions` shapes these rows for the `/review` queue, and `execution_status` serves them to
`execution-watch`, which drops what its own rules exclude. So a filter belongs here only when both
readers want it.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    StagedPackageIntake,
    WorkPackageRevision,
)
from orchestrator.services.intake.staged_intake import STAGED


def staged_intakes(session: Session) -> Sequence[StagedPackageIntake]:
    return session.scalars(
        select(StagedPackageIntake)
        .where(StagedPackageIntake.state == STAGED)
        .order_by(StagedPackageIntake.staged_at, StagedPackageIntake.id)
    ).all()


def undecomposed_revisions(session: Session) -> Sequence[WorkPackageRevision]:
    """Revisions registered through the CLI intake with no active approval and no undecided
    proposal, and no later revision of the same package: a replaced revision is nobody's to decide.
    `manual_ws31` and `protocol_fixture` revisions can never be a person's to decide."""
    active_approved = select(ApprovedDecomposition.work_package_revision_id).where(
        ApprovedDecomposition.superseded_at.is_(None)
    )
    undecided_proposals = select(DecompositionProposal.work_package_revision_id).where(
        DecompositionProposal.state == "proposed"
    )
    later = aliased(WorkPackageRevision)
    return session.scalars(
        select(WorkPackageRevision)
        .where(
            WorkPackageRevision.intake_source == "package_cli",
            ~select(later.id)
            .where(
                later.work_package_id == WorkPackageRevision.work_package_id,
                later.revision > WorkPackageRevision.revision,
            )
            .exists(),
            WorkPackageRevision.id.not_in(active_approved),
            WorkPackageRevision.id.not_in(undecided_proposals),
        )
        .order_by(WorkPackageRevision.registered_at, WorkPackageRevision.id)
    ).all()


def proposed_breakdowns(session: Session) -> Sequence[DecompositionProposal]:
    return session.scalars(
        select(DecompositionProposal)
        .where(DecompositionProposal.state == "proposed")
        .order_by(DecompositionProposal.proposed_at, DecompositionProposal.id)
    ).all()
