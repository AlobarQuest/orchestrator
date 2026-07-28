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
from orchestrator.services.lifecycle import FOLLOW_UP_AC_ID, FOLLOW_UP_EVIDENCE_TYPE


def load_required_criteria(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> tuple[PackageAcceptanceCriterion, ...]:
    generated = _generated_post_deploy_criteria(session, unit, revision)
    if generated is not None:
        return generated

    follow_up = _generated_follow_up_criteria(unit, revision)
    if follow_up is not None:
        return follow_up

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
        select(DeploymentObservation).where(
            DeploymentObservation.post_deploy_work_unit_id == unit.id
        )
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


_FOLLOW_UP_DEFAULT_REVISIT = (
    "No revisit condition was declared; confirm whether this outcome still holds."
)


def _generated_follow_up_criteria(
    unit: WorkUnit,
    revision: WorkPackageRevision,
) -> tuple[PackageAcceptanceCriterion, ...] | None:
    """The one criterion a package-declared follow-up review is discharged against.

    `observation` is already in JUDGMENT_TYPES and already accepted by the intake vocabulary
    gate, so this adds no evidence type and no vocabulary migration. It evaluates to
    `judgment_required`, which is what routes the unit to a human rather than to an evaluator.

    Every field falls back, because `revisit_when` and `owner` are nullable in the schema and
    `{"required": true, "revisit_when": null, "signals": [], "owner": null}` is a valid
    declaration -- one that would otherwise produce a criterion a reviewer cannot act on.
    """
    if unit.required_capability != "follow_up_review":
        return None
    declaration = revision.follow_up if isinstance(revision.follow_up, dict) else {}
    revisit = declaration.get("revisit_when") or _FOLLOW_UP_DEFAULT_REVISIT
    owner = declaration.get("owner") or revision.approved_by
    return (
        PackageAcceptanceCriterion(
            work_package_revision_id=revision.id,
            ac_id=FOLLOW_UP_AC_ID,
            condition="The follow-up questions declared by the package were answered.",
            evidence_type=FOLLOW_UP_EVIDENCE_TYPE,
            evidence=str(revisit),
            approver=str(owner),
        ),
    )
