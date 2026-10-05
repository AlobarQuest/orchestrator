"""The orchestrator readies a unit itself once its readiness holds (SDS 1.1 item 2d-1)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.intake.decomposition import (
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.package_intake import register_package_intake
from orchestrator.services.intake.packages import (
    DependencySpec,
    record_approval,
    register_dependency_with_event,
    resolve_dependency_command,
)
from orchestrator.services.lifecycle.lifecycle import TransitionCommand, transition_unit
from tests._support.seeding import register_unit
from tests.services.test_authority_known_good import uv_bump
from tests.services.test_decomposition import (
    package_ac_ids,
    proposal_command,
    register_intaken_revision,
    worker_actor,
)
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command

HUMAN = ActorContext("human-1", ActorRole.HUMAN)


def _approve(
    session: Session, unit: WorkUnit, key: str = "authority-1", *, version: int | None = None
) -> None:
    record_approval(
        session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
        reason="Envelope reviewed.",
        idempotency_key=key,
        expected_version=unit.version if version is None else version,
    )


def _ready_event(session: Session, unit: WorkUnit) -> Event | None:
    return session.scalar(
        select(Event).where(
            Event.subject_id == unit.id,
            Event.action == "work_unit.transitioned",
            Event.to_state == "ready",
        )
    )


def test_an_authority_approval_readies_the_unit_as_a_system_step_by_its_approver(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "approved-unit")

    _approve(migrated_session, unit)

    assert unit.state == WorkUnitState.READY
    event = _ready_event(migrated_session, unit)
    assert event is not None
    assert event.actor_id == "human-1"
    assert event.payload["actor_role"] == ActorRole.SYSTEM
    assert event.idempotency_key == "authority-1:auto-ready"


def test_a_pending_dependency_holds_the_unit_until_it_is_resolved(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "dependent-unit")
    dependency = register_dependency_with_event(
        migrated_session,
        work_unit_id=unit.id,
        spec=DependencySpec.external("ci/build", "passed"),
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
        idempotency_key="dependency-1",
    )
    _approve(migrated_session, unit)
    assert unit.state == WorkUnitState.DRAFT

    resolve_dependency_command(
        migrated_session,
        dependency_id=dependency.id,
        status="satisfied",
        detail={"run": 1},
        actor_id="system",
        actor_role=ActorRole.SYSTEM,
        expected_version=unit.version,
        idempotency_key="resolve-1",
    )

    assert unit.state == WorkUnitState.READY
    event = _ready_event(migrated_session, unit)
    assert event is not None and event.idempotency_key == "resolve-1:auto-ready"


def test_a_replayed_approval_readies_once(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "replayed-unit")
    approved_at = unit.version
    _approve(migrated_session, unit)
    version = unit.version

    _approve(migrated_session, unit, version=approved_at)  # the identical request, again

    assert unit.version == version
    events = migrated_session.scalars(
        select(Event).where(Event.subject_id == unit.id, Event.to_state == "ready")
    ).all()
    assert len(events) == 1


def test_a_decomposition_approval_leaves_an_unapproved_envelope_in_draft(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, package_ac_ids(migrated_session, revision.id)),
        worker_actor(),
    )

    approve_decomposition_proposal(
        migrated_session, proposal.id, actor=human_actor(), reason="Ok.", idempotency_key="p-ok"
    )

    units = migrated_session.scalars(
        select(WorkUnit).where(WorkUnit.work_package_revision_id == revision.id)
    ).all()
    assert {unit.state for unit in units} == {WorkUnitState.DRAFT}


def test_a_decomposition_approval_readies_a_unit_policy_already_recognises(
    migrated_session: Session,
) -> None:
    """Readiness holds at approval when policy recognises the envelope (a declared reach and the
    uv profile's known-good shape) and the unit has no dependency, so no other event will come."""
    snapshot = {**intake_command().enforcement_snapshot, "reach": ["source_repository"]}
    revision = register_package_intake(
        migrated_session,
        intake_command(
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            enforcement_snapshot=snapshot,
        ),
        human_actor(),
    )
    command = proposal_command(revision.id, package_ac_ids(migrated_session, revision.id))
    envelope = uv_bump(uuid.uuid4())
    # The orchestrator assigns the unit id into the envelope at proposal time.
    del envelope["constraints"]["work_unit_id"]
    units = tuple(
        unit.__class__(**{**unit.__dict__, "authority": normalize_authority(envelope)})
        for unit in command.proposed_units
    )
    proposal = submit_decomposition_proposal(
        migrated_session,
        command.__class__(**{**command.__dict__, "proposed_units": units, "dependencies": ()}),
        worker_actor(),
    )

    approve_decomposition_proposal(
        migrated_session, proposal.id, actor=human_actor(), reason="Ok.", idempotency_key="p-ok"
    )

    states = migrated_session.scalars(
        select(WorkUnit.state).where(WorkUnit.work_package_revision_id == revision.id)
    ).all()
    assert set(states) == {WorkUnitState.READY}


def test_commands_ready_refuses_a_unit_whose_readiness_does_not_hold(
    migrated_session: Session,
) -> None:
    """The edge had no readiness guard before; only the runner's brief refusal stopped it."""
    unit = register_unit(migrated_session, "not-ready-unit")
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.READY,
                actor=ActorContext("system", ActorRole.SYSTEM),
                expected_version=unit.version,
                idempotency_key="manual-ready",
            ),
        )

    assert error.value.code == "readiness_not_satisfied"


def test_an_approval_on_a_unit_past_draft_leaves_its_state_alone(migrated_session: Session) -> None:
    """A fresh authority approval on a READY unit (a reclaim takes one) must not move it again."""
    unit = register_unit(migrated_session, "already-ready-unit")
    _approve(migrated_session, unit)
    version = unit.version

    _approve(migrated_session, unit, "authority-2")

    assert (unit.state, unit.version) == (WorkUnitState.READY, version)


def test_a_dependency_resolved_as_failed_does_not_ready_the_unit(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "failed-dependency-unit")
    dependency = register_dependency_with_event(
        migrated_session,
        work_unit_id=unit.id,
        spec=DependencySpec.external("ci/build", "passed"),
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
        idempotency_key="dependency-failing",
    )
    _approve(migrated_session, unit)

    resolve_dependency_command(
        migrated_session,
        dependency_id=dependency.id,
        status="failed",
        detail={"run": 2},
        actor_id="system",
        actor_role=ActorRole.SYSTEM,
        expected_version=unit.version,
        idempotency_key="resolve-failed",
    )

    assert unit.state == WorkUnitState.DRAFT


def test_a_decomposition_approval_readies_only_the_units_without_a_pending_dependency(
    migrated_session: Session,
) -> None:
    """The shared proposal has unit-2 depending on unit-1: unit-1 readies, unit-2 waits."""
    snapshot = {**intake_command().enforcement_snapshot, "reach": ["source_repository"]}
    revision = register_package_intake(
        migrated_session,
        intake_command(
            package_id="pkg-mixed",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
            enforcement_snapshot=snapshot,
        ),
        human_actor(),
    )
    command = proposal_command(revision.id, package_ac_ids(migrated_session, revision.id))
    envelope = uv_bump(uuid.uuid4())
    del envelope["constraints"]["work_unit_id"]
    units = tuple(
        unit.__class__(**{**unit.__dict__, "authority": normalize_authority(envelope)})
        for unit in command.proposed_units
    )
    proposal = submit_decomposition_proposal(
        migrated_session,
        command.__class__(**{**command.__dict__, "proposed_units": units}),
        worker_actor(),
    )

    approve_decomposition_proposal(
        migrated_session, proposal.id, actor=human_actor(), reason="Ok.", idempotency_key="p-mixed"
    )

    rows = migrated_session.execute(
        select(WorkUnit.unit_key, WorkUnit.state).where(
            WorkUnit.work_package_revision_id == revision.id
        )
    ).all()
    states = {unit_key: state for unit_key, state in rows}
    assert states == {"unit-1": WorkUnitState.READY, "unit-2": WorkUnitState.DRAFT}
