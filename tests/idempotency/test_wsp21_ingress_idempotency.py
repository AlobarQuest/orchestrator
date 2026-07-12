"""AC-007: the WS-P2.1 ingress, idempotent from birth."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Evidence,
    ReconciliationCondition,
    ReconciliationResolution,
    UnitPrBinding,
    WorkUnit,
)
from orchestrator.services.claims import requeue_unit
from orchestrator.services.evidence import recover_evidence
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import get_pr_binding, upsert_pr_binding
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


def test_a_duplicate_pr_binding_report_replays(migrated_session: Session) -> None:
    """A worker re-reporting the same PR head -- a retried webhook, a re-run step -- must leave
    one row saying one thing, not two rows or a moved expectation.

    The binding is an UPSERT keyed by work_unit_id and taken FOR UPDATE, so the duplicate is
    absorbed by the row lock rather than by an idempotency key: there is exactly one row per unit
    by construction, and re-reporting the same head is a no-op. What must never happen is the
    duplicate ARMING a head or moving one already armed -- reporting is not submitting.
    """
    unit = register_unit(migrated_session, "idem-pr-binding")
    head = "c" * 40

    first = upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=9, head_sha=head
    )
    replay = upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=9, head_sha=head
    )
    migrated_session.commit()

    assert (replay.pr_number, replay.head_sha) == (first.pr_number, first.head_sha)
    assert replay.verification_read_head_sha is None, "reporting a head must never arm it"
    bindings = migrated_session.scalar(
        select(func.count()).select_from(UnitPrBinding).where(UnitPrBinding.work_unit_id == unit.id)
    )
    assert bindings == 1
    assert get_pr_binding(migrated_session, unit.id) is not None
