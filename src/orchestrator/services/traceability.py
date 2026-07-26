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

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)


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
