"""AC-007: the WS-P2.1 ingress, idempotent from birth."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Evidence,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkUnit,
)
from orchestrator.services.claims import requeue_unit
from orchestrator.services.evidence import recover_evidence
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    ResolutionCommand,
    record_reconciliation_condition,
    record_resolution,
)
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_reconciliation_conditions,
)
from tests.services.test_claims import worker
from tests.services.test_dependencies import register_unit
from tests.services.test_evidence_recovery import expired_claim, heads, recovery_kwargs
from tests.services.test_reclaim import authorize_readiness

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)


def _flip(unit_id) -> ConditionCommand:
    return ConditionCommand(
        actor=SYSTEM,
        work_unit_id=unit_id,
        observation_kind="github_check",
        condition_type="check_result_flip",
        key_facts={"check_name": "Quality"},
        stored_state={"conclusion": "success"},
        observed_state={"conclusion": "failure"},
        detail="Quality flipped after verification read it",
    )


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_a_duplicate_condition_ingest_records_one_row(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-condition")
    migrated_session.commit()

    first = record_reconciliation_condition(migrated_session, _flip(unit.id))
    second = record_reconciliation_condition(migrated_session, _flip(unit.id))

    assert isinstance(first, ConditionOutcome) and first.suppressed is False
    assert isinstance(second, ConditionOutcome) and second.suppressed is True
    assert second.condition.id == first.condition.id
    assert _count(migrated_session, ReconciliationCondition) == 1


def test_a_duplicate_resolution_replays(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-resolution")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, _flip(unit.id))
    assert isinstance(outcome, ConditionOutcome)
    command = ResolutionCommand(
        actor=HUMAN,
        condition_id=outcome.condition.id,
        decision="corrected",
        rationale="re-ran the check",
        idempotency_key="idem-resolve",
    )

    first = record_resolution(migrated_session, command)
    replay = record_resolution(migrated_session, command)

    assert isinstance(first, ReconciliationResolution)
    assert isinstance(replay, ReconciliationResolution)
    assert replay.id == first.id
    assert _count(migrated_session, ReconciliationResolution) == 1


def test_a_duplicate_detect_pass_records_no_second_condition(
    migrated_session: Session, deployed_binding
) -> None:
    first = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)
    second = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert first == DetectionCounters(conditions_recorded=1)
    # A duplicate is SUPPRESSED and COUNTED -- observable, not silent.
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert _count(migrated_session, ReconciliationCondition) == 1


def test_a_duplicate_recovery_replays_and_never_forks_the_chain(
    migrated_session: Session, ready_unit
) -> None:
    grant = expired_claim(migrated_session, ready_unit)

    first = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "idem-recover")
    )
    replay = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "idem-recover")
    )

    assert isinstance(first, Evidence)
    assert isinstance(replay, Evidence)
    assert replay.id == first.id
    assert _count(migrated_session, Evidence) == 1
    assert len(heads(migrated_session, ready_unit)) == 1  # still exactly one head


def test_a_duplicate_requeue_replays(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-requeue")
    authorize_readiness(migrated_session, unit)
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()

    first = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="host died", idempotency_key="idem-requeue"
    )
    replay = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="host died", idempotency_key="idem-requeue"
    )

    assert isinstance(first, WorkUnit)
    assert isinstance(replay, WorkUnit)
    assert replay.version == first.version  # the replay did not transition a second time
    assert worker() is not None
