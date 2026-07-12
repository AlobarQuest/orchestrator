import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, ProductionDrillResource, WorkUnit
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
from orchestrator.services.reconciliation import record_production_drill_reconciliation_condition
from tests.services.test_dependencies import register_unit
from tests.services.test_production_drills import HUMAN, command
from tests.services.test_reconciliation import flip


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


def test_close_rejects_incomplete_run_owned_assertion(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "incomplete-drill-assertion")
    run = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(run, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=run.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()

    result = close_production_drill(migrated_session, close_command(run.id))

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_assertions_incomplete"


def test_close_ignores_ordinary_unit_and_emits_explicit_audit_event(
    migrated_session: Session,
) -> None:
    ordinary_unit = register_unit(migrated_session, "ordinary-closeout-unit")
    run = start_production_drill(migrated_session, command(ordinary_unit.work_package_revision_id))
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
    run = start_production_drill(migrated_session, command(unit.work_package_revision_id))
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
    run = start_production_drill(migrated_session, command(unit.work_package_revision_id))
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


def test_close_rejects_an_active_claim_even_when_the_unit_is_terminal(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "active-claim-closeout")
    run = start_production_drill(migrated_session, command(unit.work_package_revision_id))
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
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    result = close_production_drill(migrated_session, close_command(run.id))

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_assertions_incomplete"


def test_close_rejects_an_unresolved_run_owned_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "unresolved-condition-closeout")
    run = start_production_drill(migrated_session, command(unit.work_package_revision_id))
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

    result = close_production_drill(migrated_session, close_command(run.id))

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_assertions_incomplete"
