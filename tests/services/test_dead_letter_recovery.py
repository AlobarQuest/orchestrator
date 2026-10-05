"""SDS 1.1 review 7b: each dead-letter entry names a recovery action that its route accepts.

The text is checked against the routes, not against itself. Every failed or blocked case
calls the requeue it does or doesn't name, and the retry case calls `authorize_retry`.
"""

import pytest
from sqlalchemy import Engine, select, text
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
    session: Session,
    key: str,
    state: WorkUnitState,
    *,
    attempts_left: bool,
    over_budget: bool,
    ready: bool = True,
) -> WorkUnit:
    unit = register_unit(session, key)
    if ready:
        authorize_readiness(session, unit)
    unit.state = state
    unit.attempt_count = 1 if attempts_left else unit.max_attempts
    if over_budget:
        assert READY_UNIT_MAX_LLM_CALLS is not None
        _cost_event(session, unit.id, llm_calls=READY_UNIT_MAX_LLM_CALLS)
    session.commit()
    return unit


REQUEUE = "Cancel this unit, or have the system requeue it"
RETRY = "Cancel this unit, or authorize a retry with a raised attempt limit"


@pytest.mark.parametrize(
    ("state", "attempts_left", "over_budget", "ready", "action"),
    [
        (WorkUnitState.FAILED, True, False, True, REQUEUE),
        (WorkUnitState.FAILED, False, False, True, RETRY),
        (
            WorkUnitState.FAILED,
            True,
            False,
            False,
            "Cancel this unit, or resolve its readiness, then have the system requeue it",
        ),
        (
            WorkUnitState.FAILED,
            False,
            False,
            False,
            "Cancel this unit, or resolve its readiness, then authorize a retry with a raised "
            "attempt limit",
        ),
        (
            WorkUnitState.FAILED,
            True,
            True,
            True,
            "Cancel this unit: its LLM-call budget is spent, and nothing raises it",
        ),
        (
            WorkUnitState.FAILED,
            False,
            True,
            True,
            "Cancel this unit: its LLM-call budget is spent, and nothing raises it",
        ),
        (WorkUnitState.BLOCKED, True, False, True, "Have the system requeue it"),
        (
            WorkUnitState.BLOCKED,
            True,
            False,
            False,
            "Resolve its readiness, then have the system requeue it",
        ),
        (
            WorkUnitState.BLOCKED,
            False,
            False,
            True,
            "None: its attempt budget is spent, and only a failed unit can be retried",
        ),
        (
            WorkUnitState.BLOCKED,
            True,
            True,
            True,
            "None: its LLM-call budget is spent, and nothing raises it (budget_exceeded)",
        ),
    ],
)
def test_a_named_requeue_is_accepted_and_an_unnamed_one_is_refused(
    migrated_session: Session,
    state: WorkUnitState,
    attempts_left: bool,
    over_budget: bool,
    ready: bool,
    action: str,
) -> None:
    unit = _unit(
        migrated_session,
        f"dl-route-{state}-{attempts_left}-{over_budget}-{ready}",
        state,
        attempts_left=attempts_left,
        over_budget=over_budget,
        ready=ready,
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
    named = action in (REQUEUE, "Have the system requeue it")
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
    assert recovery_action(migrated_session, unit) == RETRY

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
            "Cancel this unit on its review page; no route a person can reach approves it",
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


def test_reading_the_view_takes_no_unit_lock(migrated_engine: Engine) -> None:
    """The view reads readiness without `FOR UPDATE`, so a writer holding a unit never stalls it."""
    with Session(migrated_engine) as setup:
        unit_id = _unit(
            setup, "dl-no-lock", WorkUnitState.FAILED, attempts_left=True, over_budget=False
        ).id

    with Session(migrated_engine) as writer, Session(migrated_engine) as reader:
        writer.execute(select(WorkUnit).where(WorkUnit.id == unit_id).with_for_update())
        reader.execute(text("SET LOCAL lock_timeout = '200ms'"))

        entries = dead_letter(
            reader,
            failure_signature_threshold=3,
            stalled_approval_seconds=604_800,
            stalled_verification_seconds=604_800,
        )

        assert [entry.requeue_eligible for entry in entries] == [True]
