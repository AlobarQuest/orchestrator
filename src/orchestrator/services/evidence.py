import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Claim,
    ContextSnapshot,
    Event,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext

NON_WAIVER_OUTCOMES = frozenset({"passed", "failed", "not_applicable"})
IDEMPOTENCY_LOCK_NAMESPACE = 0x57503338


def list_evidence(session: Session, work_unit_id: uuid.UUID) -> tuple[Evidence, ...]:
    if session.get(WorkUnit, work_unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(Evidence)
            .where(Evidence.work_unit_id == work_unit_id)
            .order_by(Evidence.recorded_at, Evidence.id)
        )
    )


def append_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
    context_snapshot_id: uuid.UUID | None = None,
) -> Evidence | DomainError:
    return _store_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        attempt=attempt,
        actor=actor,
        lease_token=lease_token,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        context_snapshot_id=context_snapshot_id,
        supersede=False,
    )


def supersede_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
    context_snapshot_id: uuid.UUID | None = None,
) -> Evidence | DomainError:
    return _store_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        attempt=attempt,
        actor=actor,
        lease_token=lease_token,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        context_snapshot_id=context_snapshot_id,
        supersede=True,
    )


def append_verifier_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Evidence | DomainError:
    return _store_verifier_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        actor=actor,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )


def current_evidence(
    session: Session,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
) -> Evidence | None:
    rows = tuple(
        session.scalars(
            select(Evidence).where(
                Evidence.work_package_revision_id == work_package_revision_id,
                Evidence.work_unit_id == work_unit_id,
                Evidence.ac_id == ac_id,
            )
        )
    )
    return _terminal(rows, "supersedes_evidence_id", "evidence_chain_invalid")


def record_adjudication(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    outcome: str,
    actor: ActorContext,
    rationale: str,
    idempotency_key: str,
    expected_version: int | None = None,
    evidence_id: uuid.UUID | None = None,
    failed_evidence_id: uuid.UUID | None = None,
    risk: str | None = None,
    follow_up: str | None = None,
    scope: str | None = None,
    expires_at: datetime | None = None,
) -> Adjudication | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "evidence_id": _uuid_text(evidence_id),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "expected_version": expected_version,
        "failed_evidence_id": _uuid_text(failed_evidence_id),
        "follow_up": follow_up,
        "outcome": outcome,
        "rationale": rationale,
        "risk": risk,
        "scope": scope,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        _lock_idempotency_key(session, idempotency_key)
        unit, revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        del revision
        replay = _adjudication_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        now = TransactionClock().now(session)
        _authorize_outcome(actor, outcome)
        _validate_adjudication_fields(
            session,
            work_package_revision_id,
            work_unit_id,
            ac_id,
            outcome,
            rationale,
            evidence_id,
            failed_evidence_id,
            risk,
            follow_up,
            expires_at,
            now,
        )
        previous = current_adjudication(session, work_package_revision_id, work_unit_id, ac_id)
        event_id = uuid.uuid4()
        row = Adjudication(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            outcome=outcome,
            evidence_id=evidence_id,
            decided_by=actor.actor_id,
            decided_at=now,
            rationale=rationale,
            failed_evidence_id=failed_evidence_id,
            risk=risk,
            follow_up=follow_up,
            scope=scope,
            expires_at=expires_at,
            event_id=event_id,
            supersedes_adjudication_id=previous.id if previous is not None else None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "adjudication.recorded",
                "adjudication",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _adjudication_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def current_adjudication(
    session: Session,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
) -> Adjudication | None:
    rows = tuple(
        session.scalars(
            select(Adjudication).where(
                Adjudication.work_package_revision_id == work_package_revision_id,
                Adjudication.work_unit_id == work_unit_id,
                Adjudication.ac_id == ac_id,
            )
        )
    )
    return _terminal(rows, "supersedes_adjudication_id", "adjudication_chain_invalid")


def _store_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None,
    context_snapshot_id: uuid.UUID | None,
    supersede: bool,
) -> Evidence | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": attempt,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "lease_token_hash": hash_lease_token(lease_token),
        "payload": payload,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "supersede": supersede,
        "context_snapshot_id": _uuid_text(context_snapshot_id),
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        _lock_idempotency_key(session, idempotency_key)
        unit, revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        replay = _evidence_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        claim = _validate_attempt(session, unit, actor, attempt, lease_token)
        bound_context_snapshot_id = _resolve_context_snapshot_id(
            session,
            unit,
            revision,
            claim,
            actor,
            attempt,
            context_snapshot_id,
        )
        command["context_snapshot_id"] = _uuid_text(bound_context_snapshot_id)
        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        if supersede and previous is None:
            raise DomainError(
                "evidence_not_found", "there is no current evidence to supersede", None
            )
        if not supersede and previous is not None:
            raise DomainError(
                "evidence_already_exists",
                "current evidence must be superseded rather than replaced",
                "supersede evidence",
            )
        now = TransactionClock().now(session)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=attempt,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload=payload,
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            supersedes_evidence_id=previous.id if supersede and previous is not None else None,
            context_snapshot_id=bound_context_snapshot_id,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recorded",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def _store_verifier_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None,
) -> Evidence | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": 1,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "payload": payload,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "context_snapshot_id": None,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        _lock_idempotency_key(session, idempotency_key)
        unit, _revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        replay = _evidence_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        _authorize_verifier_evidence(actor)
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        now = TransactionClock().now(session)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=1,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload=payload,
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            supersedes_evidence_id=previous.id if previous is not None else None,
            context_snapshot_id=None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recorded",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def _validated_subject(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
) -> tuple[WorkUnit, WorkPackageRevision]:
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == unit_id).with_for_update())
    revision = session.get(WorkPackageRevision, revision_id)
    acceptance_criteria = (
        revision.enforcement_snapshot.get("acceptance_criteria") if revision is not None else None
    )
    if (
        unit is None
        or revision is None
        or unit.work_package_revision_id != revision_id
        or not isinstance(acceptance_criteria, list)
        or ac_id not in acceptance_criteria
    ):
        raise DomainError(
            "evidence_subject_invalid",
            "package revision, work unit, and acceptance criterion do not match",
            None,
        )
    return unit, revision


def _validate_evidence_fields(
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    evidence_type: str,
    source_revision: str,
) -> None:
    if not _text(stable_ref) and payload is None:
        raise DomainError(
            "evidence_required", "stable reference or structured payload is required", None
        )
    if payload is not None and not isinstance(payload, dict):
        raise DomainError("evidence_invalid", "evidence payload must be structured", None)
    if not _text(evidence_type) or not _text(source_revision):
        raise DomainError(
            "evidence_invalid", "evidence type and source revision are required", None
        )


def _validate_attempt(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    attempt: int,
    lease_token: str,
) -> Claim:
    if actor.role is not ActorRole.WORKER:
        raise DomainError("role_forbidden", "only workers may record evidence", None)
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc())
        .limit(1)
        .with_for_update()
    )
    now = TransactionClock().now(session)
    if claim is None:
        raise DomainError("claim_not_owned", "claim credentials do not match", None)
    owned = (
        claim.attempt == attempt
        and claim.claimed_by == actor.actor_id
        and secrets.compare_digest(claim.lease_token_hash, hash_lease_token(lease_token))
    )
    if not owned:
        raise DomainError("claim_not_owned", "claim credentials do not match", None)
    if (
        claim.released_at is not None
        or claim.lease_expires_at <= now
        or WorkUnitState(unit.state) not in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}
    ):
        raise DomainError("claim_not_active", "work unit has no active claim", None)
    return claim


def _resolve_context_snapshot_id(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    claim: Claim,
    actor: ActorContext,
    attempt: int,
    context_snapshot_id: uuid.UUID | None,
) -> uuid.UUID | None:
    selected_id = context_snapshot_id or claim.execution_context_snapshot_id
    if selected_id is None:
        if _has_required_context(revision):
            raise DomainError("context_missing_required", "standing context is incomplete", None)
        return None
    snapshot = session.get(ContextSnapshot, selected_id)
    valid = (
        snapshot is not None
        and snapshot.work_package_revision_id == revision.id
        and snapshot.work_unit_id == unit.id
        and snapshot.claim_id == claim.id
        and snapshot.actor_id == actor.actor_id
        and snapshot.actor_role == actor.role
        and snapshot.attempt == attempt
        and snapshot.decision == "accepted"
    )
    if not valid:
        raise DomainError(
            "context_snapshot_invalid",
            "evidence context snapshot does not match the active attempt",
            None,
        )
    assert snapshot is not None
    return snapshot.id


def _has_required_context(revision: WorkPackageRevision) -> bool:
    required = revision.enforcement_snapshot.get("required_context")
    return isinstance(required, dict) and bool(required)


def _authorize_outcome(actor: ActorContext, outcome: str) -> None:
    if outcome == "waived":
        allowed = actor.role is ActorRole.HUMAN
    else:
        allowed = outcome in NON_WAIVER_OUTCOMES and actor.role is ActorRole.VERIFIER
    if not allowed:
        raise DomainError("role_forbidden", "actor may not record this outcome", None)


def _authorize_verifier_evidence(actor: ActorContext) -> None:
    if actor.role is not ActorRole.VERIFIER:
        raise DomainError("role_forbidden", "only verifiers may record verifier evidence", None)


def _validate_adjudication_fields(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
    outcome: str,
    rationale: str,
    evidence_id: uuid.UUID | None,
    failed_evidence_id: uuid.UUID | None,
    risk: str | None,
    follow_up: str | None,
    expires_at: datetime | None,
    now: datetime,
) -> None:
    if not _text(rationale):
        code = "waiver_invalid" if outcome == "waived" else "adjudication_invalid"
        raise DomainError(code, "adjudication rationale is required", None)
    for reference in (evidence_id, failed_evidence_id):
        if reference is not None:
            _validate_evidence_reference(session, reference, revision_id, unit_id, ac_id)
    if outcome == "waived" and (
        failed_evidence_id is None
        or not _text(risk)
        or not _text(follow_up)
        or (expires_at is not None and expires_at <= now)
    ):
        raise DomainError(
            "waiver_invalid",
            "waiver requires failed evidence, risk, follow-up, and a future expiry when set",
            None,
        )


def _validate_evidence_reference(
    session: Session,
    evidence_id: uuid.UUID,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
) -> None:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None or (
        evidence.work_package_revision_id,
        evidence.work_unit_id,
        evidence.ac_id,
    ) != (revision_id, unit_id, ac_id):
        raise DomainError(
            "evidence_subject_invalid", "referenced evidence belongs to another subject", None
        )


def _evidence_replay(
    session: Session, idempotency_key: str, command: dict[str, object]
) -> Evidence | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    row = session.scalar(select(Evidence).where(Evidence.idempotency_key == idempotency_key))
    if event is None and row is None:
        return None
    if row is None:
        raise _idempotency_conflict()
    assert row is not None
    command_context_snapshot_id = command.get("context_snapshot_id")
    expected_command = command | {
        "context_snapshot_id": (
            command_context_snapshot_id
            if command_context_snapshot_id is not None
            else _uuid_text(row.context_snapshot_id)
        )
    }
    if (
        event is None
        or event.action != "evidence.recorded"
        or event.subject_id != row.id
        or event.payload.get("command") != expected_command
    ):
        raise _idempotency_conflict()
    return row


def _adjudication_replay(
    session: Session, idempotency_key: str, command: dict[str, object]
) -> Adjudication | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    if event.action != "adjudication.recorded" or event.payload.get("command") != command:
        raise _idempotency_conflict()
    row = session.get(Adjudication, event.subject_id)
    if row is None:
        raise _idempotency_conflict()
    return row


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _evidence_race_result(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    error: IntegrityError,
) -> Evidence | DomainError:
    try:
        replay = _evidence_replay(session, idempotency_key, command)
    except DomainError as conflict:
        return conflict
    if replay is not None:
        return replay
    raise error


def _adjudication_race_result(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    error: IntegrityError,
) -> Adjudication | DomainError:
    try:
        replay = _adjudication_replay(session, idempotency_key, command)
    except DomainError as conflict:
        return conflict
    if replay is not None:
        return replay
    raise error


def _event(
    event_id: uuid.UUID,
    occurred_at: datetime,
    actor: ActorContext,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID,
    command: dict[str, object],
    idempotency_key: str,
) -> Event:
    return Event(
        id=event_id,
        occurred_at=occurred_at,
        actor_id=actor.actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        from_state=None,
        to_state=None,
        payload={"command": command},
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )


def _terminal(rows: tuple[Any, ...], link_name: str, error_code: str) -> Any | None:
    if not rows:
        return None
    by_id = {row.id: row for row in rows}
    superseded_ids: set[uuid.UUID] = set()
    for row in rows:
        previous_id = getattr(row, link_name)
        if previous_id is None:
            continue
        if previous_id not in by_id or previous_id in superseded_ids:
            raise DomainError(error_code, "supersession chain is invalid", None)
        superseded_ids.add(previous_id)
    terminal = tuple(row for row in rows if row.id not in superseded_ids)
    if len(terminal) != 1:
        raise DomainError(error_code, "supersession chain has multiple terminals", None)
    return terminal[0]


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )


def _text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _uuid_text(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None
