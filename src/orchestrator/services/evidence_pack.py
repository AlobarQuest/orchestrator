"""Per-unit evidence-pack projection (WS-P2.5).

Assembles the full evidentiary record for a single work unit -- authority, revision,
dependencies, claims, evidence, adjudications, approvals, events, and event publications --
into a single read-only dict. Originally private to the ``/review`` GUI module; moved here so
other callers can share the identical assembly and query logic.
"""

import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.runner_authority import dependency_update_authority_violation
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Claim,
    Dependency,
    Event,
    EventPublication,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)


def evidence_pack_projection(session: Session, unit_id: uuid.UUID) -> dict[str, Any]:
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    evidence = tuple(
        session.scalars(
            select(Evidence).where(Evidence.work_unit_id == unit.id).order_by(Evidence.recorded_at)
        )
    )
    adjudications = tuple(
        session.scalars(
            select(Adjudication)
            .where(Adjudication.work_unit_id == unit.id)
            .order_by(Adjudication.decided_at)
        )
    )
    events = tuple(
        session.scalars(
            select(Event).where(Event.subject_id == unit.id).order_by(Event.occurred_at, Event.id)
        )
    )
    authority = normalize_authority(unit.authority).normalized()
    violation = dependency_update_authority_violation(normalize_authority(unit.authority))
    return {
        "unit": unit,
        "authority": authority,
        "authority_violation": (
            {
                "code": violation.code,
                "message": violation.message,
                "remediation": violation.remediation,
            }
            if violation is not None
            else None
        ),
        "revision": revision,
        "dependencies": tuple(
            session.scalars(select(Dependency).where(Dependency.work_unit_id == unit.id))
        ),
        "claims": tuple(
            session.scalars(
                select(Claim).where(Claim.work_unit_id == unit.id).order_by(Claim.attempt.desc())
            )
        ),
        "evidence": evidence,
        "current_evidence_ids": {row.id for row in evidence}
        - {row.supersedes_evidence_id for row in evidence if row.supersedes_evidence_id},
        "adjudications": adjudications,
        "current_adjudication_ids": {row.id for row in adjudications}
        - {
            row.supersedes_adjudication_id
            for row in adjudications
            if row.supersedes_adjudication_id
        },
        "approvals": tuple(
            session.scalars(
                select(Approval).where(Approval.subject_id == unit.id).order_by(Approval.created_at)
            )
        ),
        "events": events,
        "event_publications": _event_publication_projection(
            session,
            evidence=evidence,
            adjudications=adjudications,
            events=events,
        ),
    }


def _event_publication_projection(
    session: Session,
    *,
    evidence: tuple[Evidence, ...],
    adjudications: tuple[Adjudication, ...],
    events: tuple[Event, ...],
) -> tuple[dict[str, Any], ...]:
    source_ids: dict[str, set[uuid.UUID]] = {
        "evidence": {row.id for row in evidence},
        "adjudication": {row.id for row in adjudications},
        "event": {row.id for row in events},
    }
    clauses = [
        and_(EventPublication.source_kind == kind, EventPublication.source_id.in_(ids))
        for kind, ids in source_ids.items()
        if ids
    ]
    if not clauses:
        return ()
    rows = tuple(
        session.scalars(
            select(EventPublication)
            .where(or_(*clauses))
            .order_by(
                EventPublication.source_kind,
                EventPublication.source_id,
                EventPublication.created_at,
                EventPublication.event_id,
            )
        )
    )
    return tuple(
        {
            "row": row,
            "source_ref": f"orchestrator:{row.source_kind}:{row.source_id}",
        }
        for row in rows
    )
