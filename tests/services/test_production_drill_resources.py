from inspect import signature

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.packages import register_approved_unit
from orchestrator.services.production_drill_resources import bind_production_drill_resource
from orchestrator.services.production_drills import start_production_drill
from tests.services.test_dependencies import register_unit
from tests.services.test_production_drills import command


def test_ordinary_work_unit_cannot_self_tag_as_production_drill_resource() -> None:
    assert "run_id" not in WorkUnit.__table__.columns
    assert "run_id" not in signature(register_approved_unit).parameters


def test_resource_cannot_belong_to_two_production_drill_runs(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "resource-owner")
    package_revision_id = unit.work_package_revision_id
    first_run = command(package_revision_id, key="first-resource-run")
    second_run = command(package_revision_id, key="second-resource-run")

    first = start_production_drill(migrated_session, first_run)
    second = start_production_drill(migrated_session, second_run)
    assert not isinstance(first, DomainError)
    assert not isinstance(second, DomainError)

    bind_production_drill_resource(migrated_session, first.id, "work_unit", unit.id)
    migrated_session.commit()

    with pytest.raises(DomainError, match="resource already belongs to a production drill run"):
        bind_production_drill_resource(migrated_session, second.id, "work_unit", unit.id)


def test_ordinary_projections_hide_drill_resources_by_default(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "hidden-drill-resource")
    drill = start_production_drill(
        migrated_session, command(unit.work_package_revision_id)
    )
    assert not isinstance(drill, DomainError)
    unit.state = WorkUnitState.FAILED
    bind_production_drill_resource(migrated_session, drill.id, "work_unit", unit.id)
    migrated_session.commit()

    default_dead_letter = dead_letter(
        migrated_session,
        failure_signature_threshold=3,
        stalled_approval_seconds=60,
    )
    internal_dead_letter = dead_letter(
        migrated_session,
        failure_signature_threshold=3,
        stalled_approval_seconds=60,
        include_production_drill_resources=True,
    )

    assert unit.id not in {entry.work_unit_id for entry in default_dead_letter}
    assert unit.id in {entry.work_unit_id for entry in internal_dead_letter}

    unit.state = WorkUnitState.EXECUTING
    migrated_session.commit()
    assert unit.id not in {view.work_unit_id for view in in_flight_snapshot(migrated_session).units}
    assert unit.id in {
        view.work_unit_id
        for view in in_flight_snapshot(
            migrated_session, include_production_drill_resources=True
        ).units
    }
