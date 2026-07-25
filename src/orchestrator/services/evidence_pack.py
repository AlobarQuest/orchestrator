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

from orchestrator.api.schemas import (
    EvidencePackAdjudicationResponse,
    EvidencePackApprovalResponse,
    EvidencePackAuthorityResponse,
    EvidencePackAuthorityViolationResponse,
    EvidencePackClaimResponse,
    EvidencePackDependencyResponse,
    EvidencePackEventPublicationResponse,
    EvidencePackEventResponse,
    EvidencePackEvidenceResponse,
    EvidencePackProvenanceResponse,
    EvidencePackResponse,
    EvidencePackWorkUnitResponse,
)
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


def evidence_pack_response(projection: dict[str, Any]) -> EvidencePackResponse:
    """Serialize `evidence_pack_projection`'s ORM/set-bearing dict into a JSON-safe response.

    The projection is deliberately GUI-shaped (ORM rows, `set[UUID]` membership tests) since it
    was originally private to the `/review` template. This is the one place that maps it to plain,
    JSON-serializable types -- callers must never return the projection dict directly from a JSON
    route.
    """
    unit: WorkUnit = projection["unit"]
    revision: WorkPackageRevision = projection["revision"]
    current_evidence_ids: set[uuid.UUID] = projection["current_evidence_ids"]
    current_adjudication_ids: set[uuid.UUID] = projection["current_adjudication_ids"]
    violation = projection["authority_violation"]

    return EvidencePackResponse(
        work_unit=EvidencePackWorkUnitResponse(
            id=unit.id,
            title=unit.title,
            state=unit.state,
            authority_fingerprint=unit.authority_fingerprint,
        ),
        provenance=EvidencePackProvenanceResponse(
            revision=revision.revision,
            content_hash=revision.content_hash,
            source_path=revision.source_path,
            source_commit=revision.source_commit,
            registered_by=revision.registered_by,
        ),
        authority=EvidencePackAuthorityResponse(
            authority_fingerprint=unit.authority_fingerprint,
            envelope=projection["authority"],
            authority_violation=(
                EvidencePackAuthorityViolationResponse(**violation)
                if violation is not None
                else None
            ),
        ),
        dependencies=[
            EvidencePackDependencyResponse(
                kind=row.kind,
                required_state_or_condition=row.required_state_or_condition,
                status=row.status,
            )
            for row in projection["dependencies"]
        ],
        claims=[
            EvidencePackClaimResponse(
                attempt=row.attempt,
                claimed_by=row.claimed_by,
                lease_expires_at=row.lease_expires_at,
                terminal_reason=row.terminal_reason,
            )
            for row in projection["claims"]
        ],
        evidence=[
            EvidencePackEvidenceResponse(
                id=row.id,
                ac_id=row.ac_id,
                current=row.id in current_evidence_ids,
                evidence_type=row.evidence_type,
                stable_ref=row.stable_ref,
                payload=row.payload,
                supersedes=row.supersedes_evidence_id,
            )
            for row in projection["evidence"]
        ],
        adjudications=[
            EvidencePackAdjudicationResponse(
                id=row.id,
                ac_id=row.ac_id,
                outcome=row.outcome,
                current=row.id in current_adjudication_ids,
                decided_by=row.decided_by,
                rationale=row.rationale,
                risk=row.risk,
                follow_up=row.follow_up,
                scope=row.scope,
                expires_at=row.expires_at,
                failed_evidence_id=row.failed_evidence_id,
            )
            for row in projection["adjudications"]
        ],
        approvals=[
            EvidencePackApprovalResponse(
                subject_type=row.subject_type,
                decision=row.decision,
                approved_by=row.approved_by,
                reason=row.reason,
            )
            for row in projection["approvals"]
        ],
        event_publications=[
            EvidencePackEventPublicationResponse(
                source_ref=item["source_ref"],
                status=item["row"].status,
                event_id=item["row"].event_id,
                export_ref=item["row"].export_ref,
                last_error=item["row"].last_error,
            )
            for item in projection["event_publications"]
        ],
        events=[
            EvidencePackEventResponse(
                occurred_at=row.occurred_at,
                action=row.action,
                actor_id=row.actor_id,
                from_state=row.from_state,
                to_state=row.to_state,
                reason=row.payload.get("reason") if row.payload else None,
            )
            for row in projection["events"]
        ],
    )
