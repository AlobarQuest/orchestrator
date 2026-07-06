import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.context import context_fingerprint
from orchestrator.kernel.leases import LEASE_DURATION, hash_lease_token
from orchestrator.kernel.readiness import ReadinessStatus
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
from orchestrator.persistence.models import (
    Approval,
    Claim,
    ContextSnapshot,
    Event,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.context import PreflightCommand, require_claim_context
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import evaluate_readiness


@dataclass(frozen=True)
class LeaseGrant:
    claim_id: uuid.UUID
    attempt: int
    lease_token: str
    expires_at: datetime
    context_snapshot_id: uuid.UUID | None = None


def claim_unit(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    idempotency_key: str,
    expected_version: int | None = None,
    standing_context: dict[str, Any] | None = None,
) -> LeaseGrant | DomainError:
    try:
        unit = _locked_unit(session, unit_id)
        replay = _claim_replay(
            session,
            unit,
            actor,
            idempotency_key,
            expected_version=expected_version,
            standing_context=standing_context,
        )
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None:
            _require_version(unit, expected_version)
        if actor.role is not ActorRole.WORKER:
            raise DomainError("role_forbidden", "only a worker may acquire a claim", None)
        if WorkUnitState(unit.state) is not WorkUnitState.READY:
            raise DomainError("claim_conflict", "work unit is not available to claim", "retry")
        if unit.attempt_count >= unit.max_attempts:
            raise DomainError("attempts_exhausted", "attempt budget is exhausted", "approve_retry")

        now = TransactionClock().now(session)
        context_snapshot = _claim_context_snapshot(
            session, unit, actor, idempotency_key, standing_context
        )
        token = secrets.token_urlsafe(32)
        unit.attempt_count += 1
        claim = Claim(
            work_unit_id=unit.id,
            attempt=unit.attempt_count,
            claimed_by=actor.actor_id,
            lease_token_hash=hash_lease_token(token),
            idempotency_key=idempotency_key,
            context_snapshot_id=context_snapshot.id if context_snapshot is not None else None,
            acquired_at=now,
            lease_expires_at=now + LEASE_DURATION,
        )
        session.add(claim)
        session.flush()
        _transition(
            session,
            unit,
            WorkUnitState.CLAIMED,
            actor=ActorContext(actor.actor_id, ActorRole.SYSTEM),
            idempotency_key=idempotency_key,
            occurred_at=now,
            payload={
                "claim_id": str(claim.id),
                "attempt": claim.attempt,
                "expected_version": expected_version,
                "context_snapshot_id": (
                    str(context_snapshot.id) if context_snapshot is not None else None
                ),
            },
        )
        session.commit()
        return LeaseGrant(
            claim.id,
            claim.attempt,
            token,
            claim.lease_expires_at,
            claim.context_snapshot_id,
        )
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def renew_claim(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    attempt: int,
    lease_token: str,
    *,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> LeaseGrant | DomainError:
    try:
        unit = _locked_unit(session, unit_id)
        if idempotency_key is not None:
            replay = _renew_replay(
                session, unit, actor, attempt, lease_token, idempotency_key, expected_version
            )
            if replay is not None:
                session.commit()
                return replay
        if expected_version is not None:
            _require_version(unit, expected_version)
        claim = _current_claim(session, unit.id)
        if claim is None or not _claim_owned_by(claim, actor, attempt, lease_token):
            raise DomainError("claim_not_owned", "claim credentials do not match", None)
        now = TransactionClock().now(session)
        if claim.released_at is not None or claim.lease_expires_at <= now:
            raise DomainError("lease_expired", "claim lease has expired", "reclaim")
        if WorkUnitState(unit.state) not in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}:
            raise DomainError("claim_not_active", "work unit has no active claim", None)
        claim.renewed_at = now
        claim.lease_expires_at = now + LEASE_DURATION
        if idempotency_key is not None:
            session.add(
                Event(
                    occurred_at=now,
                    actor_id=actor.actor_id,
                    action="claim.renewed",
                    subject_type="work_unit",
                    subject_id=unit.id,
                    from_state=unit.state,
                    to_state=unit.state,
                    payload={
                        "attempt": attempt,
                        "expected_version": expected_version,
                        "expires_at": claim.lease_expires_at.isoformat(),
                        "lease_token_hash": hash_lease_token(lease_token),
                    },
                    correlation_id=uuid.uuid4(),
                    idempotency_key=idempotency_key,
                )
            )
        session.commit()
        return LeaseGrant(
            claim.id,
            claim.attempt,
            "",
            claim.lease_expires_at,
            claim.context_snapshot_id,
        )
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def reclaim_expired_claim(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    next_owner: ActorContext,
    idempotency_key: str,
    *,
    expected_version: int | None = None,
    standing_context: dict[str, Any] | None = None,
) -> LeaseGrant | DomainError:
    try:
        grant = _perform_reclaim(
            session,
            unit_id,
            actor,
            next_owner,
            idempotency_key,
            expected_version=expected_version,
            standing_context=standing_context,
        )
        session.commit()
        return grant
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def _perform_reclaim(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    next_owner: ActorContext,
    idempotency_key: str,
    *,
    expected_version: int | None = None,
    standing_context: dict[str, Any] | None = None,
) -> LeaseGrant | DomainError:
    unit = _locked_unit(session, unit_id)
    replay = _claim_replay(
        session,
        unit,
        next_owner,
        idempotency_key,
        expected_version=expected_version,
        standing_context=standing_context,
    )
    if replay is not None:
        return replay
    error_replay = _reclaim_error_replay(
        session, unit, actor, next_owner, idempotency_key, expected_version
    )
    if error_replay is not None:
        return error_replay
    if expected_version is not None:
        _require_version(unit, expected_version)
    _validate_reclaim_roles(actor, next_owner)
    claim = _current_claim(session, unit.id)
    if claim is None:
        raise DomainError("claim_not_found", "work unit has no claim", None)
    now = TransactionClock().now(session)
    _validate_expired_active_claim(unit, claim, now)

    claim.released_at = now
    claim.terminal_reason = "lease_expired"
    correlation_id = uuid.uuid4()
    eligibility_error = _reclaim_eligibility_error(session, unit)
    failed_payload: dict[str, object] = {
        "expired_claim_id": str(claim.id),
        "attempt": claim.attempt,
        "reclaim_actor_id": actor.actor_id,
        "next_owner_id": next_owner.actor_id,
        "reason": "lease_expired",
        "expected_version": expected_version,
    }
    if eligibility_error is not None:
        failed_payload["result_error_code"] = eligibility_error.code
    _transition(
        session,
        unit,
        WorkUnitState.FAILED,
        actor=actor,
        idempotency_key=f"{idempotency_key}:failed",
        occurred_at=now,
        correlation_id=correlation_id,
        payload=failed_payload,
    )
    if eligibility_error is not None:
        return eligibility_error
    _transition(
        session,
        unit,
        WorkUnitState.READY,
        actor=actor,
        idempotency_key=f"{idempotency_key}:ready",
        occurred_at=now,
        correlation_id=correlation_id,
    )
    return _acquire_reclaimed_claim(
        session,
        unit,
        next_owner,
        idempotency_key,
        now,
        correlation_id,
        expected_version=expected_version,
        standing_context=standing_context,
    )


def authorize_retry(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    *,
    new_max_attempts: int,
    reason: str,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Approval | DomainError:
    try:
        unit = _locked_unit(session, unit_id)
        existing = session.scalar(
            select(Approval).where(Approval.idempotency_key == idempotency_key)
        )
        if existing is not None:
            event = session.get(Event, existing.event_id)
            expected = (
                existing.subject_type == "retry"
                and existing.subject_id == unit.id
                and existing.approved_by == actor.actor_id
                and existing.reason == reason
                and event is not None
                and event.payload.get("new_max_attempts") == new_max_attempts
                and event.payload.get("expected_version") == expected_version
            )
            if not expected:
                raise _idempotency_conflict()
            session.commit()
            return existing
        if session.scalar(select(Event.id).where(Event.idempotency_key == idempotency_key)):
            raise _idempotency_conflict()
        _require_retry_allowed(unit, actor, new_max_attempts, expected_version)

        bound_version = unit.version
        now = TransactionClock().now(session)
        event_id = uuid.uuid4()
        approval = Approval(
            subject_type="retry",
            subject_id=unit.id,
            subject_revision_or_fingerprint=str(bound_version),
            decision="approved",
            approved_by=actor.actor_id,
            reason=reason,
            created_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
        )
        session.add(approval)
        session.flush()
        unit.max_attempts = new_max_attempts
        _transition(
            session,
            unit,
            WorkUnitState.READY,
            actor=ActorContext(actor.actor_id, ActorRole.SYSTEM),
            idempotency_key=idempotency_key,
            occurred_at=now,
            event_id=event_id,
            payload={
                "approval_id": str(approval.id),
                "approved_by": actor.actor_id,
                "new_max_attempts": new_max_attempts,
                "expected_version": expected_version,
            },
        )
        session.commit()
        return approval
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def _locked_unit(session: Session, unit_id: uuid.UUID) -> WorkUnit:
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def _claim_context_snapshot(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    idempotency_key: str,
    standing_context: dict[str, Any] | None,
) -> ContextSnapshot | None:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if standing_context is None:
        if _has_required_context(revision):
            raise DomainError("context_missing_required", "standing context is incomplete", None)
        return None
    return require_claim_context(
        session,
        PreflightCommand(
            work_unit_id=unit.id,
            standing_context=standing_context,
            previous_context_snapshot_id=None,
            approval_id=None,
            purpose="claim",
            idempotency_key=f"{idempotency_key}:claim-context",
        ),
        actor,
    )


def _has_required_context(revision: WorkPackageRevision) -> bool:
    required = revision.enforcement_snapshot.get("required_context")
    return isinstance(required, dict) and bool(required)


def _require_retry_allowed(
    unit: WorkUnit,
    actor: ActorContext,
    new_max_attempts: int,
    expected_version: int | None,
) -> None:
    if actor.role is not ActorRole.HUMAN or not actor.actor_id:
        raise DomainError("human_actor_required", "retry requires a registered human actor", None)
    if expected_version is not None:
        _require_version(unit, expected_version)
    if WorkUnitState(unit.state) is not WorkUnitState.FAILED:
        raise DomainError("retry_not_allowed", "only failed work may be retried", None)
    if unit.attempt_count < unit.max_attempts:
        raise DomainError("attempts_not_exhausted", "attempt budget is not exhausted", None)
    if new_max_attempts <= unit.attempt_count:
        raise DomainError(
            "retry_budget_required",
            "retry approval must increase the approved attempt limit",
            None,
        )


def _current_claim(session: Session, unit_id: uuid.UUID) -> Claim | None:
    return session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit_id)
        .order_by(Claim.attempt.desc())
        .limit(1)
        .with_for_update()
    )


def _validate_reclaim_roles(actor: ActorContext, next_owner: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError("role_forbidden", "only the system may reclaim leases", None)
    if next_owner.role is not ActorRole.WORKER:
        raise DomainError("role_forbidden", "reclaimed work requires a worker", None)


def _validate_expired_active_claim(unit: WorkUnit, claim: Claim, now: datetime) -> None:
    if claim.released_at is not None or claim.lease_expires_at > now:
        raise DomainError("lease_not_expired", "claim lease has not expired", None)
    if WorkUnitState(unit.state) not in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}:
        raise DomainError("claim_not_active", "work unit has no active claim", None)


def _reclaim_eligibility_error(session: Session, unit: WorkUnit) -> DomainError | None:
    if unit.attempt_count >= unit.max_attempts:
        return DomainError("attempts_exhausted", "attempt budget is exhausted", "approve_retry")
    if evaluate_readiness(session, unit.id).status is not ReadinessStatus.READY:
        return DomainError(
            "readiness_not_satisfied",
            "work unit is no longer ready after lease expiry",
            "resolve_readiness",
        )
    return None


def _claim_replay(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    idempotency_key: str,
    *,
    expected_version: int | None = None,
    standing_context: dict[str, Any] | None = None,
) -> LeaseGrant | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    claim = session.scalar(
        select(Claim).where(Claim.work_unit_id == unit.id, Claim.idempotency_key == idempotency_key)
    )
    if (
        claim is None
        or event.action != "work_unit.transitioned"
        or event.subject_id != unit.id
        or event.to_state != WorkUnitState.CLAIMED
        or claim.claimed_by != actor.actor_id
        or actor.role is not ActorRole.WORKER
        or event.payload.get("expected_version") != expected_version
    ):
        raise _idempotency_conflict()
    if not _claim_context_replay_matches(session, claim, standing_context):
        raise _idempotency_conflict()
    return LeaseGrant(
        claim.id,
        claim.attempt,
        "",
        claim.lease_expires_at,
        claim.context_snapshot_id,
    )


def _claim_context_replay_matches(
    session: Session,
    claim: Claim,
    standing_context: dict[str, Any] | None,
) -> bool:
    if claim.context_snapshot_id is None:
        return standing_context is None
    if standing_context is None:
        return False
    snapshot = session.get(ContextSnapshot, claim.context_snapshot_id)
    return snapshot is not None and snapshot.context_fingerprint == context_fingerprint(
        standing_context
    )


def _renew_replay(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    attempt: int,
    lease_token: str,
    idempotency_key: str,
    expected_version: int | None,
) -> LeaseGrant | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    claim = _current_claim(session, unit.id)
    expected = (
        claim is not None
        and event.action == "claim.renewed"
        and event.subject_id == unit.id
        and event.actor_id == actor.actor_id
        and actor.role is ActorRole.WORKER
        and event.payload.get("attempt") == attempt
        and event.payload.get("expected_version") == expected_version
        and event.payload.get("lease_token_hash") == hash_lease_token(lease_token)
    )
    if not expected:
        raise _idempotency_conflict()
    assert claim is not None
    expires_at = event.payload.get("expires_at")
    if not isinstance(expires_at, str):
        raise DomainError("event_invalid", "renewal event has no valid expiry", None)
    try:
        replay_expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        raise DomainError("event_invalid", "renewal event has no valid expiry", None) from None
    return LeaseGrant(claim.id, claim.attempt, "", replay_expiry, claim.context_snapshot_id)


def _require_version(unit: WorkUnit, expected_version: int) -> None:
    if unit.version != expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )


def _reclaim_error_replay(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    next_owner: ActorContext,
    idempotency_key: str,
    expected_version: int | None,
) -> DomainError | None:
    event = session.scalar(
        select(Event).where(Event.idempotency_key == f"{idempotency_key}:failed")
    )
    if event is None:
        return None
    error_code = event.payload.get("result_error_code")
    expected = (
        event.subject_id == unit.id
        and event.from_state in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}
        and event.to_state == WorkUnitState.FAILED
        and event.payload.get("reclaim_actor_id") == actor.actor_id
        and actor.role is ActorRole.SYSTEM
        and event.payload.get("next_owner_id") == next_owner.actor_id
        and next_owner.role is ActorRole.WORKER
        and event.payload.get("expected_version") == expected_version
        and error_code in {"attempts_exhausted", "readiness_not_satisfied"}
    )
    if not expected:
        raise _idempotency_conflict()
    if error_code == "attempts_exhausted":
        return DomainError(error_code, "attempt budget is exhausted", "approve_retry")
    return DomainError(
        "readiness_not_satisfied",
        "work unit is no longer ready after lease expiry",
        "resolve_readiness",
    )


def _claim_owned_by(claim: Claim, actor: ActorContext, attempt: int, lease_token: str) -> bool:
    return (
        actor.role is ActorRole.WORKER
        and claim.claimed_by == actor.actor_id
        and claim.attempt == attempt
        and secrets.compare_digest(claim.lease_token_hash, hash_lease_token(lease_token))
    )


def _acquire_reclaimed_claim(
    session: Session,
    unit: WorkUnit,
    owner: ActorContext,
    idempotency_key: str,
    now: datetime,
    correlation_id: uuid.UUID,
    *,
    expected_version: int | None = None,
    standing_context: dict[str, Any] | None = None,
) -> LeaseGrant:
    context_snapshot = _claim_context_snapshot(
        session,
        unit,
        owner,
        idempotency_key,
        standing_context,
    )
    token = secrets.token_urlsafe(32)
    unit.attempt_count += 1
    claim = Claim(
        work_unit_id=unit.id,
        attempt=unit.attempt_count,
        claimed_by=owner.actor_id,
        lease_token_hash=hash_lease_token(token),
        idempotency_key=idempotency_key,
        context_snapshot_id=context_snapshot.id if context_snapshot is not None else None,
        acquired_at=now,
        lease_expires_at=now + LEASE_DURATION,
    )
    session.add(claim)
    session.flush()
    _transition(
        session,
        unit,
        WorkUnitState.CLAIMED,
        actor=ActorContext(owner.actor_id, ActorRole.SYSTEM),
        idempotency_key=idempotency_key,
        occurred_at=now,
        correlation_id=correlation_id,
        payload={
            "claim_id": str(claim.id),
            "attempt": claim.attempt,
            "expected_version": expected_version,
            "context_snapshot_id": (
                str(context_snapshot.id) if context_snapshot is not None else None
            ),
        },
    )
    return LeaseGrant(
        claim.id,
        claim.attempt,
        token,
        claim.lease_expires_at,
        claim.context_snapshot_id,
    )


def _transition(
    session: Session,
    unit: WorkUnit,
    target: WorkUnitState,
    *,
    actor: ActorContext,
    idempotency_key: str,
    occurred_at: datetime,
    correlation_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> Event:
    source = WorkUnitState(unit.state)
    authorize_transition(source, target, actor.role, TransitionGuards())
    unit.state = target
    unit.version += 1
    event = Event(
        id=event_id or uuid.uuid4(),
        occurred_at=occurred_at,
        actor_id=actor.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=source,
        to_state=target,
        payload={**(payload or {}), "version": unit.version},
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
