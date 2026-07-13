import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.config import get_settings
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import (
    LEASE_DURATION,
    MIN_PRODUCTION_DRILL_DEADLINE_SECONDS,
)
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Claim,
    DeploymentObservation,
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
from orchestrator.services.deployment_observations import (
    DeploymentObservationCommand,
    record_production_drill_deployment_observation,
)
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    close_production_drill_unit,
    transition_production_drill_unit,
)
from orchestrator.services.observations import (
    ObservationCommand,
    record_production_drill_observation,
)
from orchestrator.services.packages import (
    _ProductionDrillTemplate,
    _register_fixed_production_drill_template_unit,
)
from orchestrator.services.reconciliation import (
    ResolutionCommand,
    resolve_production_drill_condition,
)
from orchestrator.services.release_artifacts import (
    ReleaseArtifactCommand,
    record_production_drill_release_artifact,
)
from orchestrator.services.runtime_observations import get_runtime_observation

PRODUCTION_DRILL_IDEMPOTENCY_LOCK_NAMESPACE = 0x5044524C
_SCENARIO_ATOMIC_SESSION_KEY = "production_drill_scenario_atomic"
MAX_RUNTIME_OBSERVATION_AGE = timedelta(minutes=5)


@contextmanager
def _scenario_atomic(session: Session):
    """Make a fixed scenario one durable operation despite service-level public wrappers."""
    if session.info.get(_SCENARIO_ATOMIC_SESSION_KEY):  # pragma: no cover - internal misuse guard
        raise RuntimeError("production drill scenario transaction is already active")
    session.info[_SCENARIO_ATOMIC_SESSION_KEY] = True
    try:
        yield
    finally:
        session.info.pop(_SCENARIO_ATOMIC_SESSION_KEY, None)


@dataclass(frozen=True)
class StartProductionDrill:
    revision_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int
    runtime_observation_id: uuid.UUID
    lease_duration_seconds: int = MIN_PRODUCTION_DRILL_DEADLINE_SECONDS
    reporting_deadline_seconds: int = MIN_PRODUCTION_DRILL_DEADLINE_SECONDS


@dataclass(frozen=True)
class ProductionDrillDeadlines:
    lease_duration: timedelta
    reporting_deadline: timedelta


@dataclass(frozen=True)
class CloseProductionDrill:
    run_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int
    closure_reason: str


@dataclass(frozen=True)
class RunProductionDrillScenario:
    run_id: uuid.UUID
    scenario: str
    actor: ActorContext
    idempotency_key: str
    expected_version: int


@dataclass(frozen=True)
class FailProductionDrill:
    run_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    expected_version: int
    failure_code: str
    diagnostic_ref: str


PRODUCTION_DRILL_SCENARIOS = frozenset(
    {
        "crash_recovery",
        "evidence_recovery",
        "external_pr_conflict",
        "deploy_split_brain",
        "stalled_approval",
    }
)
PRODUCTION_DRILL_FAILURE_CODES = frozenset(
    {
        "runner_preflight_failed",
        "crash_recovery_failed",
        "evidence_recovery_failed",
        "external_pr_conflict_failed",
        "deploy_split_brain_failed",
        "stalled_approval_failed",
    }
)
REDACTED_DIAGNOSTIC_REF = re.compile(r"^drill://redacted/[A-Za-z0-9._/-]{1,200}$")


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


def close_production_drill(
    session: Session, command: CloseProductionDrill
) -> ProductionDrillRun | DomainError:
    try:
        result = _close_production_drill(session, command)
        session.commit()
        return result
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def run_production_drill_scenario(
    session: Session, command: RunProductionDrillScenario
) -> dict[str, object] | DomainError:
    try:
        with _scenario_atomic(session):
            _run_production_drill_scenario(session, command)
        session.commit()
        return production_drill_state(session, command.run_id)
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def fail_production_drill(
    session: Session, command: FailProductionDrill
) -> dict[str, object] | DomainError:
    try:
        _fail_production_drill(session, command)
        session.commit()
        return production_drill_state(session, command.run_id)
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
    deployment_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "deployment_observation",
    )
    deployments = session.scalars(
        select(DeploymentObservation)
        .where(DeploymentObservation.id.in_(deployment_ids))
        .order_by(DeploymentObservation.id)
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
        "deployment_observations": [
            {
                "id": row.id,
                "release_artifact_binding_id": row.release_artifact_binding_id,
                "post_deploy_work_unit_id": row.post_deploy_work_unit_id,
            }
            for row in deployments
        ],
        "conditions": [_condition_state(session, row) for row in conditions],
    }


def _unit_state(unit: WorkUnit, claim: Claim | None) -> dict[str, object]:
    return {
        "id": unit.id,
        "unit_key": unit.unit_key,
        "state": unit.state,
        "version": unit.version,
        "attempt_count": unit.attempt_count,
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
    runtime_observation = get_runtime_observation(session, command.runtime_observation_id)
    if isinstance(runtime_observation, DomainError):
        raise runtime_observation
    _require_fresh_runtime_observation(runtime_observation.observed_at, now)
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
        image_ref=runtime_observation.configured_image_ref,
        image_digest=runtime_observation.observed_image_digest,
        openapi_digest=runtime_observation.openapi_sha256,
        runtime_observation_id=runtime_observation.id,
        closure_reason=None,
    )
    session.add(run)
    session.flush()
    return run


def _run_production_drill_scenario(session: Session, command: RunProductionDrillScenario) -> None:
    _require_system(command.actor)
    if command.scenario not in PRODUCTION_DRILL_SCENARIOS:
        raise DomainError(
            "production_drill_scenario_invalid", "unsupported production drill scenario", None
        )
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "production drill scenario requires expected version 0",
            "reload",
            current_version=0,
        )
    _lock_idempotency_key(session, command.idempotency_key)
    payload = _scenario_command_payload(command)
    existing = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if existing is not None:
        if (
            existing.action != f"production_drill.scenario.{command.scenario}"
            or existing.payload.get("command") != payload
        ):
            raise _idempotency_conflict()
        return
    run = session.get(ProductionDrillRun, command.run_id, with_for_update=True)
    if run is None:
        raise DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    if run.status not in {"open", "asserting"}:
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)
    try:
        _execute_fixed_scenario(session, run, command)
    except DomainError as error:
        # A scenario either becomes fully durable below or leaves no synthetic mutation behind.
        # The failure event is intentionally written only after the scenario transaction rolls back.
        session.rollback()
        run = session.get(ProductionDrillRun, command.run_id, with_for_update=True)
        assert run is not None
        _record_scenario_terminal_failure(session, run, command, error)
        return
    now = TransactionClock().now(session)
    session.add(
        Event(
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action=f"production_drill.scenario.{command.scenario}",
            subject_type="production_drill_run",
            subject_id=run.id,
            from_state=run.status,
            to_state="asserting",
            payload={"command": payload, "scenario": command.scenario},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    run.status = "asserting"
    session.flush()


def _execute_fixed_scenario(
    session: Session, run: ProductionDrillRun, command: RunProductionDrillScenario
) -> None:
    """Execute only audited, repository-independent synthetic drill templates.

    The human start event authorizes this one SYSTEM path.  Each derived key includes the run and
    fixed scenario name, so neither units nor external references can be selected by the caller.
    """
    _preflight_fixed_scenario(session, run, command)
    unit = _register_fixed_scenario_unit(session, run, command)

    if command.scenario == "crash_recovery":
        _execute_fixed_crash_recovery(session, run, unit, command)
        return

    if command.scenario == "evidence_recovery":
        _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.READY)
        _execute_fixed_evidence_recovery(session, run, unit, command)
        return

    if command.scenario == "deploy_split_brain":
        _execute_fixed_deploy_split_brain(session, run, unit, command)
        return
    if command.scenario == "external_pr_conflict":
        _execute_fixed_external_pr_conflict(session, run, unit, command)
        return
    if command.scenario == "stalled_approval":
        _execute_fixed_stalled_approval(session, run, unit, command)
        return
    raise AssertionError(f"unhandled production drill scenario {command.scenario}")


def _execute_fixed_crash_recovery(
    session: Session,
    run: ProductionDrillRun,
    unit: WorkUnit,
    command: RunProductionDrillScenario,
) -> None:
    """Prepare one lease before a restart, then reclaim it after the runner resumes.

    The two invocations are deliberately distinguished by durable unit state, not a
    caller-supplied phase.  That keeps the public command fixed while ensuring the
    second invocation exercises the ordinary expired-claim reclaim path.
    """
    from orchestrator.services.claims import _perform_reclaim, claim_unit

    worker = ActorContext("production-drill-worker", ActorRole.WORKER)
    if WorkUnitState(unit.state) is WorkUnitState.DRAFT:
        _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.READY)
        claim = claim_unit(
            session,
            unit.id,
            worker,
            f"{command.idempotency_key}:prepare:claim",
            unit.version,
        )
        if isinstance(claim, DomainError):
            raise claim
        return

    if WorkUnitState(unit.state) is not WorkUnitState.CLAIMED:
        raise DomainError(
            "production_drill_crash_recovery_invalid_state",
            "crash recovery resume requires the prepared synthetic claim",
            None,
        )
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id, Claim.released_at.is_(None))
        .with_for_update()
    )
    if claim is None:
        raise DomainError(
            "production_drill_crash_recovery_claim_missing",
            "crash recovery resume requires an unreleased synthetic claim",
            None,
        )
    _wait_for_lease_expiry(session, claim.lease_expires_at)
    # The public reclaim wrapper commits. This scenario is one atomic audited operation,
    # so it uses the same reclaim implementation inside the surrounding transaction.
    reclaimed = _perform_reclaim(
        session,
        unit.id,
        command.actor,
        worker,
        f"{command.idempotency_key}:resume:reclaim",
        expected_version=unit.version,
    )
    if isinstance(reclaimed, DomainError):
        raise reclaimed


def _execute_fixed_external_pr_conflict(
    session: Session, run: ProductionDrillRun, unit: WorkUnit, command: RunProductionDrillScenario
) -> None:
    """Exercise the real PR binding, submit, observation, and detection path."""
    from orchestrator.services.claims import claim_unit
    from orchestrator.services.evidence import append_production_drill_evidence
    from orchestrator.services.pr_bindings import arm_verification_head, upsert_pr_binding
    from orchestrator.services.production_drill_resources import (
        bind_created_drill_reconciliation_condition,
    )
    from orchestrator.services.reconciliation_detection import detect_observation_conditions

    worker = ActorContext("production-drill-worker", ActorRole.WORKER)
    _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.READY)
    claim = claim_unit(session, unit.id, worker, f"{command.idempotency_key}:claim", unit.version)
    if isinstance(claim, DomainError):
        raise claim
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.EXECUTING,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    head = "a" * 40
    upsert_pr_binding(
        session,
        actor=worker,
        work_unit_id=unit.id,
        pr_number=1,
        head_sha=head,
        attempt=claim.attempt,
        lease_token=claim.lease_token,
    )
    evidence = append_production_drill_evidence(
        session,
        run_id=run.id,
        work_package_revision_id=run.revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=claim.attempt,
        actor=worker,
        lease_token=claim.lease_token,
        evidence_type="production_drill.external_pr_conflict",
        stable_ref=f"drill://redacted/{run.id}/external-pr",
        payload={"scenario": command.scenario, "synthetic": True},
        source_revision="synthetic",
        expected_version=unit.version,
        idempotency_key=f"{command.idempotency_key}:evidence",
    )
    if isinstance(evidence, DomainError):
        raise evidence
    # A head change before verification reads it is expected iteration, not divergence.
    normal_iteration = record_production_drill_observation(
        session,
        run_id=run.id,
        command=ObservationCommand(
            actor=command.actor,
            source_system="github",
            source_reference=f"production-drill:{run.id}:pr:1:normal-iteration",
            source_url=None,
            trust_classification="delivery_system",
            subject_type="work_unit",
            subject_reference=str(unit.id),
            environment="production",
            observation_type="github_pr",
            status="observed",
            severity="info",
            observed_at=TransactionClock().now(session),
            summary="synthetic PR head changed before verification read it",
            facts={"pr_number": 1, "head_sha": "c" * 40, "state": "open", "merged": False},
            payload_digest=None,
            idempotency_key=f"{command.idempotency_key}:normal-iteration",
        ),
    )
    if isinstance(normal_iteration, DomainError):
        raise normal_iteration
    normal_counters = detect_observation_conditions(session, normal_iteration, command.actor)
    if normal_counters.conditions_recorded != 0:
        raise DomainError(
            "production_drill_pr_normal_iteration_alarm",
            "fixed PR normal iteration unexpectedly produced a condition",
            None,
        )
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.SUBMITTED,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    arm_verification_head(session, unit, actor=worker)
    observation = record_production_drill_observation(
        session,
        run_id=run.id,
        command=ObservationCommand(
            actor=command.actor,
            source_system="github",
            source_reference=f"production-drill:{run.id}:pr:1:merged",
            source_url=None,
            trust_classification="delivery_system",
            subject_type="work_unit",
            subject_reference=str(unit.id),
            environment="production",
            observation_type="github_pr",
            status="observed",
            severity="warning",
            observed_at=normal_iteration.observed_at + timedelta(microseconds=1),
            summary="synthetic PR merged outside orchestrator",
            facts={"pr_number": 1, "head_sha": head, "state": "closed", "merged": True},
            payload_digest=None,
            idempotency_key=f"{command.idempotency_key}:observation",
        ),
    )
    if isinstance(observation, DomainError):
        raise observation
    counters = detect_observation_conditions(session, observation, command.actor)
    if counters.conditions_recorded != 1:
        raise DomainError(
            "production_drill_pr_conflict_not_detected",
            "fixed PR conflict did not produce its required condition",
            None,
        )
    condition = session.scalar(
        select(ReconciliationCondition).where(
            ReconciliationCondition.work_unit_id == unit.id,
            ReconciliationCondition.observation_id == observation.id,
            ReconciliationCondition.condition_type == "external_merge_alarm",
        )
    )
    if condition is None:
        raise DomainError(
            "production_drill_pr_conflict_not_detected",
            "fixed PR conflict condition was not persisted",
            None,
        )
    bind_created_drill_reconciliation_condition(session, run.id, condition)


def _execute_fixed_stalled_approval(
    session: Session, run: ProductionDrillRun, unit: WorkUnit, command: RunProductionDrillScenario
) -> None:
    """Create a real HUMAN-only approval gate, then prove it is reported but not requeueable."""
    from orchestrator.services.claims import claim_unit
    from orchestrator.services.dead_letter import dead_letter

    worker = ActorContext("production-drill-worker", ActorRole.WORKER)
    _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.READY)
    claim = claim_unit(session, unit.id, worker, f"{command.idempotency_key}:claim", unit.version)
    if isinstance(claim, DomainError):
        raise claim
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.EXECUTING,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.AWAITING_APPROVAL,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    _wait_for_run_deadline(session, run, unit.updated_at)
    entries = dead_letter(
        session,
        failure_signature_threshold=get_settings().dispatch_failure_signature_threshold,
        stalled_approval_seconds=get_settings().dead_letter_stalled_approval_seconds,
        production_drill_run_id=run.id,
    )
    stalled = [
        entry
        for entry in entries
        if entry.source == "stalled_approval" and entry.work_unit_id == unit.id
    ]
    if len(stalled) != 1 or stalled[0].requeue_eligible:
        raise DomainError(
            "production_drill_stalled_approval_not_detected",
            "fixed approval gate was not reported as non-requeueable",
            None,
        )


def _register_fixed_scenario_unit(
    session: Session, run: ProductionDrillRun, command: RunProductionDrillScenario
) -> WorkUnit:
    return _register_fixed_production_drill_template_unit(
        session,
        run_id=run.id,
        actor_id=command.actor.actor_id,
        actor_role=command.actor.role,
        idempotency_key=f"{command.idempotency_key}:unit",
        template=_ProductionDrillTemplate(command.scenario),
    )


def _preflight_fixed_scenario(
    session: Session, run: ProductionDrillRun, command: RunProductionDrillScenario
) -> None:
    """Reject unavailable fixed templates before they create any drill-owned resource."""
    if session.get(WorkPackageRevision, run.revision_id) is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if command.scenario == "deploy_split_brain":
        deadlines = production_drill_deadlines(session, run.id)
        if isinstance(deadlines, DomainError):
            raise deadlines
        if deadlines.reporting_deadline.total_seconds() <= 0:
            raise DomainError(
                "production_drill_deadlines_invalid",
                "production drill reporting deadline must be positive",
                None,
            )


def _execute_fixed_evidence_recovery(
    session: Session,
    run: ProductionDrillRun,
    unit: WorkUnit,
    command: RunProductionDrillScenario,
) -> None:
    # The template owns this synthetic worker identity. Callers never provide it or its lease.
    from orchestrator.services.claims import claim_unit
    from orchestrator.services.evidence import append_production_drill_evidence, recover_evidence
    from orchestrator.services.production_drill_resources import bind_created_drill_evidence

    worker = ActorContext("production-drill-worker", ActorRole.WORKER)
    claim = claim_unit(
        session,
        unit.id,
        worker,
        f"{command.idempotency_key}:claim",
        expected_version=unit.version,
    )
    if isinstance(claim, DomainError):
        raise claim
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.EXECUTING,
        actor=worker,
        attempt=claim.attempt,
        lease_token=claim.lease_token,
    )
    evidence_args = {
        "work_package_revision_id": run.revision_id,
        "work_unit_id": unit.id,
        "ac_id": "ac-1",
        "attempt": claim.attempt,
        "actor": worker,
        "lease_token": claim.lease_token,
        "evidence_type": "production_drill.evidence_recovery",
        "stable_ref": f"drill://redacted/{run.id}/evidence-recovery",
        "payload": {"scenario": "evidence_recovery", "synthetic": True},
        "source_revision": "synthetic",
        "expected_version": unit.version,
    }
    evidence = append_production_drill_evidence(
        session,
        run_id=run.id,
        idempotency_key=f"{command.idempotency_key}:evidence:initial",
        **evidence_args,
    )
    if isinstance(evidence, DomainError):
        raise evidence
    _wait_for_lease_expiry(session, claim.expires_at)
    locked_out = append_production_drill_evidence(
        session,
        run_id=run.id,
        idempotency_key=f"{command.idempotency_key}:evidence:worker-lockout",
        **evidence_args,
    )
    if not isinstance(locked_out, DomainError) or locked_out.code != "claim_not_active":
        raise DomainError(
            "production_drill_worker_not_locked_out",
            "expired worker unexpectedly retained evidence write access",
            None,
        )
    recovered = recover_evidence(
        session,
        work_package_revision_id=run.revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=claim.attempt,
        actor=command.actor,
        evidence_type="production_drill.evidence_recovery",
        stable_ref=f"drill://redacted/{run.id}/evidence-recovery",
        payload={"scenario": "evidence_recovery", "synthetic": True},
        source_revision="synthetic",
        idempotency_key=f"{command.idempotency_key}:evidence:recovered",
    )
    if isinstance(recovered, DomainError):
        raise recovered
    bind_created_drill_evidence(session, run.id, recovered)
    state = production_drill_state(session, run.id)
    assert not isinstance(state, DomainError)
    evidence_state = state["evidence"]
    assert isinstance(evidence_state, list)
    heads = [row for row in evidence_state if isinstance(row, dict) and row.get("is_head") is True]
    if len(heads) != 1 or heads[0].get("id") != recovered.id:
        raise DomainError(
            "production_drill_evidence_recovery_invalid_head",
            "recovered evidence did not leave exactly one superseding head",
            None,
        )


def _execute_fixed_deploy_split_brain(
    session: Session,
    run: ProductionDrillRun,
    unit: WorkUnit,
    command: RunProductionDrillScenario,
) -> None:
    """Exercise the real AC-003 path, never a hand-written condition."""
    from orchestrator.services.reconciliation_detection import detect_reconciliation_conditions

    _complete_fixed_unit(session, run, unit, command)
    revision = session.get(WorkPackageRevision, run.revision_id)
    assert revision is not None
    binding = record_production_drill_release_artifact(
        session,
        run_id=run.id,
        command=ReleaseArtifactCommand(
            work_unit_id=unit.id,
            actor=command.actor,
            package_revision_id=run.revision_id,
            package_revision_hash=revision.content_hash,
            source_repository="production-drill/synthetic",
            implementation_pr_number=1,
            source_commit="a" * 40,
            merge_commit="b" * 40,
            artifact_registry="example.invalid",
            artifact_repository="production-drill",
            artifact_name="deploy-split-brain",
            artifact_digest="sha256:" + "c" * 64,
            artifact_tag=None,
            workflow_run_id="production-drill",
            workflow_run_attempt=1,
            workflow_path="synthetic",
            workflow_ref="synthetic",
            workflow_run_url=None,
            builder_id="production-drill",
            builder_class="synthetic",
            provenance_ref=None,
            provenance_digest=None,
            sbom_ref=None,
            sbom_digest=None,
            summary={"synthetic": True, "scenario": command.scenario},
            idempotency_key=f"{command.idempotency_key}:release-artifact",
            expected_version=unit.version,
        ),
    )
    if isinstance(binding, DomainError):
        raise binding
    deployment = record_production_drill_deployment_observation(
        session,
        run_id=run.id,
        command=DeploymentObservationCommand(
            release_artifact_binding_id=binding.id,
            actor=command.actor,
            environment="production-drill",
            base_url="https://production-drill.invalid",
            observed_artifact_digest=binding.artifact_digest,
            deployment_ref=f"production-drill:{run.id}",
            deployment_url="https://production-drill.invalid/deployments/synthetic",
            deployer="production-drill",
            observed_at=TransactionClock().now(session),
            probe_summary={
                "probes": [
                    {
                        "name": "live",
                        "method": "GET",
                        "endpoint": "/health/live",
                        "expected_status_min": 200,
                        "expected_status_max": 299,
                        "status_code": 200,
                        "observed_at": TransactionClock().now(session).isoformat(),
                    }
                ]
            },
            route_summary={
                "routes": [
                    {"path": "/health/live", "present": True},
                ]
            },
            auth_summary={"missing_m2m_status": 401, "configured_m2m_status": 200},
            dispatch_summary={"dispatch_enabled": False},
            status_summary={"status": "observed", "summary": "synthetic production drill"},
            idempotency_key=f"{command.idempotency_key}:deployment-observation",
            expected_version=0,
        ),
    )
    if isinstance(deployment, DomainError):
        raise deployment
    _wait_for_reporting_deadline(session, run, deployment)
    counters = detect_reconciliation_conditions(
        session,
        command.actor,
        stall_seconds=0,
        production_drill_run_id=run.id,
    )
    if counters.conditions_recorded != 1:
        raise DomainError(
            "production_drill_split_brain_not_detected",
            "fixed deploy split-brain scenario did not produce its required condition",
            None,
        )
    condition = session.scalar(
        select(ReconciliationCondition).where(
            ReconciliationCondition.work_unit_id == deployment.post_deploy_work_unit_id,
            ReconciliationCondition.condition_type == "deploy_split_brain",
        )
    )
    if condition is None:
        raise DomainError(
            "production_drill_split_brain_not_detected",
            "fixed deploy split-brain condition was not persisted",
            None,
        )
    from orchestrator.services.production_drill_resources import (
        bind_created_drill_reconciliation_condition,
        require_production_drill_resource,
    )

    bind_created_drill_reconciliation_condition(session, run.id, condition)
    require_production_drill_resource(session, run.id, "reconciliation_condition", condition.id)


def _complete_fixed_unit(
    session: Session,
    run: ProductionDrillRun,
    unit: WorkUnit,
    command: RunProductionDrillScenario,
) -> None:
    """Use ordinary role-scoped transitions; no scenario may assign COMPLETED directly."""
    from orchestrator.services.claims import claim_unit
    from orchestrator.services.evidence import append_production_drill_evidence, record_adjudication

    worker = ActorContext("production-drill-worker", ActorRole.WORKER)
    verifier = ActorContext("production-drill-verifier", ActorRole.VERIFIER)
    _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.READY)
    claim = claim_unit(
        session,
        unit.id,
        worker,
        f"{command.idempotency_key}:complete:claim",
        unit.version,
    )
    if isinstance(claim, DomainError):
        raise claim
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.EXECUTING,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    evidence = append_production_drill_evidence(
        session,
        run_id=run.id,
        work_package_revision_id=run.revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=claim.attempt,
        actor=worker,
        lease_token=claim.lease_token,
        evidence_type="production_drill.deploy_split_brain",
        stable_ref=f"drill://redacted/{run.id}/deploy-split-brain",
        payload={"scenario": "deploy_split_brain", "synthetic": True},
        source_revision="synthetic",
        expected_version=unit.version,
        idempotency_key=f"{command.idempotency_key}:complete:evidence",
    )
    if isinstance(evidence, DomainError):
        raise evidence
    _transition_fixed_unit(
        session,
        run.id,
        unit,
        command,
        WorkUnitState.SUBMITTED,
        worker,
        claim.attempt,
        claim.lease_token,
    )
    adjudication = record_adjudication(
        session,
        work_package_revision_id=run.revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=verifier,
        rationale="fixed synthetic production-drill verification",
        evidence_id=evidence.id,
        idempotency_key=f"{command.idempotency_key}:complete:adjudication",
        expected_version=unit.version,
    )
    if isinstance(adjudication, DomainError):
        raise adjudication
    _transition_fixed_unit(session, run.id, unit, command, WorkUnitState.COMPLETED, verifier)


def _wait_for_reporting_deadline(
    session: Session, run: ProductionDrillRun, deployment: DeploymentObservation
) -> None:
    _wait_for_run_deadline(session, run, deployment.recorded_at)


def _wait_for_lease_expiry(session: Session, expires_at: datetime) -> None:
    """Wait for the persisted, run-scoped claim deadline without backdating records."""
    remaining = (expires_at - TransactionClock().now(session)).total_seconds()
    if remaining > 0:
        time.sleep(remaining)


def _wait_for_run_deadline(
    session: Session, run: ProductionDrillRun, occurred_at: datetime
) -> None:
    deadlines = production_drill_deadlines(session, run.id)
    if isinstance(deadlines, DomainError):
        raise deadlines
    deadline = occurred_at + deadlines.reporting_deadline
    remaining = (deadline - TransactionClock().now(session)).total_seconds()
    if remaining > 0:
        time.sleep(remaining)


def _record_scenario_terminal_failure(
    session: Session,
    run: ProductionDrillRun,
    command: RunProductionDrillScenario,
    error: DomainError,
) -> None:
    now = TransactionClock().now(session)
    session.add(
        Event(
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="production_drill.failed",
            subject_type="production_drill_run",
            subject_id=run.id,
            from_state=run.status,
            to_state="failed",
            payload={
                "scenario": command.scenario,
                "failure_code": f"{command.scenario}_failed",
                "error": error.code,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{command.idempotency_key}:terminal-failure",
        )
    )
    run.status = "failed"
    session.flush()


def _transition_fixed_unit(
    session: Session,
    run_id: uuid.UUID,
    unit: WorkUnit,
    command: RunProductionDrillScenario,
    target: WorkUnitState,
    actor: ActorContext | None = None,
    attempt: int | None = None,
    lease_token: str | None = None,
) -> None:
    transition_production_drill_unit(
        session,
        run_id=run_id,
        command=TransitionCommand(
            unit_id=unit.id,
            target=target,
            actor=actor or command.actor,
            expected_version=unit.version,
            idempotency_key=(f"{command.idempotency_key}:transition:{unit.version}:{target.value}"),
            reason=f"fixed production drill scenario: {command.scenario}",
            attempt=attempt,
            lease_token=lease_token,
        ),
    )


def _fail_production_drill(session: Session, command: FailProductionDrill) -> None:
    _require_system(command.actor)
    if command.failure_code not in PRODUCTION_DRILL_FAILURE_CODES:
        raise DomainError(
            "production_drill_failure_code_invalid",
            "unsupported production drill failure code",
            None,
        )
    if not REDACTED_DIAGNOSTIC_REF.fullmatch(command.diagnostic_ref):
        raise DomainError(
            "production_drill_diagnostic_ref_invalid",
            "diagnostic reference must be a bounded redacted drill reference",
            None,
        )
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "production drill fail requires expected version 0",
            "reload",
            current_version=0,
        )
    _lock_idempotency_key(session, command.idempotency_key)
    payload = _fail_command_payload(command)
    existing = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if existing is not None:
        if (
            existing.action != "production_drill.failed"
            or existing.payload.get("command") != payload
        ):
            raise _idempotency_conflict()
        return
    run = session.get(ProductionDrillRun, command.run_id, with_for_update=True)
    if run is None:
        raise DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    if run.status == "failed":
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)
    if run.status == "closed":
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)
    now = TransactionClock().now(session)
    session.add(
        Event(
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="production_drill.failed",
            subject_type="production_drill_run",
            subject_id=run.id,
            from_state=run.status,
            to_state="failed",
            payload={
                "command": payload,
                "failure_code": command.failure_code,
                "diagnostic_ref": command.diagnostic_ref,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    run.status = "failed"
    # Failure intentionally leaves resources open for later forensic review; only HUMAN closeout
    # may resolve or close them.
    session.flush()


def _close_production_drill(session: Session, command: CloseProductionDrill) -> ProductionDrillRun:
    _require_human(command.actor)
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "production drill close requires expected version 0",
            "reload",
            current_version=0,
        )
    if not command.closure_reason:
        raise DomainError(
            "production_drill_closure_reason_required",
            "production drill close requires an explicit closure reason",
            None,
        )

    payload = _close_command_payload(command)
    _lock_idempotency_key(session, command.idempotency_key)
    existing_event = session.scalar(
        select(Event).where(Event.idempotency_key == command.idempotency_key)
    )
    if existing_event is not None:
        return _replayed_closed_run(session, existing_event, payload)

    run = session.get(ProductionDrillRun, command.run_id, with_for_update=True)
    if run is None:
        raise DomainError(
            "production_drill_run_not_found", "production drill run does not exist", None
        )
    if run.status == "closed" and run.closure_reason != command.closure_reason:
        raise DomainError(
            "production_drill_closure_reason_conflict",
            "production drill run already has a different closure reason",
            None,
        )
    if run.status not in {"open", "asserting"}:
        raise DomainError("production_drill_run_not_open", "production drill run is not open", None)

    now = TransactionClock().now(session)
    _close_run_owned_work(session, run.id, command, now)
    _resolve_run_owned_conditions(session, run.id, command)
    resources = session.scalars(
        select(ProductionDrillResource)
        .where(ProductionDrillResource.run_id == run.id)
        .with_for_update()
    ).all()
    session.add(
        Event(
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="production_drill_closed",
            subject_type="production_drill_run",
            subject_id=run.id,
            from_state=run.status,
            to_state="closed",
            payload={"closure_reason": command.closure_reason, "command": payload},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    for resource in resources:
        resource.closed_at = now
    run.closed_at = now
    run.status = "closed"
    run.closure_reason = command.closure_reason
    session.flush()
    _assert_closeout_invariant(session, run.id, now)
    return run


def _assert_closeout_invariant(session: Session, run_id: uuid.UUID, now: datetime) -> None:
    unit_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "work_unit",
    )
    active_claim = session.scalar(
        select(Claim.id).where(
            Claim.work_unit_id.in_(unit_ids),
            Claim.released_at.is_(None),
            Claim.lease_expires_at > now,
        )
    )
    if active_claim is not None:
        raise DomainError(
            "production_drill_assertions_incomplete",
            "production drill has an active synthetic claim",
            None,
        )
    nonterminal_unit = session.scalar(
        select(WorkUnit.id).where(
            WorkUnit.id.in_(unit_ids),
            WorkUnit.state.not_in(
                (
                    WorkUnitState.COMPLETED,
                    WorkUnitState.FAILED,
                    WorkUnitState.CANCELLED,
                )
            ),
        )
    )
    if nonterminal_unit is not None:
        raise DomainError(
            "production_drill_assertions_incomplete",
            "production drill has a nonterminal synthetic work unit",
            None,
        )
    condition_ids = select(ProductionDrillResource.resource_id).where(
        ProductionDrillResource.run_id == run_id,
        ProductionDrillResource.resource_type == "reconciliation_condition",
    )
    unresolved_condition = session.scalar(
        select(ReconciliationCondition.id).where(
            ReconciliationCondition.id.in_(condition_ids),
            ~exists().where(ReconciliationResolution.condition_id == ReconciliationCondition.id),
        )
    )
    if unresolved_condition is not None:
        raise DomainError(
            "production_drill_assertions_incomplete",
            "production drill has an unresolved synthetic reconciliation condition",
            None,
        )


def _close_run_owned_work(
    session: Session, run_id: uuid.UUID, command: CloseProductionDrill, now: datetime
) -> None:
    # `claims` imports the drill lease policy, so defer this dependency until both modules load.
    from orchestrator.services.claims import release_claim

    unit_ids = session.scalars(
        select(ProductionDrillResource.resource_id)
        .where(
            ProductionDrillResource.run_id == run_id,
            ProductionDrillResource.resource_type == "work_unit",
        )
        .order_by(ProductionDrillResource.resource_id)
    ).all()
    for unit_id in unit_ids:
        claims = session.scalars(
            select(Claim)
            .where(
                Claim.work_unit_id == unit_id,
                Claim.released_at.is_(None),
                Claim.lease_expires_at > now,
            )
            .with_for_update()
        ).all()
        for claim in claims:
            release_claim(claim, terminal_reason="production_drill_closed", released_at=now)
        close_production_drill_unit(
            session,
            run_id=run_id,
            unit_id=unit_id,
            actor=command.actor,
            idempotency_key=f"{command.idempotency_key}:unit:{unit_id}",
            reason="production_drill_closed",
        )


def _resolve_run_owned_conditions(
    session: Session, run_id: uuid.UUID, command: CloseProductionDrill
) -> None:
    condition_ids = session.scalars(
        select(ProductionDrillResource.resource_id)
        .where(
            ProductionDrillResource.run_id == run_id,
            ProductionDrillResource.resource_type == "reconciliation_condition",
        )
        .order_by(ProductionDrillResource.resource_id)
    ).all()
    for condition_id in condition_ids:
        if (
            session.scalar(
                select(ReconciliationResolution.id).where(
                    ReconciliationResolution.condition_id == condition_id
                )
            )
            is None
        ):
            resolve_production_drill_condition(
                session,
                run_id=run_id,
                command=ResolutionCommand(
                    actor=command.actor,
                    condition_id=condition_id,
                    decision="dismissed",
                    rationale=f"production_drill_closed: {command.closure_reason}",
                    idempotency_key=f"{command.idempotency_key}:condition:{condition_id}",
                ),
            )


def _require_human(actor: ActorContext) -> None:
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required", "only a human actor may start a production drill", None
        )


def _require_system(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden", "only the system actor may run production drill scenarios", None
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


def _replayed_closed_run(
    session: Session, event: Event, payload: dict[str, object]
) -> ProductionDrillRun:
    if event.action != "production_drill_closed" or event.subject_type != "production_drill_run":
        raise _idempotency_conflict()
    if event.payload.get("command") != payload:
        raise _idempotency_conflict()
    run = session.get(ProductionDrillRun, event.subject_id)
    if run is None or run.status != "closed":
        raise DomainError("event_invalid", "production drill close event has no closed run", None)
    return run


def _command_payload(command: StartProductionDrill) -> dict[str, object]:
    return {
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role.value,
        "revision_id": str(command.revision_id),
        "expected_version": command.expected_version,
        "runtime_observation_id": str(command.runtime_observation_id),
        "lease_duration_seconds": command.lease_duration_seconds,
        "reporting_deadline_seconds": command.reporting_deadline_seconds,
    }


def _close_command_payload(command: CloseProductionDrill) -> dict[str, object]:
    return {
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role.value,
        "run_id": str(command.run_id),
        "expected_version": command.expected_version,
        "closure_reason": command.closure_reason,
    }


def _scenario_command_payload(command: RunProductionDrillScenario) -> dict[str, object]:
    return {
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role.value,
        "run_id": str(command.run_id),
        "scenario": command.scenario,
        "expected_version": command.expected_version,
    }


def _fail_command_payload(command: FailProductionDrill) -> dict[str, object]:
    return {
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role.value,
        "run_id": str(command.run_id),
        "expected_version": command.expected_version,
        "failure_code": command.failure_code,
        "diagnostic_ref": command.diagnostic_ref,
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


def _require_fresh_runtime_observation(observed_at: datetime, now: datetime) -> None:
    age = now - observed_at
    if age < timedelta(0) or age > MAX_RUNTIME_OBSERVATION_AGE:
        raise DomainError(
            "runtime_observation_stale",
            "production drill start requires a runtime observation from the last five minutes",
            "record a fresh runtime observation before starting the production drill",
        )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
