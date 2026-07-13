import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from inspect import signature
from threading import Event, get_ident
from typing import TypedDict

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import ProductionDrillResource, WorkPackageRevision, WorkUnit
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
    transition_unit,
)
from orchestrator.services.observations import (
    record_observation,
    record_production_drill_observation,
)
from orchestrator.services.packages import _register_production_drill_unit, register_approved_unit
from orchestrator.services.production_drills import (
    RECOVERY_DRILLS_PACKAGE_ID,
    start_production_drill,
)
from orchestrator.services.reconciliation import (
    ConditionCommand,
    _record_production_drill_reconciliation_condition,
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
from tests.services.test_package_registration import (
    AUTHORITY,
    NOW,
    register_production_drill_revision,
)
from tests.services.test_production_drills import command, runtime_observation
from tests.services.test_release_artifacts import command as release_artifact_command
from tests.services.test_release_artifacts import completed_unit


def drill_command(session: Session, revision_id: uuid.UUID, *, key: str = "drill-1"):
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None or revision.work_package.package_id != RECOVERY_DRILLS_PACKAGE_ID:
        revision_id = register_production_drill_revision(session).id
    return command(
        revision_id,
        key=key,
        runtime_observation_id=runtime_observation(session, key=key),
    )


class UnitRegistration(TypedDict):
    revision_id: uuid.UUID
    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: AuthorityEnvelope
    approved_by: str
    approved_at: datetime
    actor_id: str
    actor_role: ActorRole


def test_ordinary_work_unit_cannot_self_tag_as_production_drill_resource() -> None:
    assert "run_id" not in WorkUnit.__table__.columns
    assert "run_id" not in signature(register_approved_unit).parameters


def test_drill_registration_rejects_an_existing_ordinary_unit_with_revision_locking(
    migrated_session: Session,
) -> None:
    revision = register_production_drill_revision(migrated_session)
    drill = start_production_drill(migrated_session, drill_command(migrated_session, revision.id))
    assert not isinstance(drill, DomainError)
    registration = _unit_registration(revision.id, "ordinary-before-drill")
    ordinary = register_approved_unit(migrated_session, **registration)
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        _register_production_drill_unit(migrated_session, run_id=drill.id, **registration)

    assert error.value.code == "production_drill_resource_not_owned"
    assert (
        migrated_session.scalar(
            select(ProductionDrillResource).where(
                ProductionDrillResource.resource_type == "work_unit",
                ProductionDrillResource.resource_id == ordinary.id,
            )
        )
        is None
    )


def test_concurrent_ordinary_registration_cannot_be_captured_as_drill_work(
    migrated_engine: Engine,
    migrated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.services import packages

    revision = register_production_drill_revision(migrated_session)
    migrated_session.commit()
    drill = start_production_drill(migrated_session, drill_command(migrated_session, revision.id))
    assert not isinstance(drill, DomainError)

    registration = _unit_registration(revision.id, "concurrent-drill-unit")
    release_drill_registration = Event()
    ordinary_revision_lock_attempted = Event()
    ordinary_worker_ready = Event()
    original_activation_check = packages._require_allowed_unit_activation
    drill_registration_paused = Event()
    ordinary_thread_ids: list[int] = []

    def pause_drill_after_revision_lock(*args, **kwargs) -> None:
        if not drill_registration_paused.is_set():
            drill_registration_paused.set()
            assert release_drill_registration.wait(timeout=5)
        original_activation_check(*args, **kwargs)

    def signal_ordinary_revision_lock_attempt(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if (
            get_ident() in ordinary_thread_ids
            and "FROM work_package_revisions" in statement
            and "FOR UPDATE" in statement
        ):
            ordinary_revision_lock_attempted.set()

    monkeypatch.setattr(
        packages, "_require_allowed_unit_activation", pause_drill_after_revision_lock
    )
    event.listen(migrated_engine, "before_cursor_execute", signal_ordinary_revision_lock_attempt)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            drill_registration = executor.submit(
                _register_drill_unit_and_commit, migrated_engine, drill.id, registration
            )
            assert drill_registration_paused.wait(timeout=5)
            ordinary_registration = executor.submit(
                _register_ordinary_unit_and_commit,
                migrated_engine,
                registration,
                ordinary_thread_ids,
                ordinary_worker_ready,
            )
            assert ordinary_worker_ready.wait(timeout=5)
            assert ordinary_revision_lock_attempted.wait(timeout=5)
            release_drill_registration.set()

            drill_unit_id = drill_registration.result(timeout=5)
            ordinary_unit_id = ordinary_registration.result(timeout=5)
    finally:
        release_drill_registration.set()
        event.remove(
            migrated_engine, "before_cursor_execute", signal_ordinary_revision_lock_attempt
        )

    assert ordinary_unit_id == drill_unit_id
    resource = migrated_session.scalar(
        select(ProductionDrillResource).where(
            ProductionDrillResource.resource_type == "work_unit",
            ProductionDrillResource.resource_id == drill_unit_id,
        )
    )
    assert resource is not None
    assert resource.run_id == drill.id


def _unit_registration(revision_id: uuid.UUID, unit_key: str) -> UnitRegistration:
    return {
        "revision_id": revision_id,
        "unit_key": unit_key,
        "title": "Concurrent drill unit",
        "outcome": "Concurrent drill unit is registered",
        "required_capability": "repository_write",
        "authority": AUTHORITY,
        "approved_by": "human-1",
        "approved_at": NOW,
        "actor_id": "human-1",
        "actor_role": ActorRole.HUMAN,
    }


def _register_drill_unit_and_commit(
    engine: Engine, run_id: uuid.UUID, registration: UnitRegistration
) -> uuid.UUID:
    with Session(engine) as session:
        unit = _register_production_drill_unit(session, run_id=run_id, **registration)
        unit_id = unit.id
        session.commit()
        return unit_id


def _register_ordinary_unit_and_commit(
    engine: Engine,
    registration: UnitRegistration,
    thread_ids: list[int],
    worker_ready: Event,
) -> uuid.UUID:
    with Session(engine) as session:
        thread_ids.append(get_ident())
        worker_ready.set()
        unit = register_approved_unit(session, **registration)
        unit_id = unit.id
        session.commit()
    return unit_id


def test_resource_cannot_belong_to_two_production_drill_runs(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "resource-owner")
    package_revision_id = unit.work_package_revision_id
    first_run = drill_command(migrated_session, package_revision_id, key="first-resource-run")
    second_run = drill_command(migrated_session, package_revision_id, key="second-resource-run")

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
    assert not hasattr(resources, "_bind_created_production_drill_resource")


def test_ordinary_transition_rejects_a_drill_owned_unit_but_drill_wrapper_allows_it(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "drill-lifecycle-boundary")
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=drill.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()
    lifecycle_command = TransitionCommand(
        unit_id=unit.id,
        target=WorkUnitState.READY,
        actor=ActorContext("system-1", ActorRole.SYSTEM),
        expected_version=unit.version,
        idempotency_key="drill-lifecycle-boundary",
    )

    with pytest.raises(DomainError) as error:
        transition_unit(migrated_session, lifecycle_command)

    assert error.value.code == "production_drill_resource_requires_drill_writer"
    transitioned = transition_production_drill_unit(
        migrated_session, run_id=drill.id, command=lifecycle_command
    )
    assert transitioned.state is WorkUnitState.READY


def test_ordinary_observation_idempotency_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "ordinary-observation")
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, ready_unit.work_package_revision_id)
    )
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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

    replay = _record_production_drill_reconciliation_condition(
        migrated_session, run_id=drill.id, command=condition
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_release_artifact_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit, _ordinary_binding = release_binding(migrated_session, key="ordinary-release-replay")
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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


def test_drill_release_artifact_binds_generated_evidence_and_requires_it_on_replay(
    migrated_session: Session,
) -> None:
    unit = completed_unit(migrated_session, key="drill-release-evidence")
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
    assert not isinstance(drill, DomainError)
    migrated_session.add(
        ProductionDrillResource(run_id=drill.id, resource_type="work_unit", resource_id=unit.id)
    )
    migrated_session.commit()
    artifact_command = release_artifact_command(unit, key="drill-release-evidence")
    artifact = record_production_drill_release_artifact(
        migrated_session, run_id=drill.id, command=artifact_command
    )
    assert not isinstance(artifact, DomainError)

    evidence_resource = migrated_session.scalar(
        select(ProductionDrillResource).where(
            ProductionDrillResource.run_id == drill.id,
            ProductionDrillResource.resource_type == "evidence",
            ProductionDrillResource.resource_id == artifact.evidence_id,
        )
    )
    assert evidence_resource is not None
    migrated_session.delete(evidence_resource)
    migrated_session.commit()

    replay = record_production_drill_release_artifact(
        migrated_session, run_id=drill.id, command=artifact_command
    )

    assert isinstance(replay, DomainError)
    assert replay.code == "production_drill_resource_not_owned"


def test_ordinary_deployment_observation_replay_cannot_be_captured_by_a_drill(
    migrated_session: Session,
) -> None:
    unit, binding = release_binding(migrated_session, key="ordinary-deployment-replay")
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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

    result = _record_production_drill_reconciliation_condition(
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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

    result = _record_production_drill_reconciliation_condition(
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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
    drill = start_production_drill(
        migrated_session, drill_command(migrated_session, unit.work_package_revision_id)
    )
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
