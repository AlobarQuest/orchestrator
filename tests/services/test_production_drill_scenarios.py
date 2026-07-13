from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event
from orchestrator.services.production_drills import (
    FailProductionDrill,
    RunProductionDrillScenario,
    fail_production_drill,
    run_production_drill_scenario,
    start_production_drill,
)
from tests.services.test_package_registration import register_test_revision
from tests.services.test_production_drills import SYSTEM, command


def test_system_scenario_is_audited_and_returns_only_its_run_state(
    migrated_session: Session,
) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id))
    assert not isinstance(run, DomainError)

    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(
            run_id=run.id,
            scenario="crash_recovery",
            actor=SYSTEM,
            idempotency_key="scenario-crash-recovery",
            expected_version=0,
        ),
    )

    assert not isinstance(result, DomainError)
    assert result["run_id"] == run.id
    assert result["status"] == "asserting"
    event = migrated_session.scalar(
        select(Event).where(Event.idempotency_key == "scenario-crash-recovery")
    )
    assert event is not None
    assert event.action == "production_drill.scenario.crash_recovery"


def test_system_fail_records_an_event_and_does_not_close_resources(
    migrated_session: Session,
) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id))
    assert not isinstance(run, DomainError)

    result = fail_production_drill(
        migrated_session,
        FailProductionDrill(
            run_id=run.id,
            actor=SYSTEM,
            idempotency_key="scenario-fail",
            expected_version=0,
            failure_code="runner_preflight_failed",
            diagnostic_ref="drill://redacted/preflight",
        ),
    )

    assert not isinstance(result, DomainError)
    assert result["status"] == "failed"
    assert result["closed_at"] is None
    event = migrated_session.scalar(select(Event).where(Event.idempotency_key == "scenario-fail"))
    assert event is not None
    assert event.action == "production_drill.failed"
    assert event.to_state == "failed"
