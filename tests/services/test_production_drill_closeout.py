import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Claim,
    Event,
    ProductionDrillResource,
    ReconciliationResolution,
    WorkUnit,
)
from orchestrator.services.claims import claim_unit
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_production_drill_unit,
)
from orchestrator.services.production_drills import (
    CloseProductionDrill,
    close_production_drill,
    start_production_drill,
)
from orchestrator.services.reconciliation import (
    record_production_drill_reconciliation_condition,
    record_reconciliation_condition,
)
from tests.services.test_dependencies import register_unit
from tests.services.test_production_drills import HUMAN, command, runtime_observation
from tests.services.test_reconciliation import flip


def drill_command(session: Session, revision_id: uuid.UUID, *, key: str = "drill-1"):
    return command(
        revision_id,
        key=key,
        runtime_observation_id=runtime_observation(session, key=key),
    )


def close_command(
    run_id: uuid.UUID,
    *,
    reason: str = "all assertions reviewed",
    key: str = "close-production-drill",
) -> CloseProductionDrill:
    return CloseProductionDrill(
        run_id=run_id,
        actor=HUMAN,
        idempotency_key=key,
        expected_version=0,
        closure_reason=reason,
    )


def test_close_cancels_incomplete_run_owned_assertion(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "incomplete-drill-assertion")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=run.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()

    result = close_production_drill(migrated_session, close_command(run.id))

    assert not isinstance(result, DomainError)
    closed = migrated_session.get(WorkUnit, unit.id)
    assert closed is not None
    assert closed.state == WorkUnitState.CANCELLED
    transition = migrated_session.scalar(
        select(Event).where(
            Event.action == "work_unit.transitioned",
            Event.subject_id == unit.id,
        )
    )
    assert transition is not None
    assert transition.payload["reason"] == "production_drill_closed"


def test_close_ignores_ordinary_unit_and_emits_explicit_audit_event(
    migrated_session: Session,
) -> None:
    ordinary_unit = register_unit(migrated_session, "ordinary-closeout-unit")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, ordinary_unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)

    result = close_production_drill(migrated_session, close_command(run.id))

    assert not isinstance(result, DomainError)
    assert result.status == "closed"
    assert result.closure_reason == "all assertions reviewed"
    ordinary = migrated_session.get(WorkUnit, ordinary_unit.id)
    assert ordinary is not None
    assert ordinary.state == WorkUnitState.DRAFT
    event = migrated_session.scalar(
        select(Event).where(
            Event.action == "production_drill_closed",
            Event.subject_type == "production_drill_run",
            Event.subject_id == run.id,
        )
    )
    assert event is not None
    assert event.actor_id == HUMAN.actor_id
    assert event.payload["closure_reason"] == "all assertions reviewed"
    assert event.payload["command"]["run_id"] == str(run.id)


def test_close_replay_rejects_a_second_distinct_reason(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "close-reason-idempotency")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)

    first = close_production_drill(migrated_session, close_command(run.id, reason="reviewed"))
    second = close_production_drill(
        migrated_session,
        close_command(run.id, reason="a different reason", key="second-close-reason"),
    )

    assert not isinstance(first, DomainError)
    assert isinstance(second, DomainError)
    assert second.code == "production_drill_closure_reason_conflict"


def test_close_requires_a_human_actor(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "human-closeout")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)

    result = close_production_drill(
        migrated_session,
        CloseProductionDrill(
            run_id=run.id,
            actor=ActorContext("system", ActorRole.SYSTEM),
            idempotency_key="system-close",
            expected_version=0,
            closure_reason="reviewed",
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "human_actor_required"


def test_close_releases_active_claim_and_cancels_owned_unit(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "active-claim-closeout")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=run.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()
    transition_production_drill_unit(
        migrated_session,
        run_id=run.id,
        command=TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.READY,
            actor=ActorContext("system", ActorRole.SYSTEM),
            expected_version=unit.version,
            idempotency_key="active-claim-closeout-ready",
        ),
    )

    claim = claim_unit(
        migrated_session,
        unit.id,
        ActorContext("worker", ActorRole.WORKER),
        "active-claim-closeout",
        expected_version=unit.version,
    )
    assert not isinstance(claim, DomainError)
    result = close_production_drill(migrated_session, close_command(run.id))

    assert not isinstance(result, DomainError)
    released = migrated_session.scalar(select(Claim).where(Claim.work_unit_id == unit.id))
    assert released is not None
    assert released.released_at is not None
    assert released.terminal_reason == "production_drill_closed"
    closed = migrated_session.get(WorkUnit, unit.id)
    assert closed is not None
    assert closed.state == WorkUnitState.CANCELLED


def test_close_resolves_only_run_owned_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "unresolved-condition-closeout")
    run = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(run, DomainError)
    unit.state = WorkUnitState.COMPLETED
    migrated_session.add(
        ProductionDrillResource(run_id=run.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()
    condition = record_production_drill_reconciliation_condition(
        migrated_session,
        run_id=run.id,
        command=flip(unit.id),
    )
    assert not isinstance(condition, DomainError)

    ordinary_unit = register_unit(migrated_session, "ordinary-condition-closeout")
    ordinary_unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    ordinary_condition = record_reconciliation_condition(migrated_session, flip(ordinary_unit.id))
    assert not isinstance(ordinary_condition, DomainError)

    result = close_production_drill(migrated_session, close_command(run.id))

    assert not isinstance(result, DomainError)
    resolution = migrated_session.scalar(
        select(ReconciliationResolution).where(
            ReconciliationResolution.condition_id == condition.condition.id
        )
    )
    assert resolution is not None
    assert resolution.decision == "dismissed"
    assert resolution.rationale == "production_drill_closed: all assertions reviewed"
    assert (
        migrated_session.scalar(
            select(ReconciliationResolution).where(
                ReconciliationResolution.condition_id == ordinary_condition.condition.id
            )
        )
        is None
    )
