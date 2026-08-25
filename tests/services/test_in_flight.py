"""WS-P2.1 Task 14: the runner's read surface (AC-009)."""

import warnings

from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.deployment_observations import record_deployment_observation
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import upsert_pr_binding
from orchestrator.services.reconciliation_detection import detect_reconciliation_conditions
from tests.services.test_dependencies import register_unit
from tests.services.test_deployment_observations import (
    activation_command,
    machine_local_binding,
)

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HEAD = "a" * 40


def test_in_flight_units_carry_their_pr_binding(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "inflight-executing")
    unit.state = WorkUnitState.EXECUTING
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=41, head_sha=HEAD, attempt=1
    )
    migrated_session.commit()

    snapshot = in_flight_snapshot(migrated_session)

    assert [view.work_unit_id for view in snapshot.units] == [unit.id]
    assert snapshot.units[0].pr_number == 41
    assert snapshot.units[0].head_sha == HEAD
    assert snapshot.units[0].verification_read_head_sha is None


def test_terminal_units_without_bindings_are_excluded(migrated_session: Session) -> None:
    completed = register_unit(migrated_session, "inflight-done")
    completed.state = WorkUnitState.COMPLETED
    cancelled = register_unit(migrated_session, "inflight-cancelled")
    cancelled.state = WorkUnitState.CANCELLED
    migrated_session.commit()

    snapshot = in_flight_snapshot(migrated_session)

    assert snapshot.units == ()
    assert snapshot.release_bindings == ()


def test_a_binding_with_no_deployment_observation_is_visible_as_unverified(
    migrated_session: Session, unreported_binding
) -> None:
    """THE reason bindings are in this surface at all.

    A unit carrying a release binding is COMPLETED -- so it is not in-flight. Without the
    bindings here, the runner would be structurally blind to the deploy nobody reported.
    `has_post_deploy_unit = False` IS that signal.
    """
    snapshot = in_flight_snapshot(migrated_session)

    assert [view.binding_id for view in snapshot.release_bindings] == [unreported_binding.id]
    view = snapshot.release_bindings[0]
    assert view.work_unit_state == WorkUnitState.COMPLETED
    assert view.has_post_deploy_unit is False
    assert view.post_deploy_unit_state is None


def test_a_deployed_binding_reports_its_post_deploy_unit(
    migrated_session: Session, deployed_binding
) -> None:
    snapshot = in_flight_snapshot(migrated_session)

    views = [v for v in snapshot.release_bindings if v.binding_id == deployed_binding.id]
    assert len(views) == 1
    assert views[0].has_post_deploy_unit is True
    assert views[0].post_deploy_unit_state == WorkUnitState.SUBMITTED
    assert views[0].post_deploy_unit_created_at is not None


def test_the_snapshot_is_read_only(migrated_session: Session, deployed_binding) -> None:
    before = {
        (u.id, u.state, u.version)
        for u in migrated_session.query(WorkUnit).all()  # noqa: F401
    }

    in_flight_snapshot(migrated_session)

    migrated_session.expire_all()
    after = {(u.id, u.state, u.version) for u in migrated_session.query(WorkUnit).all()}
    assert after == before


def test_a_machine_local_activation_reports_no_verification_unit_and_no_split_brain(
    migrated_session: Session,
) -> None:
    """READ `has_post_deploy_unit` LITERALLY, because ADR-0030's activation check changed what it
    can be inferred from.

    A machine-local activation is OBSERVED and mints no verification unit, so a binding can carry
    an observation and still report False. The detector that acts on "a deploy nobody reported"
    keys on the ABSENCE OF AN OBSERVATION, which it reads for itself; the detector pass here
    additionally proves its join tolerates the NULL rather than raising on it. Pinning both halves
    is what stops a future consumer inheriting the older reading by accident.
    """
    _unit, binding = machine_local_binding(migrated_session, key="in-flight-activation")
    observation = record_deployment_observation(migrated_session, activation_command(binding))
    assert not isinstance(observation, DomainError)
    migrated_session.commit()

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        snapshot = in_flight_snapshot(migrated_session)

    # The read must not ASK for a unit that is legitimately absent. SQLAlchemy answers a NULL
    # primary key with None and a warning saying it may raise in a future release, so this is
    # the control that can see the guard: without it the warning appears here.
    assert not [w for w in raised if issubclass(w.category, SAWarning)]

    views = [v for v in snapshot.release_bindings if v.binding_id == binding.id]
    assert len(views) == 1
    assert views[0].has_post_deploy_unit is False
    assert views[0].post_deploy_unit_state is None

    counters = detect_reconciliation_conditions(migrated_session, actor=SYSTEM, stall_seconds=0)
    assert counters.conditions_recorded == 0
