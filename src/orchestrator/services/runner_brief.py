from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposalAcMapping,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.packages import evaluate_readiness


def runner_brief(session: Session, unit_id: UUID) -> dict[str, object]:
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    criteria = _criteria_for_unit(session, revision.id, unit.unit_key)
    readiness = evaluate_readiness(session, unit.id, for_update=False)
    target_repository = str(unit.authority.get("constraints", {}).get("target_repository", ""))
    if not target_repository:
        raise DomainError(
            "runner_target_missing",
            "work unit authority does not declare constraints.target_repository",
            None,
        )

    return {
        "work_unit": {
            "id": unit.id,
            "state": unit.state,
            "version": unit.version,
            "title": unit.title,
            "outcome": unit.outcome,
            "required_capability": unit.required_capability,
            "max_attempts": unit.max_attempts,
        },
        "package": {
            "id": revision.work_package.package_id,
            "revision_id": revision.id,
            "revision": revision.revision,
            "content_hash": revision.content_hash,
            "source_repository": revision.work_package.source_repository,
            "source_path": revision.source_path,
            "source_commit": revision.source_commit,
        },
        "authority": {
            "fingerprint": unit.authority_fingerprint,
            "envelope": unit.authority,
        },
        "acceptance_criteria": [
            {
                "id": criterion.id,
                "ac_id": criterion.ac_id,
                "condition": criterion.condition,
                "evidence_type": criterion.evidence_type,
                "evidence": criterion.evidence,
                "approver": criterion.approver,
            }
            for criterion in criteria
        ],
        "readiness": {
            "status": readiness.status,
            "reasons": [
                {"code": reason.code, "subject_id": reason.subject_id, "detail": reason.detail}
                for reason in readiness.reasons
            ],
        },
        "target": {"repository": target_repository},
        "standing_context": revision.enforcement_snapshot.get("required_context", {}),
    }


def _criteria_for_unit(
    session: Session, revision_id: UUID, unit_key: str
) -> tuple[PackageAcceptanceCriterion, ...]:
    approved = session.scalar(
        select(ApprovedDecomposition).where(
            ApprovedDecomposition.work_package_revision_id == revision_id,
            ApprovedDecomposition.superseded_at.is_(None),
        )
    )
    if approved is None:
        return tuple(
            session.scalars(
                select(PackageAcceptanceCriterion)
                .where(PackageAcceptanceCriterion.work_package_revision_id == revision_id)
                .order_by(PackageAcceptanceCriterion.ac_id)
            )
        )
    mapped = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .join(
                DecompositionProposalAcMapping,
                DecompositionProposalAcMapping.package_acceptance_criterion_id
                == PackageAcceptanceCriterion.id,
            )
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision_id)
            .where(DecompositionProposalAcMapping.proposal_id == approved.proposal_id)
            .where(DecompositionProposalAcMapping.unit_key == unit_key)
            .order_by(PackageAcceptanceCriterion.ac_id)
        )
    )
    return mapped
