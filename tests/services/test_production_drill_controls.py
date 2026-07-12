import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.leases import LEASE_DURATION
from orchestrator.services.packages import register_production_drill_unit
from orchestrator.services.production_drills import (
    StartProductionDrill,
    lease_duration_for_work_unit,
    production_drill_state,
    start_production_drill,
)
from tests.services.test_package_registration import AUTHORITY, register_test_revision
from tests.services.test_production_drills import HUMAN, command


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


def test_production_drill_rejects_deadline_above_configured_maximum(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    result = start_production_drill(
        migrated_session,
        StartProductionDrill(
            **{
                **command(revision.id).__dict__,
                "lease_duration_seconds": 61,
                "max_deadline_seconds": 60,
            }
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_deadline_too_long"


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
