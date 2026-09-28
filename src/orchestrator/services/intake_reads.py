"""Read-side queries over a registered package intake and its decomposition proposals.

The JSON API and the review pages both show an intake and its proposals. Each used to run the
same queries itself, against the persistence models, in two copies that could drift. Both now
ask here for the rows and keep only their own shaping.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    DecompositionProposalRetainedAc,
    DecompositionProposalUnit,
    Event,
    PackageAcceptanceCriterion,
)


def revision_acceptance_criteria(
    session: Session, revision_id: uuid.UUID
) -> tuple[PackageAcceptanceCriterion, ...]:
    return tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision_id)
            .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
        )
    )


def acceptance_criteria_by_id(
    session: Session, criterion_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, PackageAcceptanceCriterion]:
    if not criterion_ids:
        return {}
    return {
        criterion.id: criterion
        for criterion in session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.id.in_(criterion_ids))
            .order_by(PackageAcceptanceCriterion.ac_id, PackageAcceptanceCriterion.id)
        )
    }


def intake_authority(session: Session, revision_id: uuid.UUID) -> object:
    """The authority block the intake command declared, or None when no intake event exists."""
    intake_event = session.scalar(
        select(Event)
        .where(
            Event.subject_type == "work_package_revision",
            Event.subject_id == revision_id,
            Event.action == "package_revision.intake_registered",
        )
        .order_by(Event.occurred_at, Event.id)
    )
    command = intake_event.payload.get("command", {}) if intake_event is not None else {}
    return command.get("authority")


@dataclass(frozen=True)
class ProposalChildren:
    """Each proposal's child rows, keyed by proposal id, each list in its display order."""

    units: dict[uuid.UUID, list[DecompositionProposalUnit]]
    dependencies: dict[uuid.UUID, list[DecompositionProposalDependency]]
    mappings: dict[uuid.UUID, list[DecompositionProposalAcMapping]]
    retained: dict[uuid.UUID, list[DecompositionProposalRetainedAc]]


def proposal_children(session: Session, proposal_ids: Sequence[uuid.UUID]) -> ProposalChildren:
    units: dict[uuid.UUID, list[DecompositionProposalUnit]] = defaultdict(list)
    dependencies: dict[uuid.UUID, list[DecompositionProposalDependency]] = defaultdict(list)
    mappings: dict[uuid.UUID, list[DecompositionProposalAcMapping]] = defaultdict(list)
    retained: dict[uuid.UUID, list[DecompositionProposalRetainedAc]] = defaultdict(list)
    if proposal_ids:
        for unit in session.scalars(
            select(DecompositionProposalUnit)
            .where(DecompositionProposalUnit.proposal_id.in_(proposal_ids))
            .order_by(DecompositionProposalUnit.proposal_id, DecompositionProposalUnit.unit_key)
        ):
            units[unit.proposal_id].append(unit)
        for dependency in session.scalars(
            select(DecompositionProposalDependency)
            .where(DecompositionProposalDependency.proposal_id.in_(proposal_ids))
            .order_by(
                DecompositionProposalDependency.proposal_id,
                DecompositionProposalDependency.source_unit_key,
                DecompositionProposalDependency.target_unit_key,
                DecompositionProposalDependency.external_ref,
            )
        ):
            dependencies[dependency.proposal_id].append(dependency)
        for mapping in session.scalars(
            select(DecompositionProposalAcMapping)
            .where(DecompositionProposalAcMapping.proposal_id.in_(proposal_ids))
            .order_by(
                DecompositionProposalAcMapping.proposal_id,
                DecompositionProposalAcMapping.unit_key,
                DecompositionProposalAcMapping.package_acceptance_criterion_id,
            )
        ):
            mappings[mapping.proposal_id].append(mapping)
        for row in session.scalars(
            select(DecompositionProposalRetainedAc)
            .where(DecompositionProposalRetainedAc.proposal_id.in_(proposal_ids))
            .order_by(
                DecompositionProposalRetainedAc.proposal_id,
                DecompositionProposalRetainedAc.package_acceptance_criterion_id,
            )
        ):
            retained[row.proposal_id].append(row)
    return ProposalChildren(units, dependencies, mappings, retained)
