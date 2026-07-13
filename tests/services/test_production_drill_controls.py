import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.production_drills as production_drills
from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import LEASE_DURATION
from orchestrator.persistence.models import ProductionDrillResource, ReconciliationCondition
from orchestrator.services.observations import (
    ObservationCommand,
    record_production_drill_observation,
)
from orchestrator.services.packages import register_production_drill_unit
from orchestrator.services.production_drills import (
    StartProductionDrill,
    lease_duration_for_work_unit,
    production_drill_state,
    start_production_drill,
)
from orchestrator.services.reconciliation_detection import detect_reconciliation_conditions
from orchestrator.services.release_artifacts import record_production_drill_release_artifact
from tests.services.test_package_registration import AUTHORITY, register_test_revision
from tests.services.test_production_drills import HUMAN, SYSTEM, command
from tests.services.test_release_artifacts import command as release_artifact_command
from tests.services.test_release_artifacts import completed_unit


@pytest.mark.parametrize("seconds", [0, -1, 59])
def test_production_drill_rejects_deadlines_below_the_minimum(
    migrated_session: Session, seconds: int
) -> None:
    revision = register_test_revision(migrated_session)
    result = start_production_drill(
        migrated_session,
        StartProductionDrill(
            **{
                **command(revision.id).__dict__,
                "lease_duration_seconds": seconds,
                "reporting_deadline_seconds": 60,
            }
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_deadline_too_short"


def test_production_drill_uses_the_configured_deadline_maximum_not_command_input(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = register_test_revision(migrated_session)
    monkeypatch.setattr(
        production_drills,
        "get_settings",
        lambda: Settings(
            database_url="postgresql://test", production_drill_max_deadline_seconds=60
        ),
    )
    result = start_production_drill(
        migrated_session,
        StartProductionDrill(
            **{
                **command(revision.id).__dict__,
                "lease_duration_seconds": 61,
            }
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_deadline_too_long"


def test_production_drill_command_rejects_a_forged_deadline_maximum(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)

    with pytest.raises(TypeError, match="max_deadline_seconds"):
        StartProductionDrill(**{**command(revision.id).__dict__, "max_deadline_seconds": 86_400})


def test_registered_drill_unit_uses_its_run_lease_without_changing_ordinary_duration(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    drill = start_production_drill(
        migrated_session,
        StartProductionDrill(**{**command(revision.id).__dict__, "lease_duration_seconds": 61}),
    )
    assert not isinstance(drill, DomainError)
    unit = register_production_drill_unit(
        migrated_session,
        run_id=drill.id,
        revision_id=revision.id,
        unit_key="drill-control-unit",
        title="Drill control unit",
        outcome="bounded lease",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by=HUMAN.actor_id,
        approved_at=revision.approved_at,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )

    assert lease_duration_for_work_unit(migrated_session, unit.id) == timedelta(seconds=61)
    assert lease_duration_for_work_unit(migrated_session, uuid.uuid4()) == LEASE_DURATION


def test_state_is_scoped_to_the_requested_run(migrated_session: Session) -> None:
    revision = register_test_revision(migrated_session)
    first = start_production_drill(migrated_session, command(revision.id, key="state-first"))
    second = start_production_drill(migrated_session, command(revision.id, key="state-second"))
    assert not isinstance(first, DomainError)
    assert not isinstance(second, DomainError)

    state = production_drill_state(migrated_session, first.id)

    assert not isinstance(state, DomainError)
    assert state["run_id"] == first.id
    assert state["run_id"] != second.id


def test_run_scoped_reconciliation_ignores_another_runs_deployment_report(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="cross-run-deployment")
    first = start_production_drill(
        migrated_session, command(unit.work_package_revision_id, key="run-a")
    )
    second = start_production_drill(
        migrated_session, command(unit.work_package_revision_id, key="run-b")
    )
    assert not isinstance(first, DomainError)
    assert not isinstance(second, DomainError)
    migrated_session.add_all(
        (ProductionDrillResource(run_id=second.id, resource_type="work_unit", resource_id=unit.id),)
    )
    migrated_session.commit()

    artifact = release_artifact_command(unit, key="cross-run-deployment-artifact")
    binding = record_production_drill_release_artifact(
        migrated_session, run_id=second.id, command=artifact
    )
    assert not isinstance(binding, DomainError)
    report = record_production_drill_observation(
        migrated_session,
        run_id=second.id,
        command=ObservationCommand(
            actor=SYSTEM,
            source_system="github",
            source_reference="deployment:cross-run-deployment-report",
            source_url=None,
            trust_classification="delivery_system",
            subject_type="release_binding",
            subject_reference=str(binding.id),
            environment="production",
            observation_type="deployment",
            status="observed",
            severity="info",
            observed_at=unit.updated_at,
            summary="deployment observed",
            facts={"deploy_status": "succeeded", "artifact_digest": binding.artifact_digest},
            payload_digest=None,
            idempotency_key="cross-run-deployment-report",
        ),
    )
    assert not isinstance(report, DomainError)

    counters = detect_reconciliation_conditions(
        migrated_session, SYSTEM, stall_seconds=0, production_drill_run_id=first.id
    )

    assert counters.conditions_recorded == 0
    assert list(migrated_session.scalars(select(ReconciliationCondition))) == []
