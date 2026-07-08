import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event, InfraLaneLink, WorkUnit
from orchestrator.services.lifecycle import ActorContext

BWS_TOKEN_SHAPE = re.compile(r"\b0\.[0-9a-fA-F-]{36}\.[A-Za-z0-9_=-]{8,}")
SECRET_KEY_PARTS = ("authorization", "password", "secret", "token")


@dataclass(frozen=True)
class InfraLaneLinkCommand:
    work_unit_id: uuid.UUID
    attempt: int
    actor: ActorContext
    lease_token: str
    status: str
    change_manager_ref: str
    change_manager_url: str | None
    infraops_ref: str | None
    approval_ref: str | None
    rollback_ref: str | None
    verify_ref: str | None
    final_evidence_ref: str | None
    payload: dict[str, Any] | None
    idempotency_key: str
    expected_version: int | None = None


def list_infra_lane_links(
    session: Session, work_unit_id: uuid.UUID
) -> tuple[InfraLaneLink, ...] | DomainError:
    if session.get(WorkUnit, work_unit_id) is None:
        return DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(InfraLaneLink)
            .where(InfraLaneLink.work_unit_id == work_unit_id)
            .order_by(InfraLaneLink.recorded_at, InfraLaneLink.id)
        )
    )


def record_infra_lane_link(
    session: Session,
    command: InfraLaneLinkCommand,
) -> InfraLaneLink | DomainError:
    try:
        result = _record_infra_lane_link(session, command)
        session.commit()
        return result
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def _record_infra_lane_link(session: Session, command: InfraLaneLinkCommand) -> InfraLaneLink:
    payload = _command_payload(command)
    existing = session.scalar(
        select(InfraLaneLink).where(InfraLaneLink.idempotency_key == command.idempotency_key)
    )
    if existing is not None:
        event = session.get(Event, existing.event_id)
        if event is None or event.payload.get("command") != payload:
            raise _idempotency_conflict()
        return existing
    event_with_key = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if event_with_key is not None:
        raise _idempotency_conflict()

    _validate_command(command)
    unit = session.scalar(
        select(WorkUnit).where(WorkUnit.id == command.work_unit_id).with_for_update()
    )
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if command.expected_version is not None and unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    claim = _current_claim(session, unit.id)
    if claim is None or not _claim_owned_by(claim, command):
        raise DomainError("claim_not_owned", "claim credentials do not match", None)
    now = TransactionClock().now(session)
    if claim.released_at is not None or claim.lease_expires_at <= now:
        raise DomainError("lease_expired", "claim lease has expired", "reclaim")
    if WorkUnitState(unit.state) not in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}:
        raise DomainError("claim_not_active", "work unit has no active claim", None)

    link_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="infra_lane_link.recorded",
            subject_type="infra_lane_link",
            subject_id=link_id,
            from_state=unit.state,
            to_state=unit.state,
            payload={"command": payload},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()
    row = InfraLaneLink(
        id=link_id,
        work_unit_id=unit.id,
        work_package_revision_id=unit.work_package_revision_id,
        attempt=command.attempt,
        status=command.status,
        change_manager_ref=command.change_manager_ref,
        change_manager_url=command.change_manager_url,
        infraops_ref=command.infraops_ref,
        approval_ref=command.approval_ref,
        rollback_ref=command.rollback_ref,
        verify_ref=command.verify_ref,
        final_evidence_ref=command.final_evidence_ref,
        payload=command.payload or {},
        recorded_by=command.actor.actor_id,
        recorded_at=now,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def _validate_command(command: InfraLaneLinkCommand) -> None:
    if command.actor.role is not ActorRole.WORKER:
        raise DomainError(
            "role_forbidden",
            "only a claimed worker may record infra-lane linkage",
            None,
        )
    if command.attempt <= 0:
        raise DomainError("infra_link_attempt_invalid", "attempt must be positive", None)
    if not command.change_manager_ref.strip():
        raise DomainError(
            "infra_link_invalid",
            "change-manager reference is required",
            None,
        )
    secret_path = _secret_metadata_path(
        {
            "change_manager_ref": command.change_manager_ref,
            "change_manager_url": command.change_manager_url,
            "infraops_ref": command.infraops_ref,
            "approval_ref": command.approval_ref,
            "rollback_ref": command.rollback_ref,
            "verify_ref": command.verify_ref,
            "final_evidence_ref": command.final_evidence_ref,
            "payload": command.payload,
        }
    )
    if secret_path is not None:
        raise DomainError(
            "infra_link_secret_rejected",
            f"infra-lane link metadata contains secret-shaped content at {secret_path}",
            "store only non-secret references",
        )


def _current_claim(session: Session, unit_id: uuid.UUID) -> Claim | None:
    return session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit_id)
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc())
        .limit(1)
    )


def _claim_owned_by(claim: Claim, command: InfraLaneLinkCommand) -> bool:
    return (
        claim.attempt == command.attempt
        and claim.claimed_by == command.actor.actor_id
        and secrets.compare_digest(claim.lease_token_hash, hash_lease_token(command.lease_token))
    )


def _secret_metadata_path(value: object, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            child_path = f"{path}.{key_text}"
            if any(part in lowered for part in SECRET_KEY_PARTS):
                return child_path
            found = _secret_metadata_path(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _secret_metadata_path(child, f"{path}[{index}]")
            if found is not None:
                return found
    elif isinstance(value, str):
        lowered = value.lower()
        if "authorization: bearer " in lowered or BWS_TOKEN_SHAPE.search(value):
            return path
    return None


def _command_payload(command: InfraLaneLinkCommand) -> dict[str, object]:
    raw = asdict(command)
    actor = raw.pop("actor")
    raw.pop("lease_token")
    raw["actor_id"] = actor["actor_id"]
    raw["actor_role"] = actor["role"]
    raw["work_unit_id"] = str(command.work_unit_id)
    raw["payload"] = command.payload or {}
    return raw


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different infra-lane linkage",
        "use a new idempotency key",
    )
