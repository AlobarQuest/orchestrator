import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.config import get_settings
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import (
    LEASE_DURATION,
    MIN_PRODUCTION_DRILL_DEADLINE_SECONDS,
)
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    Claim,
    Event,
    Evidence,
    Observation,
    ProductionDrillResource,
    ProductionDrillRun,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext

PRODUCTION_DRILL_IDEMPOTENCY_LOCK_NAMESPACE = 0x5044524C


@dataclass(frozen=True)
class StartProductionDrill:
    revision_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int
    image_ref: str
    image_digest: str
    openapi_digest: str
    lease_duration_seconds: int = MIN_PRODUCTION_DRILL_DEADLINE_SECONDS
    reporting_deadline_seconds: int = MIN_PRODUCTION_DRILL_DEADLINE_SECONDS


@dataclass(frozen=True)
class ProductionDrillDeadlines:
    lease_duration: timedelta
    reporting_deadline: timedelta


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


def production_drill_deadlines(
    session: Session, run_id: uuid.UUID
) -> ProductionDrillDeadlines | DomainError:
    run = production_drill_run(session, run_id)
    if isinstance(run, DomainError):
        return run
    event = session.scalar(
        select(Event).where(
            Event.action == "production_drill.started",
            Event.subject_type == "production_drill_run",
            Event.subject_id == run_id,
        )
    )
    command = event.payload.get("command") if event is not None else None
    if (
        event is None
        or event.actor_id != run.owner_actor_id
        or not isinstance(command, dict)
        or command.get("actor_role") != ActorRole.HUMAN.value
    ):
        return DomainError(
            "production_drill_human_authorization_required",
            "production drill run has no human authorization record",
            None,
        )
    deadlines = event.payload.get("deadlines")
    if not isinstance(deadlines, dict):
        return DomainError(
            "production_drill_deadlines_missing", "production drill deadlines missing", None
        )
    try:
        lease_seconds = int(deadlines["lease_duration_seconds"])
        reporting_seconds = int(deadlines["reporting_deadline_seconds"])
    except (KeyError, TypeError, ValueError):
        return DomainError(
            "production_drill_deadlines_invalid", "production drill deadlines invalid", None
        )
    return ProductionDrillDeadlines(
        timedelta(seconds=lease_seconds), timedelta(seconds=reporting_seconds)
    )


def lease_duration_for_work_unit(session: Session, unit_id: uuid.UUID) -> timedelta:
    resource = session.scalar(
        select(ProductionDrillResource).where(
            ProductionDrillResource.resource_type == "work_unit",
            ProductionDrillResource.resource_id == unit_id,
        )
    )
    if resource is None:
        return LEASE_DURATION
    deadlines = production_drill_deadlines(session, resource.run_id)
    if isinstance(deadlines, ProductionDrillDeadlines):
        return deadlines.lease_duration
    return LEASE_DURATION


def production_drill_state(session: Session, run_id: uuid.UUID) -> dict[str, object] | DomainError:
    deadlines = production_drill_deadlines(session, run_id)
    if isinstance(deadlines, DomainError):
        return deadlines
    run = session.get(ProductionDrillRun, run_id)
    assert run is not None
    unit_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "work_unit",
    )
    units = session.scalars(
        select(WorkUnit).where(WorkUnit.id.in_(unit_ids)).order_by(WorkUnit.id)
    ).all()
    now = TransactionClock().now(session)
    claims = {
        claim.work_unit_id: claim
        for claim in session.scalars(
            select(Claim).where(
                Claim.work_unit_id.in_(unit_ids),
                Claim.released_at.is_(None),
                Claim.lease_expires_at > now,
            )
        )
    }
    evidence_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "evidence",
    )
    evidence = session.scalars(
        select(Evidence).where(Evidence.id.in_(evidence_ids)).order_by(Evidence.id)
    ).all()
    observation_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "observation",
    )
    observations = session.scalars(
        select(Observation).where(Observation.id.in_(observation_ids)).order_by(Observation.id)
    ).all()
    condition_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "reconciliation_condition",
    )
    conditions = session.scalars(
        select(ReconciliationCondition)
        .where(ReconciliationCondition.id.in_(condition_ids))
        .order_by(ReconciliationCondition.id)
    ).all()
    return {
        "run_id": run.id,
        "status": run.status,
        "closed_at": run.closed_at,
        "lease_duration_seconds": int(deadlines.lease_duration.total_seconds()),
        "reporting_deadline_seconds": int(deadlines.reporting_deadline.total_seconds()),
        "units": [_unit_state(unit, claims.get(unit.id)) for unit in units],
        "evidence": [_evidence_state(session, row) for row in evidence],
        "observations": [_observation_state(row) for row in observations],
        "conditions": [_condition_state(session, row) for row in conditions],
    }


def _unit_state(unit: WorkUnit, claim: Claim | None) -> dict[str, object]:
    return {
        "id": unit.id,
        "unit_key": unit.unit_key,
        "state": unit.state,
        "version": unit.version,
        "active_claim": (
            None
            if claim is None
            else {
                "id": claim.id,
                "attempt": claim.attempt,
                "lease_expires_at": claim.lease_expires_at,
            }
        ),
    }


def _evidence_state(session: Session, row: Evidence) -> dict[str, object]:
    return {
        "id": row.id,
        "work_unit_id": row.work_unit_id,
        "ac_id": row.ac_id,
        "supersedes_evidence_id": row.supersedes_evidence_id,
        "is_head": not session.scalar(
            select(exists().where(Evidence.supersedes_evidence_id == row.id))
        ),
    }


def _observation_state(row: Observation) -> dict[str, object]:
    return {
        "id": row.id,
        "observation_type": row.observation_type,
        "status": row.status,
        "observed_at": row.observed_at,
    }


def _condition_state(session: Session, row: ReconciliationCondition) -> dict[str, object]:
    return {
        "id": row.id,
        "work_unit_id": row.work_unit_id,
        "condition_type": row.condition_type,
        "is_open": not session.scalar(
            select(exists().where(ReconciliationResolution.condition_id == row.id))
        ),
    }


def _start_production_drill(session: Session, command: StartProductionDrill) -> ProductionDrillRun:
    _require_human(command.actor)
    _require_deadlines(command)
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "production drill start requires expected version 0",
            "reload",
            current_version=0,
        )
    payload = _command_payload(command)
    _lock_idempotency_key(session, command.idempotency_key)
    existing_event = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if existing_event is not None:
        return _replayed_run(session, existing_event, payload)

    revision = session.get(WorkPackageRevision, command.revision_id, with_for_update=True)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    authorization = _revision_approval_provenance(revision)

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
            payload={
                "command": payload,
                "authorization": authorization,
                "deadlines": _deadline_payload(command),
            },
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


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": PRODUCTION_DRILL_IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _revision_approval_provenance(revision: WorkPackageRevision) -> dict[str, str]:
    if not revision.approved_by or revision.approved_at is None or not revision.approval_event_id:
        raise DomainError(
            "production_drill_revision_approval_required",
            "an approved package revision is required to start a production drill",
            "register an approved package revision before starting the production drill",
        )
    return {
        "revision_approved_by": revision.approved_by,
        "revision_approved_at": revision.approved_at.isoformat(),
        "revision_approval_event_id": revision.approval_event_id,
    }


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
        "lease_duration_seconds": command.lease_duration_seconds,
        "reporting_deadline_seconds": command.reporting_deadline_seconds,
    }


def _deadline_payload(command: StartProductionDrill) -> dict[str, int]:
    return {
        "lease_duration_seconds": command.lease_duration_seconds,
        "reporting_deadline_seconds": command.reporting_deadline_seconds,
    }


def _require_deadlines(command: StartProductionDrill) -> None:
    max_deadline_seconds = get_settings().production_drill_max_deadline_seconds
    for value in (command.lease_duration_seconds, command.reporting_deadline_seconds):
        if value < MIN_PRODUCTION_DRILL_DEADLINE_SECONDS:
            raise DomainError(
                "production_drill_deadline_too_short",
                "production drill deadlines must be at least 60 seconds",
                None,
            )
        if value > max_deadline_seconds:
            raise DomainError(
                "production_drill_deadline_too_long",
                "production drill deadline exceeds configured maximum",
                None,
            )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
