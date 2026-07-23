import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Claim, Event, WorkUnit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.slo_report import (
    STATUS_NO_DATA,
    STATUS_NOT_INSTRUMENTED,
    SloReportFilters,
    slo_report,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)


# ---- shared builders (reused by Tasks 4-7) ---------------------------------

def _build_unit(session, key, *, enforcement=None):
    now = TransactionClock().now(session)
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=now,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot=enforcement or {"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        unit_id=None,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=now,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    return revision, unit


def _add_event(session, unit_id, *, action, to_state, occurred_at, from_state=None,
               improvisation=False, actor_id="system", actor_role="system"):
    event = Event(
        occurred_at=occurred_at,
        actor_id=actor_id,
        action=action,
        subject_type="work_unit",
        subject_id=unit_id,
        from_state=from_state,
        to_state=to_state,
        payload={"actor_role": actor_role},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"evt-{uuid.uuid4()}",
        improvisation=improvisation,
    )
    session.add(event)
    session.flush()
    return event


def _add_claim(session, unit_id, *, attempt, acquired_at, terminal_reason=None,
               lease_expires_at=None):
    claim = Claim(
        work_unit_id=unit_id,
        attempt=attempt,
        claimed_by="worker-1",
        lease_token_hash=f"hash-{uuid.uuid4()}",
        idempotency_key=f"claim-{uuid.uuid4()}",
        acquired_at=acquired_at,
        lease_expires_at=lease_expires_at or (acquired_at + timedelta(minutes=30)),
        terminal_reason=terminal_reason,
        released_at=acquired_at if terminal_reason else None,
    )
    session.add(claim)
    session.flush()
    return claim


# ---- skeleton tests --------------------------------------------------------

def test_empty_store_reports_no_data_and_not_instrumented(migrated_session):
    report = slo_report(migrated_session)
    # window defaults to 7 days ending "now"
    assert (report.until - report.since) == timedelta(days=7)
    for metric in (
        report.intake_to_first_work,
        report.queue_age,
        report.claim_expiry_rate,
        report.waiver_frequency,
        report.revert_rate,
        report.evidence_completeness,
        report.improvisation,
    ):
        assert metric.status == STATUS_NO_DATA
        assert metric.value is None


def test_cost_and_tokens_are_not_instrumented(migrated_session):
    """Guard test: cost/tokens have no source data and must never be silently zero-filled."""
    report = slo_report(migrated_session)
    assert report.cost_per_unit.status == STATUS_NOT_INSTRUMENTED
    assert report.cost_per_unit.value is None
    assert report.token_consumption.status == STATUS_NOT_INSTRUMENTED
    assert report.token_consumption.value is None


def test_explicit_window_is_respected(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.since == since
    assert report.until == until


# ---- shared builder smoke test (this task's own deliverable) --------------

def test_shared_builders_smoke(migrated_session):
    """Prove the shared builders themselves work, since the skeleton tests above
    run on an empty store and never call them. Tasks 4-7 depend on these builders;
    this is not a metric test."""
    revision, unit = _build_unit(migrated_session, "smoke")
    migrated_session.commit()
    assert revision.id is not None
    assert unit.id is not None

    now = TransactionClock().now(migrated_session)
    event = _add_event(
        migrated_session,
        unit.id,
        action="submitted",
        to_state="ready",
        occurred_at=now,
    )
    claim = _add_claim(migrated_session, unit.id, attempt=1, acquired_at=now)
    migrated_session.commit()

    persisted_event = migrated_session.scalar(select(Event).where(Event.id == event.id))
    assert persisted_event is not None
    assert persisted_event.subject_id == unit.id

    persisted_claim = migrated_session.scalar(select(Claim).where(Claim.id == claim.id))
    assert persisted_claim is not None
    assert persisted_claim.work_unit_id == unit.id

    persisted_unit = migrated_session.scalar(select(WorkUnit).where(WorkUnit.id == unit.id))
    assert persisted_unit is not None
    assert persisted_unit.unit_key == "smoke"
