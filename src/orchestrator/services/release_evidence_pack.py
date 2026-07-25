"""Per-release evidence-pack assembly (WS-P2.5 Increment 2).

Composes the Increment-1 per-unit evidence pack for every work unit in a package revision
with that revision's release artifact bindings and deployment observations, producing one
read-only, JSON-safe response consumed by both the ``/api`` JSON route and the ``/review``
GUI page. It reads canonical rows only; it never dispatches, deploys, or writes to git.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    DeploymentObservationResponse,
    ReleaseArtifactResponse,
    ReleaseEvidencePackResponse,
    ReleaseEvidencePackRevisionResponse,
)
from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.evidence_pack import (
    evidence_pack_projection,
    evidence_pack_response,
)


def release_evidence_pack_response(
    session: Session, revision_id: uuid.UUID
) -> ReleaseEvidencePackResponse:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    units = tuple(
        session.scalars(
            select(WorkUnit)
            .where(WorkUnit.work_package_revision_id == revision_id)
            .order_by(WorkUnit.unit_key)
        )
    )
    artifacts = tuple(
        session.scalars(
            select(ReleaseArtifactBinding)
            .where(ReleaseArtifactBinding.work_package_revision_id == revision_id)
            .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
        )
    )
    observations = tuple(
        session.scalars(
            select(DeploymentObservation)
            .where(DeploymentObservation.work_package_revision_id == revision_id)
            .order_by(DeploymentObservation.recorded_at, DeploymentObservation.id)
        )
    )
    return ReleaseEvidencePackResponse(
        revision=ReleaseEvidencePackRevisionResponse.model_validate(revision),
        # Intentionally O(N): each unit is composed through the full per-unit projection rather
        # than re-implementing a batched query, per the spec's "compose, don't reimplement" rule.
        # Fine at today's revision sizes; revisit with a batched projection if a high-volume
        # consumer (e.g. WS-P2.6) ever drives large revisions through this path.
        units=[
            evidence_pack_response(evidence_pack_projection(session, unit.id)) for unit in units
        ],
        release_artifacts=[ReleaseArtifactResponse.model_validate(row) for row in artifacts],
        deployments=[DeploymentObservationResponse.model_validate(row) for row in observations],
    )
