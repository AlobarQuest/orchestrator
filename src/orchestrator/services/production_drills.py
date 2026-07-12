import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Approval, Event, ProductionDrillRun, WorkPackageRevision
from orchestrator.services.lifecycle import ActorContext


@dataclass(frozen=True)
class StartProductionDrill:
    revision_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int
    image_ref: str
    image_digest: str
    openapi_digest: str


def start_production_drill(
    session: Session, command: StartProductionDrill
) -> ProductionDrillRun | DomainError:
    try:
        result = _start_production_drill(session, command)
        session.commit()
        return result
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def production_drill_run(session: Session, run_id: uuid.UUID) -> ProductionDrillRun | DomainError:
    run = session.get(ProductionDrillRun, run_id)
    if run is None:
        return DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    return run


def _start_production_drill(session: Session, command: StartProductionDrill) -> ProductionDrillRun:
    _require_human(command.actor)
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "production drill start requires expected version 0",
            "reload",
            current_version=0,
        )
    payload = _command_payload(command)
    existing_event = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if existing_event is not None:
        return _replayed_run(session, existing_event, payload)

    revision = session.get(WorkPackageRevision, command.revision_id, with_for_update=True)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if not _has_authority_approval(session, revision):
        raise DomainError(
            "production_drill_authority_approval_required",
            "an approved authority decision for this package revision is required",
            "record authority approval before starting the production drill",
        )

    now = TransactionClock().now(session)
    run_id = uuid.uuid4()
    session.add(
        Event(
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="production_drill.started",
            subject_type="production_drill_run",
            subject_id=run_id,
            from_state=None,
            to_state="open",
            payload={"command": payload},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()
    run = ProductionDrillRun(
        id=run_id,
        revision_id=revision.id,
        owner_actor_id=command.actor.actor_id,
        opened_at=now,
        closed_at=None,
        status="open",
        image_ref=command.image_ref,
        image_digest=command.image_digest,
        openapi_digest=command.openapi_digest,
        closure_reason=None,
    )
    session.add(run)
    session.flush()
    return run


def _require_human(actor: ActorContext) -> None:
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required", "only a human actor may start a production drill", None
        )


def _has_authority_approval(session: Session, revision: WorkPackageRevision) -> bool:
    return (
        session.scalar(
            select(Approval.id).where(
                Approval.subject_type == "authority",
                Approval.subject_id == revision.id,
                Approval.subject_revision_or_fingerprint == revision.authority_fingerprint,
                Approval.decision == "approved",
            )
        )
        is not None
    )


def _replayed_run(session: Session, event: Event, payload: dict[str, object]) -> ProductionDrillRun:
    if event.action != "production_drill.started" or event.subject_type != "production_drill_run":
        raise _idempotency_conflict()
    if event.payload.get("command") != payload:
        raise _idempotency_conflict()
    run = session.get(ProductionDrillRun, event.subject_id)
    if run is None:
        raise DomainError("event_invalid", "production drill start event has no run", None)
    return run


def _command_payload(command: StartProductionDrill) -> dict[str, object]:
    return {
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role.value,
        "revision_id": str(command.revision_id),
        "expected_version": command.expected_version,
        "image_ref": command.image_ref,
        "image_digest": command.image_digest,
        "openapi_digest": command.openapi_digest,
    }


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
