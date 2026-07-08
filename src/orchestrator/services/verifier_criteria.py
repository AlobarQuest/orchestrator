from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposalAcMapping,
    DeploymentObservation,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)


def load_required_criteria(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> tuple[PackageAcceptanceCriterion, ...]:
    generated = _generated_post_deploy_criteria(session, unit, revision)
    if generated is not None:
        return generated

    has_approved_decomposition = (
        session.execute(
            select(ApprovedDecomposition.id)
            .where(
                ApprovedDecomposition.work_package_revision_id == revision.id,
                ApprovedDecomposition.superseded_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    if has_approved_decomposition:
        return tuple(
            session.scalars(
                select(PackageAcceptanceCriterion)
                .join(
                    DecompositionProposalAcMapping,
                    DecompositionProposalAcMapping.package_acceptance_criterion_id
                    == PackageAcceptanceCriterion.id,
                )
                .join(
                    ApprovedDecomposition,
                    ApprovedDecomposition.proposal_id == DecompositionProposalAcMapping.proposal_id,
                )
                .where(
                    ApprovedDecomposition.work_package_revision_id == revision.id,
                    ApprovedDecomposition.superseded_at.is_(None),
                    PackageAcceptanceCriterion.work_package_revision_id == revision.id,
                    DecompositionProposalAcMapping.unit_key == unit.unit_key,
                )
                .order_by(PackageAcceptanceCriterion.ac_id)
            )
        )

    ac_ids = revision.enforcement_snapshot.get("acceptance_criteria")
    if not isinstance(ac_ids, list) or not ac_ids:
        raise DomainError(
            "verification_subject_invalid",
            "package revision has no required acceptance criteria",
            None,
        )
    if not all(isinstance(ac_id, str) and ac_id.strip() for ac_id in ac_ids):
        raise DomainError(
            "verification_subject_invalid",
            "package revision acceptance criteria are malformed",
            None,
        )
    criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(
                PackageAcceptanceCriterion.work_package_revision_id == revision.id,
                PackageAcceptanceCriterion.ac_id.in_(ac_ids),
            )
            .order_by(PackageAcceptanceCriterion.ac_id)
        )
    )
    if len(criteria) != len(ac_ids):
        raise DomainError(
            "verification_subject_invalid",
            "package revision acceptance criteria rows are incomplete",
            None,
        )
    return criteria


def _generated_post_deploy_criteria(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> tuple[PackageAcceptanceCriterion, ...] | None:
    observation = session.scalar(
        select(DeploymentObservation).where(DeploymentObservation.post_deploy_work_unit_id == unit.id)
    )
    if observation is None:
        return None
    if observation.work_package_revision_id != revision.id:
        raise DomainError(
            "verification_subject_invalid",
            "post-deploy observation revision does not match generated unit",
            None,
        )
    specs = (
        (
            "post-deploy-artifact",
            "Deployed artifact digest matches release binding.",
            "release.deployment_observed",
            "bounded deployment observation evidence",
        ),
        (
            "post-deploy-health",
            "Production health probes pass.",
            "production.health",
            "bounded health probe summary",
        ),
        (
            "post-deploy-routes",
            "Required production routes are present.",
            "production.route_presence",
            "bounded route presence summary",
        ),
        (
            "post-deploy-auth",
            "Production M2M behavior matches expected posture.",
            "production.auth_behavior",
            "bounded authentication behavior summary",
        ),
        (
            "post-deploy-dispatch",
            "Production dispatch automation remains disabled.",
            "production.dispatch_posture",
            "bounded dispatch posture summary",
        ),
    )
    return tuple(
        PackageAcceptanceCriterion(
            work_package_revision_id=revision.id,
            ac_id=ac_id,
            condition=condition,
            evidence_type=evidence_type,
            evidence=evidence,
            approver="verifier",
        )
        for ac_id, condition, evidence_type, evidence in specs
    )
