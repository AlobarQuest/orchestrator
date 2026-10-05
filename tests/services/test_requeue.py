"""WS-P2.1 Task 11: requeue (AC-006).

The ONLY genuinely new recovery action. `retry` already exists twice -- at
`/review/units/{id}/retry` and `POST /api/v1/work-units/{id}/retry-authorization` -- and both are
pinned. Adding a third would fail the pinned-route test. requeue covers the case retry does not:
attempts NOT exhausted.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.intake.packages import DependencySpec
from orchestrator.services.lifecycle.claims import (
    LeaseGrant,
    authorize_retry,
    claim_unit,
    renew_claim,
    requeue_unit,
)
from orchestrator.services.lifecycle.lifecycle import TransitionCommand, transition_unit
from orchestrator.services.reporting.dead_letter import dead_letter
from tests._support.seeding import register_unit
from tests.services.test_claims import worker
from tests.services.test_package_intake import human_actor
from tests.services.test_reclaim import authorize_readiness

# Long enough that nothing in these fixtures is stale; the stalled-approval
# report has its own tests.
STALLED_APPROVAL_SECONDS = 604_800

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)


def test_requeue_moves_a_failed_unit_back_to_ready(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-failed")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()

    result = requeue_unit(
        migrated_session,
        unit.id,
        SYSTEM,
        reason="runner host died",
        idempotency_key="requeue-1",
    )

    assert isinstance(result, WorkUnit)
    assert result.state == WorkUnitState.READY
    event = migrated_session.scalar(select(Event).where(Event.idempotency_key == "requeue-1"))
    assert event is not None
    assert (event.from_state, event.to_state) == (WorkUnitState.FAILED, WorkUnitState.READY)
    # The operator is ATTRIBUTED even though the edge is authorized as SYSTEM.
    assert event.actor_id == "system"
    assert event.payload["reason"] == "runner host died"


def test_requeue_moves_a_blocked_unit_back_to_ready(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-blocked")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.BLOCKED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="blocker cleared", idempotency_key="requeue-2"
    )

    assert isinstance(result, WorkUnit)
    assert result.state == WorkUnitState.READY


def test_requeue_refuses_an_exhausted_unit_and_it_stays_visible(
    migrated_session: Session,
) -> None:
    """THE hazard. Requeue past the budget would land the unit READY, where claim_unit rejects it
    with attempts_exhausted -- and it would drop out of the failed-units dead-letter view at the
    same moment. Invisible AND unrunnable. `retry` (which raises the budget) is that path."""
    unit = register_unit(migrated_session, "requeue-exhausted")
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = unit.max_attempts
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="one more go", idempotency_key="requeue-3"
    )

    assert isinstance(result, DomainError)
    assert result.code == "attempts_exhausted"
    assert result.recovery == "approve_retry"
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert refreshed.state == WorkUnitState.FAILED  # unchanged
    visible = {
        entry.work_unit_id
        for entry in dead_letter(
            migrated_session,
            failure_signature_threshold=3,
            stalled_approval_seconds=STALLED_APPROVAL_SECONDS,
            stalled_verification_seconds=604_800,
        )
        if entry.source == "work_unit"
    }
    assert unit.id in visible  # still visible, still actionable via retry


def test_requeue_refuses_when_readiness_is_not_satisfied(migrated_session: Session) -> None:
    """Reuses the SAME eligibility check reclaim uses. A unit whose dependency is unresolved
    would land READY and be unclaimable -- the same invisible-and-unrunnable trap."""
    unit = register_unit(
        migrated_session,
        "requeue-unready",
        dependencies=(
            DependencySpec(
                kind="external_system",
                required_state_or_condition="approved",
                external_ref="CAB-1",
            ),
        ),
    )
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="try again", idempotency_key="requeue-4"
    )

    assert isinstance(result, DomainError)
    assert result.code == "readiness_not_satisfied"
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None and refreshed.state == WorkUnitState.FAILED


def test_requeue_is_system_only(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-role")
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, WORKER, reason="mine now", idempotency_key="requeue-5"
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_requeue_refuses_a_completed_unit(migrated_session: Session) -> None:
    """Recovery never un-completes. Only failed or blocked work may be requeued."""
    unit = register_unit(migrated_session, "requeue-completed")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="undo", idempotency_key="requeue-6"
    )

    assert isinstance(result, DomainError)
    assert result.code == "requeue_not_allowed"


def test_a_requeued_unit_is_actually_claimable(migrated_session: Session) -> None:
    """The point of the eligibility check: READY must mean genuinely runnable."""
    unit = register_unit(migrated_session, "requeue-claimable")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()
    requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="retry the run", idempotency_key="requeue-7"
    )

    grant = claim_unit(migrated_session, unit.id, worker(), "requeue-7-claim")

    assert not isinstance(grant, DomainError)
    assert grant.attempt == 2


def test_requeue_replays_on_duplicate_delivery(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-replay")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()

    first = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="host died", idempotency_key="requeue-8"
    )
    replay = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="host died", idempotency_key="requeue-8"
    )

    assert isinstance(first, WorkUnit)
    assert isinstance(replay, WorkUnit)
    assert replay.version == first.version  # the replay did not transition again
    events = list(
        migrated_session.scalars(select(Event).where(Event.idempotency_key == "requeue-8"))
    )
    assert len(events) == 1


def test_a_retried_blocked_unit_is_claimed_afresh_and_the_old_lease_is_dead(
    migrated_session: Session,
) -> None:
    """Blocking does not release the claim, so the retry must not let the old lease act again."""
    unit = register_unit(migrated_session, "retry-blocked-claimed")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.READY
    unit.max_attempts = 1
    migrated_session.commit()
    first = claim_unit(migrated_session, unit.id, worker(), "retry-blocked-claim-1")
    assert isinstance(first, LeaseGrant)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.BLOCKED,
            actor=worker(),
            expected_version=unit.version,
            idempotency_key="retry-blocked-block",
            attempt=first.attempt,
            lease_token=first.lease_token,
        ),
    )

    retried = authorize_retry(
        migrated_session,
        unit.id,
        human_actor(),
        new_max_attempts=2,
        reason="the blocker is cleared",
        idempotency_key="retry-blocked-retry",
    )
    assert not isinstance(retried, DomainError)
    second = claim_unit(migrated_session, unit.id, worker(), "retry-blocked-claim-2")

    assert isinstance(second, LeaseGrant)
    assert second.attempt == first.attempt + 1
    stale = renew_claim(migrated_session, unit.id, worker(), first.attempt, first.lease_token)
    assert isinstance(stale, DomainError)
