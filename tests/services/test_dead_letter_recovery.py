"""SDS 1.1 review 7b: each dead-letter entry names a recovery action that its route accepts.

The text is checked against the routes, not against itself. Every failed or blocked case
calls the requeue it does or doesn't name, and the retry case calls `authorize_retry`.
"""

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.lifecycle.claims import authorize_retry, requeue_unit
from orchestrator.services.reporting.dead_letter import dead_letter, recovery_action
from tests._support.seeding import register_unit
from tests.services.test_budget import READY_UNIT_MAX_LLM_CALLS, _cost_event
from tests.services.test_package_intake import human_actor
from tests.services.test_reclaim import authorize_readiness

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def _unit(
    session: Session, key: str, state: WorkUnitState, *, attempts_left: bool, over_budget: bool
) -> WorkUnit:
    unit = register_unit(session, key)
    authorize_readiness(session, unit)
    unit.state = state
    unit.attempt_count = 1 if attempts_left else unit.max_attempts
    if over_budget:
        assert READY_UNIT_MAX_LLM_CALLS is not None
        _cost_event(session, unit.id, llm_calls=READY_UNIT_MAX_LLM_CALLS)
    session.commit()
    return unit


@pytest.mark.parametrize(
    ("state", "attempts_left", "over_budget", "action"),
    [
        (
            WorkUnitState.FAILED,
            True,
            False,
            "Cancel this unit, or have the system requeue it",
        ),
        (
            WorkUnitState.FAILED,
            False,
            False,
            "Authorize a retry with a raised attempt limit, or cancel this unit",
        ),
        (
            WorkUnitState.FAILED,
            True,
            True,
            "Cancel this unit: neither a requeue nor a retry lets it run (budget_exceeded)",
        ),
        (
            WorkUnitState.FAILED,
            False,
            True,
            "Cancel this unit: neither a requeue nor a retry lets it run (attempts_exhausted)",
        ),
        (WorkUnitState.BLOCKED, True, False, "Have the system requeue this unit"),
        (
            WorkUnitState.BLOCKED,
            False,
            False,
            "None: requeue refuses it (attempts_exhausted), and no other route takes it",
        ),
        (
            WorkUnitState.BLOCKED,
            True,
            True,
            "None: requeue refuses it (budget_exceeded), and no other route takes it",
        ),
    ],
)
def test_a_named_requeue_is_accepted_and_an_unnamed_one_is_refused(
    migrated_session: Session,
    state: WorkUnitState,
    attempts_left: bool,
    over_budget: bool,
    action: str,
) -> None:
    unit = _unit(
        migrated_session,
        f"dl-route-{state}-{attempts_left}-{over_budget}",
        state,
        attempts_left=attempts_left,
        over_budget=over_budget,
    )

    assert recovery_action(migrated_session, unit) == action
    (entry,) = (
        entry
        for entry in dead_letter(
            migrated_session,
            failure_signature_threshold=3,
            stalled_approval_seconds=604_800,
            stalled_verification_seconds=604_800,
        )
        if entry.work_unit_id == unit.id
    )

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="dead-letter probe", idempotency_key="probe"
    )
    named = action in (
        "Cancel this unit, or have the system requeue it",
        "Have the system requeue this unit",
    )
    assert isinstance(result, DomainError) is not named
    assert entry.requeue_eligible is named


def test_a_named_retry_is_accepted(migrated_session: Session) -> None:
    unit = _unit(
        migrated_session,
        "dl-route-retry",
        WorkUnitState.FAILED,
        attempts_left=False,
        over_budget=False,
    )
    assert recovery_action(migrated_session, unit).startswith("Authorize a retry")

    result = authorize_retry(
        migrated_session,
        unit.id,
        human_actor(),
        new_max_attempts=unit.max_attempts + 1,
        reason="dead-letter probe",
        idempotency_key="probe-retry",
    )

    assert not isinstance(result, DomainError)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (
            WorkUnitState.READY,
            "Have the system dispatch this unit again once the failure's cause is fixed",
        ),
        (
            WorkUnitState.DRAFT,
            "None yet: the orchestrator readies this unit once its readiness holds",
        ),
        (
            WorkUnitState.AWAITING_APPROVAL,
            "Cancel this unit on its review page, or approve it with the human approve command",
        ),
        (
            WorkUnitState.AWAITING_REVIEW,
            "Complete this unit or request a revision on its review page",
        ),
        (WorkUnitState.SUBMITTED, "Have the verifier evaluate this unit"),
        (WorkUnitState.VERIFYING, "Have the verifier evaluate this unit"),
        (WorkUnitState.EXECUTING, "None: the unit is executing and has moved past this failure"),
    ],
)
def test_every_other_state_names_its_owner(
    migrated_session: Session, state: WorkUnitState, action: str
) -> None:
    unit = register_unit(migrated_session, f"dl-owner-{state}")
    unit.state = state
    migrated_session.commit()

    assert recovery_action(migrated_session, unit) == action
