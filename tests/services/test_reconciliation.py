"""WS-P2.1 Task 5: recording a reconciliation condition, and resolving it exactly once."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Event,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    ResolutionCommand,
    open_conditions,
    record_reconciliation_condition,
    record_resolution,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)


def flip(unit_id: uuid.UUID) -> ConditionCommand:
    return ConditionCommand(
        actor=SYSTEM,
        work_unit_id=unit_id,
        observation_kind="github_check",
        condition_type="check_result_flip",
        key_facts={"check_name": "Quality"},
        stored_state={"conclusion": "success"},
        observed_state={"conclusion": "failure"},
        detail="Quality flipped from success to failure after verification read it",
    )


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_recording_a_condition_writes_the_condition_and_an_event(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "reconcile-basic")
    migrated_session.commit()

    outcome = record_reconciliation_condition(migrated_session, flip(unit.id))

    assert isinstance(outcome, ConditionOutcome)
    assert outcome.suppressed is False
    condition = outcome.condition
    assert condition.resolution_generation == 0
    event = migrated_session.get(Event, condition.event_id)
    assert event is not None
    assert event.action == "reconciliation.required"
    assert event.subject_type == "reconciliation_condition"
    assert event.subject_id == condition.id


def test_recording_a_condition_never_mutates_the_work_unit(migrated_session: Session) -> None:
    """Failure modes #3/#4: detection never auto-un-completes a completed unit and never
    transitions anything. `version` is the observable -- every transition bumps it."""
    unit = register_unit(migrated_session, "reconcile-completed")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    state_before, version_before = unit.state, unit.version

    outcome = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="external_merge_alarm",
            key_facts={"pr_number": 12, "head_sha": "a" * 40},
            stored_state={"state": "completed"},
            observed_state={"merged": True},
            detail="pull request merged outside the session on a completed unit",
        ),
    )

    assert isinstance(outcome, ConditionOutcome)
    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert (refreshed.state, refreshed.version) == (state_before, version_before)


def test_an_unresolved_condition_dedups_on_re_detection(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-dedup")
    migrated_session.commit()

    first = record_reconciliation_condition(migrated_session, flip(unit.id))
    second = record_reconciliation_condition(migrated_session, flip(unit.id))

    assert isinstance(first, ConditionOutcome)
    assert isinstance(second, ConditionOutcome)
    assert second.suppressed is True
    assert second.condition.id == first.condition.id
    assert _count(migrated_session, ReconciliationCondition) == 1
    # No duplicate event either -- a re-detection is a replay-return, not a 500 and not a row.
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Event).where(Event.action == "reconciliation.required")
        )
        == 1
    )


def test_a_divergence_recurring_after_resolution_is_raisable_again(
    migrated_session: Session,
) -> None:
    """THE reason the divergence hash folds in a resolution generation.

    check_result_flip and deploy_split_brain recur with IDENTICAL key facts. A generation-free
    hash would hit the UNIQUE, be silently swallowed, and never re-emit reconciliation.required --
    permanently blinding the operator to exactly the conditions that recur.
    """
    unit = register_unit(migrated_session, "reconcile-recur")
    migrated_session.commit()
    first = record_reconciliation_condition(migrated_session, flip(unit.id))
    assert isinstance(first, ConditionOutcome)
    assert first.condition.resolution_generation == 0

    resolved = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=first.condition.id,
            decision="corrected",
            rationale="Re-ran the check; it is green.",
            idempotency_key="resolve-recur-1",
        ),
    )
    assert isinstance(resolved, ReconciliationResolution)

    recurrence = record_reconciliation_condition(migrated_session, flip(unit.id))

    assert isinstance(recurrence, ConditionOutcome)
    assert recurrence.suppressed is False
    assert recurrence.condition.id != first.condition.id
    assert recurrence.condition.resolution_generation == 1
    assert recurrence.condition.lineage_hash == first.condition.lineage_hash
    assert (
        recurrence.condition.normalized_divergence_hash
        != first.condition.normalized_divergence_hash
    )
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Event).where(Event.action == "reconciliation.required")
        )
        == 2
    )


def test_a_condition_is_resolvable_exactly_once(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-once")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip(unit.id))
    assert isinstance(outcome, ConditionOutcome)

    first = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=outcome.condition.id,
            decision="accepted",
            rationale="Acknowledged.",
            idempotency_key="resolve-once-1",
        ),
    )
    second = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=outcome.condition.id,
            decision="dismissed",
            rationale="Changed my mind.",
            idempotency_key="resolve-once-2",
        ),
    )

    assert isinstance(first, ReconciliationResolution)
    assert isinstance(second, DomainError)
    assert second.code == "condition_already_resolved"
    assert _count(migrated_session, ReconciliationResolution) == 1


def test_resolution_duplicate_delivery_replays(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-replay")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip(unit.id))
    assert isinstance(outcome, ConditionOutcome)
    command = ResolutionCommand(
        actor=HUMAN,
        condition_id=outcome.condition.id,
        decision="accepted",
        rationale="Acknowledged.",
        idempotency_key="resolve-replay-1",
    )

    first = record_resolution(migrated_session, command)
    replay = record_resolution(migrated_session, command)

    assert isinstance(first, ReconciliationResolution)
    assert isinstance(replay, ReconciliationResolution)
    assert replay.id == first.id
    assert _count(migrated_session, ReconciliationResolution) == 1


def test_open_conditions_is_the_set_difference(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-open")
    migrated_session.commit()
    resolved = record_reconciliation_condition(migrated_session, flip(unit.id))
    assert isinstance(resolved, ConditionOutcome)
    record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=resolved.condition.id,
            decision="accepted",
            rationale="Acknowledged.",
            idempotency_key="resolve-open-1",
        ),
    )
    still_open = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="pr_state_divergence",
            key_facts={"pr_number": 3, "head_sha": "c" * 40},
            stored_state={"head_sha": "a" * 40},
            observed_state={"head_sha": "c" * 40},
            detail="head changed after verification read it",
        ),
    )
    assert isinstance(still_open, ConditionOutcome)

    assert tuple(row.id for row in open_conditions(migrated_session, unit.id)) == (
        still_open.condition.id,
    )


def test_key_fact_ordering_does_not_change_the_hash(migrated_session: Session) -> None:
    """key_facts is canonicalized at write time, so a reordered dict is the SAME divergence."""
    unit = register_unit(migrated_session, "reconcile-canonical")
    migrated_session.commit()

    def command(facts: dict[str, object]) -> ConditionCommand:
        return ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts=facts,
            stored_state={},
            observed_state={},
            detail="flip",
        )

    first = record_reconciliation_condition(
        migrated_session, command({"ac_id": "AC-001", "check_name": "Quality"})
    )
    reordered = record_reconciliation_condition(
        migrated_session, command({"check_name": "Quality", "ac_id": "AC-001"})
    )

    assert isinstance(first, ConditionOutcome)
    assert isinstance(reordered, ConditionOutcome)
    assert reordered.suppressed is True
    assert reordered.condition.id == first.condition.id


def test_distinct_condition_types_on_one_unit_do_not_collide(migrated_session: Session) -> None:
    """key_facts and condition_type are BOTH hash inputs. Omitting either would make two
    different alarms on one unit hash identically and silently swallow the second."""
    unit = register_unit(migrated_session, "reconcile-distinct")
    migrated_session.commit()

    merge_alarm = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="external_merge_alarm",
            key_facts={"pr_number": 1, "head_sha": "a" * 40},
            stored_state={},
            observed_state={"merged": True},
            detail="merged outside the session",
        ),
    )
    divergence = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="pr_state_divergence",
            key_facts={"pr_number": 1, "head_sha": "b" * 40},
            stored_state={},
            observed_state={"head_sha": "b" * 40},
            detail="head changed after verification read it",
        ),
    )

    assert isinstance(merge_alarm, ConditionOutcome)
    assert isinstance(divergence, ConditionOutcome)
    assert divergence.suppressed is False
    assert divergence.condition.id != merge_alarm.condition.id
    assert _count(migrated_session, ReconciliationCondition) == 2


def test_only_the_system_actor_may_record_a_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-role")
    migrated_session.commit()

    error = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=WORKER,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={},
            observed_state={},
            detail="worker-submitted condition",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "role_forbidden"


def test_only_a_human_may_resolve_a_condition(migrated_session: Session) -> None:
    """Invariant #4: detection never auto-resolves. Resolution is an operator decision."""
    unit = register_unit(migrated_session, "reconcile-resolve-role")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip(unit.id))
    assert isinstance(outcome, ConditionOutcome)

    error = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=SYSTEM,
            condition_id=outcome.condition.id,
            decision="accepted",
            rationale="Auto-resolved.",
            idempotency_key="resolve-role-1",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "role_forbidden"


def test_an_unknown_work_unit_is_rejected(migrated_session: Session) -> None:
    error = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=uuid.uuid4(),
            observation_kind="github_pr",
            condition_type="external_merge_alarm",
            key_facts={"pr_number": 1},
            stored_state={},
            observed_state={},
            detail="ghost unit",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "work_unit_not_found"
