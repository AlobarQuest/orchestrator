from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import orchestrator.services.production_drills as production_drills
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    Event,
    ProductionDrillResource,
    ReconciliationCondition,
    WorkUnit,
)
from orchestrator.services.packages import register_production_drill_unit
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.production_drills import (
    CloseProductionDrill,
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


def test_successful_scenario_remains_human_closeable(migrated_session: Session) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id, key="closeable-run"))
    assert not isinstance(run, DomainError)
    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(run.id, "crash_recovery", SYSTEM, "closeable-scenario", 0),
    )
    assert not isinstance(result, DomainError)
    assert result["status"] == "asserting"
    closed = production_drills.close_production_drill(
        migrated_session,
        CloseProductionDrill(
            run.id, type(SYSTEM)("human-1", ActorRole.HUMAN), "closeable-close", 0, "reviewed"
        ),
    )
    assert not isinstance(closed, DomainError)
    assert closed.status == "closed"


def test_external_pr_conflict_uses_real_binding_and_detection(migrated_session: Session) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id, key="real-pr-conflict-run"))
    assert not isinstance(run, DomainError)
    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(run.id, "external_pr_conflict", SYSTEM, "real-pr-conflict", 0),
    )
    assert not isinstance(result, DomainError)
    unit = migrated_session.scalar(
        select(WorkUnit).where(WorkUnit.unit_key.contains("external_pr_conflict"))
    )
    assert unit is not None
    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.verification_read_head_sha == binding.head_sha
    condition = migrated_session.scalar(
        select(ReconciliationCondition).where(
            ReconciliationCondition.work_unit_id == unit.id,
            ReconciliationCondition.condition_type == "external_merge_alarm",
        )
    )
    assert condition is not None
    assert condition.observation_id is not None
    assert condition.observed_state["merged"] is True


def test_late_scenario_failure_rolls_back_all_synthetic_resources(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id, key="atomic-failure-run"))
    assert not isinstance(run, DomainError)

    def fail_after_unit(*_args, **_kwargs) -> None:
        raise DomainError("late_fixed_failure", "forced late scenario failure", None)

    monkeypatch.setattr(production_drills, "_execute_fixed_evidence_recovery", fail_after_unit)
    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(run.id, "evidence_recovery", SYSTEM, "atomic-failure", 0),
    )
    assert not isinstance(result, DomainError)
    assert result["status"] == "failed"
    assert result["units"] == []
    assert result["evidence"] == []
    assert result["observations"] == []
    assert result["conditions"] == []


def test_deploy_split_brain_waits_then_detects_only_run_owned_resources(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.services.reconciliation_detection as detection

    revision_id = register_test_revision(migrated_session).id
    run = start_production_drill(migrated_session, command(revision_id, key="deploy-e2e-run"))
    assert not isinstance(run, DomainError)
    waits: list[float] = []

    class PastDeadlineClock:
        def now(self, _session: Session):
            return run.opened_at + production_drills.timedelta(seconds=120)

    monkeypatch.setattr(production_drills, "TransactionClock", PastDeadlineClock)
    monkeypatch.setattr(detection, "TransactionClock", PastDeadlineClock)
    monkeypatch.setattr(production_drills.time, "sleep", waits.append)
    result = run_production_drill_scenario(
        migrated_session,
        RunProductionDrillScenario(run.id, "deploy_split_brain", SYSTEM, "deploy-e2e", 0),
    )

    assert not isinstance(result, DomainError)
    assert waits == []
    assert len(result["deployment_observations"]) == 1
    assert any(
        row["condition_type"] == "deploy_split_brain" and row["is_open"]
        for row in result["conditions"]
    )
    owned = migrated_session.scalars(
        select(ProductionDrillResource).where(ProductionDrillResource.run_id == run.id)
    ).all()
    assert {row.resource_type for row in owned} >= {
        "deployment_observation",
        "reconciliation_condition",
        "work_unit",
    }


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
