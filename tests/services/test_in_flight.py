"""WS-P2.1 Task 14: the runner's read surface (AC-009)."""

from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.in_flight import in_flight_snapshot
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import upsert_pr_binding
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HEAD = "a" * 40


def test_in_flight_units_carry_their_pr_binding(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "inflight-executing")
    unit.state = WorkUnitState.EXECUTING
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=41, head_sha=HEAD
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
