import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Claim,
    ContextSnapshot,
    Dependency,
    Event,
    Evidence,
    WorkUnit,
)


@dataclass(frozen=True)
class StatusLedgerFilters:
    actor_id: str | None = None
    work_unit_id: uuid.UUID | None = None
    state: str | None = None
    include_inactive: bool = False


@dataclass(frozen=True)
class EvidenceStatus:
    id: uuid.UUID
    ac_id: str
    attempt: int
    evidence_type: str
    stable_ref: str | None
    source_revision: str
    recorded_by: str
    recorded_at: datetime
    context_snapshot_id: uuid.UUID | None


@dataclass(frozen=True)
class AdjudicationStatus:
    id: uuid.UUID
    ac_id: str
    outcome: str
    decided_by: str
    decided_at: datetime
    evidence_id: uuid.UUID | None
    rationale: str


@dataclass(frozen=True)
class FailureStatus:
    event_id: uuid.UUID
    actor_id: str
    occurred_at: datetime
    from_state: str | None
    reason: str | None


@dataclass(frozen=True)
class StatusLedgerRow:
    actor_id: str | None
    unit_id: uuid.UUID
    unit_key: str
    unit_title: str
    unit_state: str
    claim_id: uuid.UUID | None
    claim_attempt: int | None
    claim_lease_expires_at: datetime | None
    claim_released_at: datetime | None
    claim_terminal_reason: str | None
    last_heartbeat_at: datetime | None
    last_event_at: datetime | None
    blockers: tuple[dict[str, object | None], ...]
    pending_human_approvals: tuple[dict[str, object], ...]
    latest_evidence: EvidenceStatus | None
    latest_adjudication: AdjudicationStatus | None
    last_failure: FailureStatus | None
    context_snapshot_id: uuid.UUID | None
    context_classification: str | None
    context_decision: str | None


def status_ledger(
    session: Session,
    filters: StatusLedgerFilters | None = None,
) -> tuple[StatusLedgerRow, ...]:
    criteria = filters or StatusLedgerFilters()
    statement = select(WorkUnit)
    if criteria.work_unit_id is not None:
        statement = statement.where(WorkUnit.id == criteria.work_unit_id)
    if criteria.state is not None:
        statement = statement.where(WorkUnit.state == criteria.state)
    if not criteria.include_inactive:
        statement = statement.where(WorkUnit.state.not_in(("completed", "cancelled")))

    rows = tuple(_row_for_unit(session, unit) for unit in session.scalars(statement))
    if criteria.actor_id is not None:
        rows = tuple(row for row in rows if row.actor_id == criteria.actor_id)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.last_event_at is None,
                -(row.last_event_at.timestamp() if row.last_event_at is not None else 0),
                row.actor_id or "",
            ),
        )
    )


def _row_for_unit(session: Session, unit: WorkUnit) -> StatusLedgerRow:
    claim = _latest_claim(session, unit.id)
    latest_event = _latest_event(session, unit.id)
    latest_evidence = _latest_evidence(session, unit.id)
    latest_adjudication = _latest_adjudication(session, unit.id)
    last_failure = _last_failure(session, unit.id)
    context = _latest_context_snapshot(session, unit.id, claim)
    return StatusLedgerRow(
        actor_id=claim.claimed_by if claim is not None else _fallback_actor_id(latest_event),
        unit_id=unit.id,
        unit_key=unit.unit_key,
        unit_title=unit.title,
        unit_state=unit.state,
        claim_id=claim.id if claim is not None else None,
        claim_attempt=claim.attempt if claim is not None else None,
        claim_lease_expires_at=claim.lease_expires_at if claim is not None else None,
        claim_released_at=claim.released_at if claim is not None else None,
        claim_terminal_reason=claim.terminal_reason if claim is not None else None,
        last_heartbeat_at=claim.renewed_at if claim is not None else None,
        last_event_at=_max_datetime(
            latest_event.occurred_at if latest_event is not None else None,
            latest_evidence.recorded_at if latest_evidence is not None else None,
            latest_adjudication.decided_at if latest_adjudication is not None else None,
            context.created_at if context is not None else None,
        ),
        blockers=_blockers(session, unit.id),
        pending_human_approvals=_pending_human_approvals(session, unit),
        latest_evidence=_evidence_status(latest_evidence),
        latest_adjudication=_adjudication_status(latest_adjudication),
        last_failure=_failure_status(last_failure),
        context_snapshot_id=context.id if context is not None else None,
        context_classification=context.classification if context is not None else None,
        context_decision=context.decision if context is not None else None,
    )


def _latest_claim(session: Session, unit_id: uuid.UUID) -> Claim | None:
    active = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit_id, Claim.released_at.is_(None))
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc(), Claim.id.desc())
        .limit(1)
    )
    if active is not None:
        return active
    return session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit_id)
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc(), Claim.id.desc())
        .limit(1)
    )


def _latest_event(session: Session, unit_id: uuid.UUID) -> Event | None:
    return session.scalar(
        select(Event)
        .where(Event.subject_type == "work_unit", Event.subject_id == unit_id)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    )


def _latest_evidence(session: Session, unit_id: uuid.UUID) -> Evidence | None:
    return session.scalar(
        select(Evidence)
        .where(Evidence.work_unit_id == unit_id)
        .order_by(Evidence.recorded_at.desc(), Evidence.id.desc())
        .limit(1)
    )


def _latest_adjudication(session: Session, unit_id: uuid.UUID) -> Adjudication | None:
    return session.scalar(
        select(Adjudication)
        .where(Adjudication.work_unit_id == unit_id)
        .order_by(Adjudication.decided_at.desc(), Adjudication.id.desc())
        .limit(1)
    )


def _last_failure(session: Session, unit_id: uuid.UUID) -> Event | None:
    return session.scalar(
        select(Event)
        .where(
            Event.subject_type == "work_unit",
            Event.subject_id == unit_id,
            Event.to_state == "failed",
        )
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    )


def _latest_context_snapshot(
    session: Session,
    unit_id: uuid.UUID,
    claim: Claim | None,
) -> ContextSnapshot | None:
    preferred_id = None
    if claim is not None:
        preferred_id = claim.execution_context_snapshot_id or claim.context_snapshot_id
    if preferred_id is not None:
        snapshot = session.get(ContextSnapshot, preferred_id)
        if snapshot is not None:
            return snapshot
    return session.scalar(
        select(ContextSnapshot)
        .where(ContextSnapshot.work_unit_id == unit_id)
        .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.id.desc())
        .limit(1)
    )


def _blockers(session: Session, unit_id: uuid.UUID) -> tuple[dict[str, object | None], ...]:
    dependencies = session.scalars(
        select(Dependency)
        .where(Dependency.work_unit_id == unit_id, Dependency.status != "satisfied")
        .order_by(Dependency.id)
    )
    return tuple(
        {
            "dependency_id": str(dependency.id),
            "kind": dependency.kind,
            "required_state_or_condition": dependency.required_state_or_condition,
            "depends_on_work_unit_id": (
                str(dependency.depends_on_work_unit_id)
                if dependency.depends_on_work_unit_id is not None
                else None
            ),
            "external_ref": dependency.external_ref,
            "status": dependency.status,
        }
        for dependency in dependencies
    )


def _pending_human_approvals(
    session: Session,
    unit: WorkUnit,
) -> tuple[dict[str, object], ...]:
    if unit.state != "awaiting_approval":
        return ()
    approved = session.scalar(
        select(Approval.id)
        .where(
            Approval.subject_type == "action",
            Approval.subject_id == unit.id,
            Approval.subject_revision_or_fingerprint == str(unit.version),
            Approval.decision == "approved",
        )
        .limit(1)
    )
    if approved is not None:
        return ()
    return (
        {
            "subject_type": "action",
            "subject_revision_or_fingerprint": str(unit.version),
        },
    )


def _evidence_status(evidence: Evidence | None) -> EvidenceStatus | None:
    if evidence is None:
        return None
    return EvidenceStatus(
        id=evidence.id,
        ac_id=evidence.ac_id,
        attempt=evidence.attempt,
        evidence_type=evidence.evidence_type,
        stable_ref=evidence.stable_ref,
        source_revision=evidence.source_revision,
        recorded_by=evidence.recorded_by,
        recorded_at=evidence.recorded_at,
        context_snapshot_id=evidence.context_snapshot_id,
    )


def _adjudication_status(adjudication: Adjudication | None) -> AdjudicationStatus | None:
    if adjudication is None:
        return None
    return AdjudicationStatus(
        id=adjudication.id,
        ac_id=adjudication.ac_id,
        outcome=adjudication.outcome,
        decided_by=adjudication.decided_by,
        decided_at=adjudication.decided_at,
        evidence_id=adjudication.evidence_id,
        rationale=adjudication.rationale,
    )


def _failure_status(event: Event | None) -> FailureStatus | None:
    if event is None:
        return None
    return FailureStatus(
        event_id=event.id,
        actor_id=event.actor_id,
        occurred_at=event.occurred_at,
        from_state=event.from_state,
        reason=event.payload.get("reason"),
    )


def _fallback_actor_id(event: Event | None) -> str | None:
    if event is None:
        return None
    return event.actor_id


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    return max(present)
