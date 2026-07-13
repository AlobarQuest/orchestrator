from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.production_drills as production_drills
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, ProductionDrillResource
from orchestrator.services.packages import register_production_drill_unit
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


def test_human_cannot_execute_a_system_scenario(migrated_session: Session) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id))
    assert not isinstance(run, DomainError)

    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(
            run_id=run.id,
            scenario="crash_recovery",
            actor=type(SYSTEM)("human-1", ActorRole.HUMAN),
            idempotency_key="human-scenario",
            expected_version=0,
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_scenario_creates_only_fixed_run_owned_resources_and_replays_exactly(
    migrated_session: Session,
) -> None:
    revision_id = register_test_revision(migrated_session).id
    first = start_production_drill(migrated_session, command(revision_id, key="scenario-first"))
    second = start_production_drill(migrated_session, command(revision_id, key="scenario-second"))
    assert not isinstance(first, DomainError)
    assert not isinstance(second, DomainError)
    scenario = RunProductionDrillScenario(
        run_id=first.id,
        scenario="evidence_recovery",
        actor=SYSTEM,
        idempotency_key="evidence-recovery",
        expected_version=0,
    )

    result = run_production_drill_scenario(migrated_session, scenario)
    replay = run_production_drill_scenario(migrated_session, scenario)
    conflict = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(**{**scenario.__dict__, "run_id": second.id}),
    )

    assert not isinstance(result, DomainError)
    assert not isinstance(replay, DomainError)
    assert isinstance(conflict, DomainError)
    assert conflict.code == "idempotency_conflict"
    assert migrated_session.scalars(
        select(ProductionDrillResource).where(ProductionDrillResource.run_id == first.id)
    ).all()
    evidence = result["evidence"]
    assert isinstance(evidence, list)
    assert len(evidence) == 2
    assert sum(row["is_head"] for row in evidence) == 1
    assert (
        migrated_session.scalars(
            select(ProductionDrillResource).where(ProductionDrillResource.run_id == second.id)
        ).all()
        == []
    )


def test_scenarios_reject_runs_failed_by_the_system(migrated_session: Session) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id))
    assert not isinstance(run, DomainError)
    failed = fail_production_drill(
        migrated_session,
        FailProductionDrill(
            run_id=run.id,
            actor=SYSTEM,
            idempotency_key="failed-run",
            expected_version=0,
            failure_code="runner_preflight_failed",
            diagnostic_ref="drill://redacted/preflight",
        ),
    )
    assert not isinstance(failed, DomainError)

    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(
            run_id=run.id,
            scenario="crash_recovery",
            actor=SYSTEM,
            idempotency_key="after-failure",
            expected_version=0,
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_run_not_open"


def test_fail_rejects_an_unredacted_diagnostic_in_the_service(
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
            idempotency_key="unredacted-diagnostic",
            expected_version=0,
            failure_code="runner_preflight_failed",
            diagnostic_ref="https://unsafe.example/secret",
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_diagnostic_ref_invalid"


def test_unavailable_fixed_scenario_terminal_fails_before_resource_or_scenario_mutation(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id))
    assert not isinstance(run, DomainError)
    scenario = RunProductionDrillScenario(
        run_id=run.id,
        scenario="deploy_split_brain",
        actor=SYSTEM,
        idempotency_key="unavailable-split-brain",
        expected_version=0,
    )

    def unavailable(*_args, **_kwargs) -> None:
        raise DomainError("fixed_template_unavailable", "synthetic prerequisite unavailable", None)

    monkeypatch.setattr(production_drills, "_preflight_fixed_scenario", unavailable)
    result = run_production_drill_scenario(migrated_session, scenario)

    assert not isinstance(result, DomainError)
    assert result["status"] == "failed"
    assert result["units"] == []
    assert result["evidence"] == []
    assert result["observations"] == []
    assert result["conditions"] == []
    events = migrated_session.scalars(select(Event).where(Event.subject_id == run.id)).all()
    assert {event.action for event in events} == {
        "production_drill.started",
        "production_drill.failed",
    }


def test_direct_system_registration_cannot_select_an_arbitrary_drill_template(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    run = start_production_drill(migrated_session, command(revision.id))
    assert not isinstance(run, DomainError)

    with pytest.raises(DomainError) as error:
        register_production_drill_unit(
            migrated_session,
            run_id=run.id,
            revision_id=revision.id,
            unit_key="attacker-selected-unit",
            title="attacker selected",
            outcome="attacker selected",
            required_capability="repository_write",
            authority=revision.enforcement_snapshot["authority"],
            approved_by=run.owner_actor_id,
            approved_at=revision.approved_at,
            actor_id=SYSTEM.actor_id,
            actor_role=SYSTEM.role,
            idempotency_key="attacker-selected-unit",
        )

    assert error.value.code == "human_actor_required"
    assert (
        migrated_session.scalars(
            select(ProductionDrillResource).where(ProductionDrillResource.run_id == run.id)
        ).all()
        == []
    )


def test_split_brain_wait_uses_only_the_persisted_run_deadline(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id, key="bounded-wait"))
    assert not isinstance(run, DomainError)
    waits: list[float] = []

    class FixedClock:
        def now(self, _session: Session):
            return run.opened_at

    monkeypatch.setattr(production_drills, "TransactionClock", FixedClock)
    monkeypatch.setattr(production_drills.time, "sleep", waits.append)
    production_drills._wait_for_reporting_deadline(
        migrated_session,
        run,
        SimpleNamespace(recorded_at=run.opened_at),
    )

    assert waits == [60.0]
