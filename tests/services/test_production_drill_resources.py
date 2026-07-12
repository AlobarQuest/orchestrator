from inspect import signature

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import ProductionDrillResource, WorkUnit
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.deployment_observations import (
    record_deployment_observation,
    record_production_drill_deployment_observation,
)
from orchestrator.services.evidence import append_evidence, append_production_drill_evidence
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.lifecycle import (
    ActorContext,
    TransitionCommand,
    transition_production_drill_unit,
)
from orchestrator.services.observations import (
    record_observation,
    record_production_drill_observation,
)
from orchestrator.services.packages import register_approved_unit
from orchestrator.services.production_drills import start_production_drill
from orchestrator.services.reconciliation import (
    ConditionCommand,
    record_production_drill_reconciliation_condition,
    record_reconciliation_condition,
)
from orchestrator.services.release_artifacts import record_production_drill_release_artifact
from tests.services.test_dependencies import register_unit
from tests.services.test_deployment_observations import (
    observation_command as deployment_observation_command,
)
from tests.services.test_deployment_observations import (
    release_binding,
)
from tests.services.test_evidence import active_claim, evidence_kwargs
from tests.services.test_observations import SYSTEM
from tests.services.test_observations import command as observation_command
from tests.services.test_production_drills import command
from tests.services.test_release_artifacts import command as release_artifact_command


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

    migrated_session.add(
        ProductionDrillResource(run_id=first.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()

    migrated_session.add(
        ProductionDrillResource(run_id=second.id, resource_type="work_unit", resource_id=unit.id)
    )
    with pytest.raises(IntegrityError):
        migrated_session.commit()
    migrated_session.rollback()


def test_resource_registry_exposes_no_generic_direct_binding_api() -> None:
    import orchestrator.services.production_drill_resources as resources

    assert not hasattr(resources, "bind_production_drill_resource")
    assert not hasattr(resources, "bind_created_production_drill_resource")


def test_ordinary_observation_idempotency_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "ordinary-observation")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    ordinary = record_observation(migrated_session, observation_command(key="ordinary-observation"))
    assert not isinstance(ordinary, DomainError)

    replay = record_production_drill_observation(
        migrated_session,
        run_id=drill.id,
        command=observation_command(key="ordinary-observation"),
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_evidence_idempotency_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session, ready_unit
) -> None:
    drill = start_production_drill(migrated_session, command(ready_unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(
            run_id=drill.id,
            resource_type="work_unit",
            resource_id=ready_unit.id,
        )
    )
    migrated_session.commit()
    grant = active_claim(migrated_session, ready_unit)
    evidence_command = evidence_kwargs(ready_unit, grant)
    ordinary = append_evidence(migrated_session, **evidence_command)
    assert not isinstance(ordinary, DomainError)

    replay = append_production_drill_evidence(migrated_session, run_id=drill.id, **evidence_command)

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_condition_replay_cannot_be_captured_by_a_drill(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "ordinary-condition-replay")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(
            run_id=drill.id,
            resource_type="work_unit",
            resource_id=unit.id,
        )
    )
    migrated_session.commit()
    condition = ConditionCommand(
        actor=SYSTEM,
        work_unit_id=unit.id,
        observation_kind="github_check",
        condition_type="check_result_flip",
        key_facts={"check_name": "Quality"},
        stored_state={"conclusion": "success"},
        observed_state={"conclusion": "failure"},
        detail="Synthetic check changed after verification",
    )
    ordinary = record_reconciliation_condition(migrated_session, condition)
    assert not isinstance(ordinary, DomainError)

    replay = record_production_drill_reconciliation_condition(
        migrated_session, run_id=drill.id, command=condition
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_release_artifact_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit, _ordinary_binding = release_binding(migrated_session, key="ordinary-release-replay")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=drill.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()

    replay = record_production_drill_release_artifact(
        migrated_session,
        run_id=drill.id,
        command=release_artifact_command(unit, key="ordinary-release-replay-binding"),
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_deployment_observation_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit, binding = release_binding(migrated_session, key="ordinary-deployment-replay")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    migrated_session.add_all(
        (
            ProductionDrillResource(
                run_id=drill.id, resource_type="work_unit", resource_id=unit.id
            ),
            ProductionDrillResource(
                run_id=drill.id, resource_type="release_artifact", resource_id=binding.id
            ),
        )
    )
    migrated_session.commit()
    ordinary = record_deployment_observation(
        migrated_session,
        deployment_observation_command(binding, key="ordinary-deployment-replay-observation"),
    )
    assert not isinstance(ordinary, DomainError)

    replay = record_production_drill_deployment_observation(
        migrated_session,
        run_id=drill.id,
        command=deployment_observation_command(
            binding, key="ordinary-deployment-replay-observation"
        ),
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_drill_condition_rejects_an_ordinary_observation_reference(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "condition-owned-unit")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    ordinary = record_observation(
        migrated_session, observation_command(key="condition-observation")
    )
    assert not isinstance(ordinary, DomainError)
    migrated_session.add(
        ProductionDrillResource(
            run_id=drill.id,
            resource_type="work_unit",
            resource_id=unit.id,
        )
    )
    migrated_session.commit()

    result = record_production_drill_reconciliation_condition(
        migrated_session,
        run_id=drill.id,
        command=ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={"conclusion": "success"},
            observed_state={"conclusion": "failure"},
            detail="Synthetic check changed after verification",
            observation_id=ordinary.id,
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_resource_not_owned"


def test_drill_condition_rejects_an_ordinary_deployment_observation_reference(
    migrated_session: Session,
) -> None:
    unit, binding = release_binding(migrated_session, key="condition-deployment")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(
            run_id=drill.id,
            resource_type="work_unit",
            resource_id=unit.id,
        )
    )
    deployment = record_deployment_observation(
        migrated_session,
        deployment_observation_command(binding, key="condition-deployment-observation"),
    )
    assert not isinstance(deployment, DomainError)

    result = record_production_drill_reconciliation_condition(
        migrated_session,
        run_id=drill.id,
        command=ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="deployment",
            condition_type="deploy_split_brain",
            key_facts={"environment": "production"},
            stored_state={"state": "submitted"},
            observed_state={"state": "stalled"},
            detail="Synthetic deployment verification stalled",
            deployment_observation_id=deployment.id,
        ),
    )

    assert isinstance(result, DomainError)
    assert result.code == "production_drill_resource_not_owned"


def test_drill_lifecycle_control_rejects_an_ordinary_work_unit(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "ordinary-lifecycle")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)

    with pytest.raises(DomainError, match="does not belong to the production drill run"):
        transition_production_drill_unit(
            migrated_session,
            run_id=drill.id,
            command=TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.SUBMITTED,
                actor=ActorContext("human-1", ActorRole.HUMAN),
                expected_version=unit.version,
                idempotency_key="ordinary-lifecycle",
            ),
        )


def test_ordinary_projections_hide_drill_resources_by_default(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "hidden-drill-resource")
    drill = start_production_drill(migrated_session, command(unit.work_package_revision_id))
    assert not isinstance(drill, DomainError)
    unit.state = WorkUnitState.FAILED
    migrated_session.add(
        ProductionDrillResource(run_id=drill.id, resource_type="work_unit", resource_id=unit.id)
    )
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
