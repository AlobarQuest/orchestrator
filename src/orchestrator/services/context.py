import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.context import (
    ContextDecision,
    classify_context_update,
    context_fingerprint,
    normalize_standing_context,
)
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.persistence.models import (
    Approval,
    Claim,
    ContextSnapshot,
    Event,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext


@dataclass(frozen=True)
class PreflightCommand:
    work_unit_id: uuid.UUID
    standing_context: dict[str, object]
    previous_context_snapshot_id: uuid.UUID | None
    approval_id: uuid.UUID | None
    purpose: str
    idempotency_key: str
    attempt: int | None = None
    lease_token: str | None = None


def record_preflight(
    session: Session,
    command: PreflightCommand,
    actor: ActorContext,
) -> ContextSnapshot | DomainError:
    try:
        replay = _preflight_replay(session, command, actor)
        if replay is not None:
            return replay
        unit = _locked_unit(session, command.work_unit_id)
        if unit is None:
            return DomainError("work_unit_not_found", "work unit does not exist", None)
        previous = _previous_context(session, command, unit)
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            return DomainError("revision_not_found", "package revision does not exist", None)

        normalized_context = normalize_standing_context(command.standing_context)
        normalized_required = normalize_standing_context(_required_context(revision))
        decision = classify_context_update(
            previous=previous,
            current=normalized_context,
            required=normalized_required,
            allowed_capabilities=_allowed_capabilities(unit, revision, normalized_required),
        )
        effective_decision = _effective_decision(
            session, unit, command, decision, normalized_context
        )
        if isinstance(effective_decision, DomainError):
            return effective_decision
        classification = decision.classification
        if previous is None and classification == "same_scope":
            classification = "accepted"

        now = TransactionClock().now(session)
        claim = _bound_claim(session, unit, command, actor, now)
        attempt = _snapshot_attempt(unit, claim, command.purpose)
        context_digest = context_fingerprint(normalized_context)
        snapshot = ContextSnapshot(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            claim_id=claim.id if claim is not None else None,
            attempt=attempt,
            actor_id=actor.actor_id,
            actor_role=actor.role,
            context=normalized_context,
            context_fingerprint=context_digest,
            classification=classification,
            decision=effective_decision,
            approval_id=command.approval_id,
            event_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
            created_at=now,
        )
        session.add(snapshot)
        session.flush()
        event = Event(
            id=snapshot.event_id,
            occurred_at=now,
            actor_id=actor.actor_id,
            action=_event_action(snapshot),
            subject_type="context_snapshot",
            subject_id=snapshot.id,
            from_state=None,
            to_state=None,
            payload={
                "actor_role": actor.role,
                "classification": snapshot.classification,
                "decision": snapshot.decision,
                "purpose": command.purpose,
                "work_unit_id": str(unit.id),
                "attempt": snapshot.attempt,
                "claim_id": str(snapshot.claim_id) if snapshot.claim_id is not None else None,
                "context_fingerprint": snapshot.context_fingerprint,
                "previous_context_snapshot_id": (
                    str(command.previous_context_snapshot_id)
                    if command.previous_context_snapshot_id is not None
                    else None
                ),
                "approval_id": str(command.approval_id) if command.approval_id else None,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
        session.add(event)
        session.flush()
        return snapshot
    except DomainError as error:
        return error


def require_claim_context(
    session: Session,
    command: PreflightCommand,
    actor: ActorContext,
) -> ContextSnapshot:
    result = record_preflight(session, command, actor)
    if isinstance(result, DomainError):
        raise result
    return result


def require_execution_context(
    session: Session,
    command: PreflightCommand,
    actor: ActorContext,
) -> ContextSnapshot:
    result = record_preflight(session, command, actor)
    if isinstance(result, DomainError):
        raise result
    return result


def _locked_unit(session: Session, unit_id: uuid.UUID) -> WorkUnit | None:
    return session.scalar(select(WorkUnit).where(WorkUnit.id == unit_id).with_for_update())


def _preflight_replay(
    session: Session,
    command: PreflightCommand,
    actor: ActorContext,
) -> ContextSnapshot | None:
    existing = session.scalar(
        select(ContextSnapshot).where(ContextSnapshot.idempotency_key == command.idempotency_key)
    )
    if existing is None:
        existing_event = session.scalar(
            select(Event).where(Event.idempotency_key == command.idempotency_key)
        )
        if existing_event is not None:
            raise _idempotency_conflict()
        return None
    expected = (
        existing.work_unit_id == command.work_unit_id
        and existing.actor_id == actor.actor_id
        and existing.actor_role == actor.role
        and existing.context_fingerprint == context_fingerprint(command.standing_context)
        and existing.approval_id == command.approval_id
    )
    if not expected:
        raise _idempotency_conflict()
    event = session.get(Event, existing.event_id)
    if (
        event is None
        or event.subject_id != existing.id
        or event.payload.get("purpose") != command.purpose
        or event.payload.get("previous_context_snapshot_id")
        != (
            str(command.previous_context_snapshot_id)
            if command.previous_context_snapshot_id is not None
            else None
        )
    ):
        raise _idempotency_conflict()
    return existing


def _previous_context(
    session: Session, command: PreflightCommand, unit: WorkUnit
) -> dict[str, object] | None:
    if command.previous_context_snapshot_id is None:
        return None
    snapshot = session.get(ContextSnapshot, command.previous_context_snapshot_id)
    if snapshot is None or snapshot.work_unit_id != unit.id:
        raise DomainError(
            "context_snapshot_invalid",
            "previous context snapshot does not match work unit",
            None,
        )
    return snapshot.context if isinstance(snapshot.context, dict) else None


def _required_context(revision: WorkPackageRevision) -> dict[str, object]:
    required = revision.enforcement_snapshot.get("required_context", {})
    if not isinstance(required, dict):
        return {}
    return required


def _allowed_capabilities(
    unit: WorkUnit,
    revision: WorkPackageRevision,
    required_context: Mapping[str, object],
) -> set[str]:
    capabilities = set(_string_list(required_context.get("capabilities")))
    capabilities.add(unit.required_capability)
    authority_value = revision.enforcement_snapshot.get("authority", {})
    if isinstance(authority_value, Mapping):
        authority = normalize_authority(authority_value)
        capabilities.update(
            capability
            for capability, level in authority.capabilities.items()
            if level == "allowed"
        )
    return capabilities


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _event_action(snapshot: ContextSnapshot) -> str:
    if snapshot.classification == "same_scope":
        return "context.update_accepted"
    return "context.preflight_recorded"


def _effective_decision(
    session: Session,
    unit: WorkUnit,
    command: PreflightCommand,
    decision: ContextDecision,
    normalized_context: Mapping[str, object],
) -> str | DomainError:
    if decision.classification == "missing_required":
        return DomainError("context_missing_required", "standing context is incomplete", None)
    if decision.classification == "stale":
        return DomainError("context_stale", "standing context is stale", None)
    if decision.decision != "requires_approval":
        return decision.decision
    if command.approval_id is None:
        return DomainError(
            "context_authority_expanding",
            "standing context expands authority",
            "approval_required",
        )
    approval = session.get(Approval, command.approval_id)
    if approval is None:
        return DomainError("approval_not_found", "approval does not exist", None)
    if not _approval_matches(unit, approval, normalized_context):
        return DomainError(
            "context_approval_mismatch",
            "approval does not authorize this standing context",
            "approve",
        )
    return "accepted"


def _approval_matches(
    unit: WorkUnit,
    approval: Approval,
    normalized_context: Mapping[str, object],
) -> bool:
    _ = normalized_context
    return (
        approval.subject_type == "authority"
        and approval.subject_id == unit.id
        and approval.decision == "approved"
        and approval.subject_revision_or_fingerprint == unit.authority_fingerprint
        and approval.approved_by != ""
    )


def _bound_claim(
    session: Session,
    unit: WorkUnit,
    command: PreflightCommand,
    actor: ActorContext,
    occurred_at: datetime,
) -> Claim | None:
    if command.purpose != "execution":
        return None
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc())
        .limit(1)
        .with_for_update()
    )
    valid = (
        claim is not None
        and command.attempt is not None
        and command.lease_token is not None
        and claim.claimed_by == actor.actor_id
        and claim.attempt == command.attempt
        and secrets.compare_digest(
            claim.lease_token_hash,
            hash_lease_token(command.lease_token),
        )
        and claim.released_at is None
        and claim.lease_expires_at > occurred_at
    )
    if not valid:
        raise DomainError(
            "active_claim_required",
            "execution preflight requires active claim credentials",
            "claim",
        )
    return claim


def _snapshot_attempt(unit: WorkUnit, claim: Claim | None, purpose: str) -> int:
    if claim is not None:
        return claim.attempt
    if purpose == "execution":
        raise DomainError(
            "active_claim_required",
            "execution preflight requires an active claim",
            "claim",
        )
    return max(1, unit.attempt_count + 1)


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
