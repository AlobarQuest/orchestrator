"""Bidirectional traceability query (WS-P2.6).

Resolves any node on the intent -> work unit -> PR -> commit -> artifact -> deployment ->
observation chain to the full ordered chain, answering "why is this code in production?". It
reads canonical rows only and composes the WS-P2.5 projections and the release-artifact /
deployment-observation fetchers; it never writes, never transitions, and never touches git.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    TraceabilityAnchorResponse,
    TraceabilityArtifactHop,
    TraceabilityChainResponse,
    TraceabilityCommitHop,
    TraceabilityConditionHop,
    TraceabilityDeploymentHop,
    TraceabilityIntentHop,
    TraceabilityObservationHop,
    TraceabilityPrHop,
    TraceabilityResponse,
    TraceabilityUnitHop,
)
from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    DeploymentObservation,
    Observation,
    ReconciliationCondition,
    ReconciliationResolution,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.deployment_observations import list_deployment_observations
from orchestrator.services.evidence_pack import evidence_pack_projection
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.release_artifacts import list_release_artifacts


@dataclass(frozen=True)
class TraceabilityAnchor:
    kind: str
    work_unit_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    artifact_digest: str | None = None
    commit: str | None = None
    pr_number: int | None = None
    source_repository: str | None = None
    environment: str | None = None

    @property
    def display_value(self) -> str:
        value = {
            "work_unit": self.work_unit_id,
            "revision": self.revision_id,
            "artifact_digest": self.artifact_digest,
            "commit": self.commit,
            "pr": self.pr_number,
            "environment": self.environment,
        }[self.kind]
        return str(value)


def resolve_anchors(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.kind == "work_unit":
        if session.get(WorkUnit, anchor.work_unit_id) is None:
            raise DomainError("work_unit_not_found", "work unit does not exist", None)
        return (anchor.work_unit_id,)  # type: ignore[return-value]
    if anchor.kind == "revision":
        if session.get(WorkPackageRevision, anchor.revision_id) is None:
            raise DomainError("revision_not_found", "package revision does not exist", None)
        return tuple(
            session.scalars(
                select(WorkUnit.id)
                .where(WorkUnit.work_package_revision_id == anchor.revision_id)
                .order_by(WorkUnit.unit_key)
            )
        )
    if anchor.kind == "artifact_digest":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                ReleaseArtifactBinding.artifact_digest == anchor.artifact_digest
            ),
        )
    if anchor.kind == "commit":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                or_(
                    ReleaseArtifactBinding.source_commit == anchor.commit,
                    ReleaseArtifactBinding.merge_commit == anchor.commit,
                )
            ),
        )
    if anchor.kind == "pr":
        return _resolve_pr(session, anchor)
    if anchor.kind == "environment":
        return _resolve_environment(session, anchor.environment)
    raise DomainError("traceability_anchor_invalid", f"unknown anchor kind {anchor.kind}", None)


def _distinct_units(session: Session, stmt) -> tuple[uuid.UUID, ...]:
    # Preserve first-seen order for a stable response; de-duplicate a digest/commit shared by
    # multiple bindings of the same unit.
    seen: dict[uuid.UUID, None] = {}
    for unit_id in session.scalars(stmt):
        seen.setdefault(unit_id, None)
    return tuple(seen)


def _resolve_pr(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.source_repository is not None:
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                ReleaseArtifactBinding.source_repository == anchor.source_repository,
                ReleaseArtifactBinding.implementation_pr_number == anchor.pr_number,
            ),
        )
    return _distinct_units(
        session,
        select(UnitPrBinding.work_unit_id).where(UnitPrBinding.pr_number == anchor.pr_number),
    )


def _resolve_environment(session: Session, environment: str | None) -> tuple[uuid.UUID, ...]:
    # "What is in this environment now" = the latest observation per unit for that environment.
    rows = session.scalars(
        select(DeploymentObservation)
        .where(DeploymentObservation.environment == environment)
        .order_by(
            DeploymentObservation.observed_at.desc(),
            DeploymentObservation.recorded_at.desc(),
            DeploymentObservation.id.desc(),
        )
    )
    seen: dict[uuid.UUID, None] = {}
    for row in rows:
        seen.setdefault(row.implementation_work_unit_id, None)
    return tuple(seen)


def build_chain(session: Session, unit_id: uuid.UUID) -> TraceabilityChainResponse:
    projection = evidence_pack_projection(session, unit_id)  # raises work_unit_not_found if absent
    unit = projection["unit"]
    revision = projection["revision"]
    authority_approval = next(
        (a for a in projection["approvals"] if a.subject_type == "authority"), None
    )

    artifacts = _unwrap(list_release_artifacts(session, unit_id))
    pr_binding = get_pr_binding(session, unit_id)

    deployment_hops: list[TraceabilityDeploymentHop] = []
    for binding in artifacts:
        for obs in _unwrap(list_deployment_observations(session, binding.id)):
            deployment_hops.append(
                TraceabilityDeploymentHop(
                    environment=obs.environment,
                    observed_artifact_digest=obs.observed_artifact_digest,
                    digest_matches=obs.observed_artifact_digest == binding.artifact_digest,
                    deployment_ref=obs.deployment_ref,
                    deployment_url=obs.deployment_url,
                    deployer=obs.deployer,
                    observed_at=obs.observed_at,
                    status_summary=obs.status_summary,
                    probe_summary=obs.probe_summary,
                )
            )

    conditions = tuple(
        session.scalars(
            select(ReconciliationCondition)
            .where(ReconciliationCondition.work_unit_id == unit_id)
            .order_by(ReconciliationCondition.detected_at, ReconciliationCondition.id)
        )
    )
    resolutions = (
        {
            row.condition_id: row
            for row in session.scalars(
                select(ReconciliationResolution).where(
                    ReconciliationResolution.condition_id.in_([c.id for c in conditions])
                )
            )
        }
        if conditions
        else {}
    )

    observations = tuple(
        session.scalars(
            select(Observation)
            .where(
                Observation.subject_type == "work_unit",
                Observation.subject_reference == str(unit_id),
            )
            .order_by(Observation.observed_at, Observation.received_at, Observation.id)
        )
    )

    return TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=revision.revision,
            content_hash=revision.content_hash,
            source_path=revision.source_path,
            source_commit=revision.source_commit,
            registered_by=revision.registered_by,
        ),
        unit=TraceabilityUnitHop(
            id=unit.id,
            unit_key=unit.unit_key,
            title=unit.title,
            state=unit.state,
            authority_fingerprint=unit.authority_fingerprint,
            authority_approved_by=authority_approval.approved_by if authority_approval else None,
            authority_decision=authority_approval.decision if authority_approval else None,
        ),
        pr=(
            TraceabilityPrHop(pr_number=pr_binding.pr_number, head_sha=pr_binding.head_sha)
            if pr_binding is not None
            else None
        ),
        commit=[
            TraceabilityCommitHop(
                source_repository=b.source_repository,
                source_commit=b.source_commit,
                merge_commit=b.merge_commit,
                implementation_pr_number=b.implementation_pr_number,
            )
            for b in artifacts
        ],
        artifact=[
            TraceabilityArtifactHop(
                artifact_digest=b.artifact_digest,
                artifact_registry=b.artifact_registry,
                artifact_repository=b.artifact_repository,
                artifact_name=b.artifact_name,
                artifact_tag=b.artifact_tag,
                workflow_run_url=b.workflow_run_url,
                builder_id=b.builder_id,
                provenance_digest=b.provenance_digest,
                sbom_digest=b.sbom_digest,
            )
            for b in artifacts
        ],
        deployment=deployment_hops,
        conditions=[
            TraceabilityConditionHop(
                observation_kind=c.observation_kind,
                condition_type=c.condition_type,
                detail=c.detail,
                resolution_generation=c.resolution_generation,
                detected_at=c.detected_at,
                open=c.id not in resolutions,
                resolution_decision=(resolutions[c.id].decision if c.id in resolutions else None),
            )
            for c in conditions
        ],
        observations=[
            TraceabilityObservationHop(
                source_system=o.source_system,
                observation_type=o.observation_type,
                status=o.status,
                severity=o.severity,
                summary=o.summary,
                observed_at=o.observed_at,
            )
            for o in observations
        ],
    )


def _unwrap(result):
    # list_* fetchers return `tuple | DomainError`; inside build_chain the unit is known to exist
    # (evidence_pack_projection already validated it), so a DomainError here is a real bug.
    if isinstance(result, DomainError):
        raise result
    return result


def traceability_response(session: Session, anchor: TraceabilityAnchor) -> TraceabilityResponse:
    unit_ids = resolve_anchors(session, anchor)
    return TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on=anchor.kind, value=anchor.display_value),
        chains=[build_chain(session, unit_id) for unit_id in unit_ids],
    )
