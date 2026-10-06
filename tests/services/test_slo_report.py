import inspect
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Claim,
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.intake import decomposition as decomposition_module
from orchestrator.services.intake.decomposition import (
    approve_decomposition_proposal,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.package_intake import register_package_intake
from orchestrator.services.intake.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)
from orchestrator.services.lifecycle.budget import BREACH_ACTION
from orchestrator.services.lifecycle.claims import authorize_retry
from orchestrator.services.lifecycle.lifecycle import TransitionCommand, transition_unit
from orchestrator.services.reporting.slo_report import (
    _DECOMPOSITION_DECISIONS,
    STATUS_COMPUTED,
    STATUS_NO_DATA,
    STATUS_PARTIAL,
    SloReportFilters,
    slo_report,
)
from orchestrator.services.verifier.evidence import record_adjudication
from tests._support.seeding import register_unit
from tests.services.test_decomposition import package_ac_ids, proposal_command, worker_actor
from tests.services.test_package_intake import (
    acceptance_criterion,
    human_actor,
    intake_command,
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


def _add_event(
    session,
    unit_id,
    *,
    action,
    to_state,
    occurred_at,
    from_state=None,
    improvisation=False,
    actor_id="system",
    actor_role="system",
    reason=None,
):
    event = Event(
        occurred_at=occurred_at,
        actor_id=actor_id,
        action=action,
        subject_type="work_unit",
        subject_id=unit_id,
        from_state=from_state,
        to_state=to_state,
        payload={"actor_role": actor_role, "reason": reason},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"evt-{uuid.uuid4()}",
        improvisation=improvisation,
    )
    session.add(event)
    session.flush()
    return event


def _add_claim(
    session, unit_id, *, attempt, acquired_at, terminal_reason=None, lease_expires_at=None
):
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


def _seed_evidence(session, unit, *, ac_id, key):
    evidence_id = uuid.uuid4()
    session.add(
        Evidence(
            id=evidence_id,
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id=ac_id,
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://x",
            payload=None,
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key=key,
        )
    )
    session.flush()
    return evidence_id


def _add_adjudication(
    session,
    revision_id,
    unit_id,
    *,
    ac_id,
    outcome,
    decided_at,
    failed_evidence_id=None,
    event_id=None,
    decided_by_role: str | None = "verifier",
):
    adj = Adjudication(
        work_package_revision_id=revision_id,
        work_unit_id=unit_id,
        ac_id=ac_id,
        outcome=outcome,
        decided_by="verifier-1",
        decided_by_role=decided_by_role,
        decided_at=decided_at,
        rationale="r",
        event_id=event_id or uuid.uuid4(),
        # waived requires failed_evidence_id + non-empty rationale/risk/follow_up (CHECK)
        failed_evidence_id=failed_evidence_id,
        risk="low" if outcome == "waived" else None,
        follow_up="none" if outcome == "waived" else None,
    )
    session.add(adj)
    session.flush()
    return adj


def _add_cost_event(
    session,
    unit_id,
    *,
    occurred_at,
    cost_known=True,
    llm_calls=10,
    input_tokens=1000,
    output_tokens=200,
    cost_usd=1.5,
):
    event = Event(
        occurred_at=occurred_at,
        actor_id="worker",
        action="attempt.cost_recorded",
        subject_type="work_unit",
        subject_id=unit_id,
        from_state=None,
        to_state=None,
        payload={
            "attempt": 1,
            "cost_known": cost_known,
            "llm_calls": llm_calls if cost_known else None,
            "num_turns": 3 if cost_known else None,
            "input_tokens": input_tokens if cost_known else None,
            "output_tokens": output_tokens if cost_known else None,
            "cost_usd": cost_usd if cost_known else None,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"cost-{uuid.uuid4()}",
    )
    session.add(event)
    session.flush()
    return event


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
        report.gate_load,
    ):
        assert metric.status == STATUS_NO_DATA
        assert metric.value is None


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


# ---- claim_expiry_rate / waiver_frequency (this task's deliverable) -------


def test_claim_expiry_rate_counts_lease_expired_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "expiry")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    outside = datetime(2026, 6, 1, tzinfo=UTC)
    _add_claim(
        migrated_session, unit.id, attempt=1, acquired_at=inside, terminal_reason="lease_expired"
    )
    _add_claim(migrated_session, unit.id, attempt=2, acquired_at=inside, terminal_reason=None)
    _add_claim(migrated_session, unit.id, attempt=3, acquired_at=inside, terminal_reason="released")
    _add_claim(
        migrated_session, unit.id, attempt=4, acquired_at=outside, terminal_reason="lease_expired"
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    # in-window claims: attempts 1,2,3 = 3 total; lease_expired = 1 -> 1/3
    assert report.claim_expiry_rate.status == STATUS_COMPUTED
    assert report.claim_expiry_rate.value is not None
    assert report.claim_expiry_rate.value == 1 / 3


def test_claim_expiry_rate_no_claims_is_no_data(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.claim_expiry_rate.status == STATUS_NO_DATA


def test_waiver_frequency_counts_waived_over_adjudications(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    revision, unit = _build_unit(migrated_session, "waiver")
    inside = datetime(2026, 7, 4, tzinfo=UTC)
    _add_adjudication(
        migrated_session, revision.id, unit.id, ac_id="ac-1", outcome="passed", decided_at=inside
    )
    failed_evidence_id = _seed_evidence(migrated_session, unit, ac_id="ac-2", key="waiver-failed-1")
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-2",
        outcome="waived",
        decided_at=inside,
        failed_evidence_id=failed_evidence_id,
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    # 2 adjudications in window, 1 waived -> 0.5
    assert report.waiver_frequency.status == STATUS_COMPUTED
    assert report.waiver_frequency.value is not None
    assert report.waiver_frequency.value == 0.5


# ---- intake_to_first_work / queue_age (this task's deliverable) -----------


def test_intake_to_first_work_median_latency_seconds(migrated_session):
    revision, unit = _build_unit(migrated_session, "intake")
    # registered_at is server-set at register/flush time. Read the real value and bracket the
    # window around it, rather than fighting the append-only trigger to overwrite it.
    reg_at = revision.registered_at
    since = reg_at - timedelta(seconds=1)
    until = reg_at + timedelta(days=1)
    # first claim 120s after registration, a later one at 300s
    _add_claim(migrated_session, unit.id, attempt=1, acquired_at=reg_at + timedelta(seconds=120))
    _add_claim(migrated_session, unit.id, attempt=2, acquired_at=reg_at + timedelta(seconds=300))
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.intake_to_first_work.status == STATUS_COMPUTED
    assert report.intake_to_first_work.value is not None
    assert report.intake_to_first_work.value == 120.0  # MIN(acquired_at) - registered_at


def test_queue_age_median_of_ready_units(migrated_session):
    from orchestrator.kernel.states import WorkUnitState

    _, unit = _build_unit(migrated_session, "queue")
    # force the unit into ready and record the ready-entry event
    unit_row = migrated_session.get(WorkUnit, unit.id)
    unit_row.state = WorkUnitState.READY.value
    ready_at = datetime(2026, 7, 5, tzinfo=UTC)
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="ready",
        from_state="draft",
        occurred_at=ready_at,
    )
    migrated_session.commit()
    now = TransactionClock().now(migrated_session)
    report = slo_report(
        migrated_session,
        SloReportFilters(since=datetime(2026, 7, 1, tzinfo=UTC), until=now),
    )
    assert report.queue_age.status == STATUS_COMPUTED
    expected = (now - ready_at).total_seconds()
    assert report.queue_age.value is not None
    assert abs(report.queue_age.value - expected) < 5  # within a few seconds


# ---- revert_rate / evidence_completeness (this task's deliverable) --------


def test_revert_rate_is_partial_with_release_revert_blind_spot(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "revert")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    # two submits, one revert (revision_required from submitted)
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="submitted",
        from_state="executing",
        occurred_at=inside,
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="submitted",
        from_state="executing",
        occurred_at=inside + timedelta(hours=1),
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="revision_required",
        from_state="submitted",
        occurred_at=inside + timedelta(hours=2),
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.revert_rate.status == STATUS_PARTIAL
    assert report.revert_rate.value == 0.5  # 1 revert / 2 submits
    assert "release-revert" in report.revert_rate.basis


def test_retiring_a_unit_that_never_submitted_is_not_a_revert(migrated_session):
    """A system-minted review unit (WS-P2.8) is BORN in `awaiting_review` and never submits, so
    retiring one used to add a numerator event with no denominator event that could answer for it:
    one submit plus two retired review units reported a revert rate of 2.0."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    _, submitter = _build_unit(migrated_session, "revert-submitter")
    _add_event(
        migrated_session,
        submitter.id,
        action="work_unit.transitioned",
        to_state="submitted",
        from_state="executing",
        occurred_at=inside,
    )
    for index in range(2):
        _, review = _build_unit(migrated_session, f"revert-review-{index}")
        _add_event(
            migrated_session,
            review.id,
            action="work_unit.transitioned",
            to_state="revision_required",
            from_state="awaiting_review",
            occurred_at=inside + timedelta(hours=1),
        )
    migrated_session.commit()

    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))

    assert report.revert_rate.value == 0.0


def test_a_revert_out_of_awaiting_review_still_counts_when_the_unit_submitted(migrated_session):
    """The exclusion must be "never submitted", not "left awaiting_review" -- the ordinary route
    to `awaiting_review` is `submitted -> verifying -> awaiting_review`, and a revert from there is
    the metric's own subject."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "revert-after-review")
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="submitted",
        from_state="executing",
        occurred_at=inside,
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="revision_required",
        from_state="awaiting_review",
        occurred_at=inside + timedelta(hours=2),
    )
    migrated_session.commit()

    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))

    assert report.revert_rate.value == 1.0


def test_evidence_completeness_ratio(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    revision, unit = _build_unit(
        migrated_session, "complete", enforcement={"acceptance_criteria": ["ac-1", "ac-2"]}
    )
    from orchestrator.services.lifecycle.lifecycle import required_ac_ids

    required = required_ac_ids(migrated_session, revision, migrated_session.get(WorkUnit, unit.id))
    assert required is not None
    assert set(required) == {"ac-1", "ac-2"}
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    # a transition in-window makes the unit "active in window"
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="executing",
        from_state="claimed",
        occurred_at=inside,
    )
    # satisfy ac-1 only (passed); ac-2 unsatisfied
    _add_adjudication(
        migrated_session, revision.id, unit.id, ac_id="ac-1", outcome="passed", decided_at=inside
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.evidence_completeness.status == STATUS_COMPUTED
    assert report.evidence_completeness.value == 0.5  # 1 of 2 required satisfied


# ---- improvisation (this task's deliverable) -------------------------------


def test_improvisation_counts_flagged_events_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "improv")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="cancelled",
        from_state="executing",
        occurred_at=inside,
        improvisation=True,
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="executing",
        from_state="claimed",
        occurred_at=inside,
        improvisation=False,
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="cancelled",
        from_state="executing",
        occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
        improvisation=True,
    )  # out of window
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.improvisation.status == STATUS_COMPUTED
    assert report.improvisation.value == 1.0  # one flagged, in-window


def test_improvisation_zero_overrides_but_active_is_computed_zero(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "improv-zero")
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="executing",
        from_state="claimed",
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
        improvisation=False,
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.improvisation.status == STATUS_COMPUTED
    assert report.improvisation.value == 0.0


def test_improvisation_no_activity_is_no_data(migrated_session):
    report = slo_report(
        migrated_session,
        SloReportFilters(
            since=datetime(2026, 7, 1, tzinfo=UTC), until=datetime(2026, 7, 8, tzinfo=UTC)
        ),
    )
    assert report.improvisation.status == STATUS_NO_DATA


# ---- cost_per_unit / token_consumption (WS-P2.4 deliverable) --------------


def test_cost_and_tokens_no_data_when_no_cost_events(migrated_session):
    report = slo_report(migrated_session)
    assert report.cost_per_unit.status == STATUS_NO_DATA
    assert report.token_consumption.status == STATUS_NO_DATA


def test_cost_and_tokens_computed_from_events(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "cost")
    _add_cost_event(
        migrated_session,
        unit.id,
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
        cost_usd=2.0,
        input_tokens=1000,
        output_tokens=200,
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.cost_per_unit.status == STATUS_COMPUTED
    assert report.cost_per_unit.value == 2.0
    assert report.token_consumption.status == STATUS_COMPUTED
    assert report.token_consumption.value == 1200.0


def test_cost_partial_when_some_unknown(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "cost")
    _add_cost_event(migrated_session, unit.id, occurred_at=datetime(2026, 7, 3, tzinfo=UTC))
    _add_cost_event(
        migrated_session,
        unit.id,
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        cost_known=False,
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.cost_per_unit.status == STATUS_PARTIAL
    assert report.cost_per_unit.value == 1.5  # _add_cost_event default cost_usd, known event only


def test_cost_and_tokens_all_unknown_is_no_data(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "cost-all-unknown")
    _add_cost_event(
        migrated_session,
        unit.id,
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
        cost_known=False,
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.cost_per_unit.status == STATUS_NO_DATA
    assert report.cost_per_unit.value is None
    assert report.token_consumption.status == STATUS_NO_DATA
    assert report.token_consumption.value is None


# ---- budget_breach (WS-P2.4 Increment 2 deliverable) -----------------------


def test_budget_breach_counts_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "breach")
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="failed",
        from_state="ready",
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
        reason="budget_exceeded",
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="failed",
        from_state="executing",
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        reason="work_unit_failed",
    )  # not a breach
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.budget_breach.status == STATUS_COMPUTED
    assert report.budget_breach.value == 1.0


def test_budget_breach_no_data_when_none(migrated_session):
    report = slo_report(migrated_session)
    assert report.budget_breach.status == STATUS_NO_DATA


def test_budget_breach_counts_an_overrun_the_halt_path_cannot_produce(migrated_session):
    """The case that made this metric report a clean window while a unit had overrun.

    A unit is asked whether it is over budget only when it is about to be granted another
    attempt, so an attempt that blows its ceiling and then COMPLETES never transitions to
    failed and never carried a `budget_exceeded` reason. Note the unit here has no halt
    transition at all -- that absence is the point of the test.
    """
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "overran-and-finished")
    _add_event(
        migrated_session,
        unit.id,
        action=BREACH_ACTION,
        to_state=None,
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
    )
    migrated_session.commit()

    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))

    assert report.budget_breach.status == STATUS_COMPUTED
    assert report.budget_breach.value == 1.0


def test_budget_breach_counts_a_unit_once_when_both_signals_exist(migrated_session):
    """A unit that overran and was LATER refused a re-claim emits both signals. They are
    unioned on the unit, not summed, or one unit would read as two."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "overran-then-halted")
    _add_event(
        migrated_session,
        unit.id,
        action=BREACH_ACTION,
        to_state=None,
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        to_state="failed",
        from_state="ready",
        occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
        reason="budget_exceeded",
    )
    migrated_session.commit()

    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))

    assert report.budget_breach.value == 1.0


def test_budget_breach_ignores_a_breach_outside_the_window(migrated_session):
    """The control for the two tests above: the new signal must still be window-scoped."""
    _, unit = _build_unit(migrated_session, "breach-out-of-window")
    _add_event(
        migrated_session,
        unit.id,
        action=BREACH_ACTION,
        to_state=None,
        occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    migrated_session.commit()

    report = slo_report(
        migrated_session,
        SloReportFilters(
            since=datetime(2026, 7, 1, tzinfo=UTC), until=datetime(2026, 7, 8, tzinfo=UTC)
        ),
    )

    assert report.budget_breach.status == STATUS_NO_DATA


# ---- gate load ---------------------------------------------------------------


def _add_approval(session, unit_id, *, subject_type, created_at):
    approval = Approval(
        subject_type=subject_type,
        subject_id=unit_id,
        subject_revision_or_fingerprint="1",
        decision="approved",
        approved_by="human-1",
        reason="r",
        created_at=created_at,
        event_id=uuid.uuid4(),
        idempotency_key=f"approval-{uuid.uuid4()}",
    )
    session.add(approval)
    session.flush()
    return approval


def _complete(session, unit_id, *, occurred_at, from_state="verifying", actor_role="verifier"):
    return _add_event(
        session,
        unit_id,
        action="work_unit.transitioned",
        from_state=from_state,
        to_state="completed",
        occurred_at=occurred_at,
        actor_role=actor_role,
    )


def _add_decomposition_event(session, *, action, at):
    session.add(
        Event(
            occurred_at=at,
            actor_id="human-1",
            action=action,
            subject_type="decomposition_proposal",
            subject_id=uuid.uuid4(),
            from_state="proposed",
            to_state="approved",
            payload={},
            correlation_id=uuid.uuid4(),
            idempotency_key=f"decomposition-{uuid.uuid4()}",
        )
    )
    session.flush()


def _gate_load_window(session, since, until):
    return slo_report(session, SloReportFilters(since=since, until=until)).gate_load


_GL_SINCE = datetime(2026, 7, 1, tzinfo=UTC)
_GL_UNTIL = datetime(2026, 7, 8, tzinfo=UTC)
_GL_INSIDE = datetime(2026, 7, 3, tzinfo=UTC)
_GL_BEFORE = datetime(2026, 6, 30, tzinfo=UTC)


def test_gate_load_counts_each_kind_of_human_decision_per_completed_unit(migrated_session):
    revision, first = _build_unit(migrated_session, "gl-a")
    _, second = _build_unit(migrated_session, "gl-b")
    _add_approval(migrated_session, first.id, subject_type="action", created_at=_GL_INSIDE)
    _add_approval(migrated_session, first.id, subject_type="authority", created_at=_GL_INSIDE)
    _add_approval(migrated_session, first.id, subject_type="authority", created_at=_GL_INSIDE)
    _add_approval(migrated_session, second.id, subject_type="retry", created_at=_GL_INSIDE)
    _add_adjudication(
        migrated_session,
        revision.id,
        first.id,
        ac_id="ac-1",
        outcome="passed",
        decided_at=_GL_INSIDE,
        decided_by_role="human",
    )
    _add_event(
        migrated_session,
        first.id,
        action="work_unit.transitioned",
        from_state="awaiting_approval",
        to_state="ready",
        occurred_at=_GL_INSIDE,
        actor_role="human",
    )
    _complete(
        migrated_session,
        first.id,
        from_state="awaiting_review",
        actor_role="human",
        occurred_at=_GL_INSIDE,
    )
    _complete(migrated_session, second.id, occurred_at=_GL_INSIDE)
    _add_decomposition_event(migrated_session, action="decomposition.approved", at=_GL_INSIDE)
    # A submission is not a decision.
    _add_decomposition_event(migrated_session, action="decomposition.proposed", at=_GL_INSIDE)

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.status == STATUS_COMPUTED
    assert metric.by_kind == {
        "action_approval": 1,
        "authority_approval": 2,
        "retry_approval": 1,
        "decomposition_decision": 1,
        "human_adjudication": 1,
        "human_transition": 2,
    }
    assert metric.decisions == 8
    assert metric.completed_units == 2
    assert metric.value == 4.0


def test_gate_load_counts_no_verifier_system_or_worker_decision(migrated_session):
    revision, unit = _build_unit(migrated_session, "gl-machine")
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-1",
        outcome="passed",
        decided_at=_GL_INSIDE,
        decided_by_role="verifier",
    )
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-2",
        outcome="passed",
        decided_at=_GL_INSIDE,
        decided_by_role="system",
    )
    # A system edge, and a worker edge: neither is a human's even when the role says so, and a
    # system actor never takes a human edge.
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        from_state="failed",
        to_state="ready",
        occurred_at=_GL_INSIDE,
        actor_role="system",
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        from_state="executing",
        to_state="submitted",
        occurred_at=_GL_INSIDE,
        actor_role="human",
    )
    # The verifier and a human may both take submitted/verifying -> completed; only the role
    # tells them apart.
    _complete(migrated_session, unit.id, from_state="submitted", occurred_at=_GL_INSIDE)

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.status == STATUS_COMPUTED
    assert metric.decisions == 0
    assert set(metric.by_kind.values()) == {0}
    assert metric.completed_units == 1
    assert metric.value == 0.0


def test_gate_load_ignores_decisions_and_completions_outside_the_window(migrated_session):
    revision, unit = _build_unit(migrated_session, "gl-window")
    _, late = _build_unit(migrated_session, "gl-late")
    _add_approval(migrated_session, unit.id, subject_type="action", created_at=_GL_BEFORE)
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-1",
        outcome="passed",
        decided_at=_GL_BEFORE,
        decided_by_role="human",
    )
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        from_state="awaiting_approval",
        to_state="ready",
        occurred_at=_GL_BEFORE,
        actor_role="human",
    )
    _complete(migrated_session, late.id, occurred_at=_GL_BEFORE)
    _add_decomposition_event(migrated_session, action="decomposition.rejected", at=_GL_BEFORE)
    _add_approval(migrated_session, unit.id, subject_type="retry", created_at=_GL_INSIDE)
    _complete(migrated_session, unit.id, occurred_at=_GL_INSIDE)

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.decisions == 1
    assert metric.by_kind["retry_approval"] == 1
    assert metric.completed_units == 1


def test_gate_load_with_no_completed_unit_is_no_data_and_still_reports_the_count(
    migrated_session,
):
    _, unit = _build_unit(migrated_session, "gl-nodata")
    _add_approval(migrated_session, unit.id, subject_type="action", created_at=_GL_INSIDE)
    _add_event(
        migrated_session,
        unit.id,
        action="work_unit.transitioned",
        from_state="draft",
        to_state="ready",
        occurred_at=_GL_INSIDE,
    )

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.status == STATUS_NO_DATA
    assert metric.value is None
    assert metric.decisions == 1
    assert metric.by_kind["action_approval"] == 1
    assert metric.completed_units == 0


def test_gate_load_with_no_completed_unit_still_names_unattributed_adjudications(
    migrated_session,
):
    revision, unit = _build_unit(migrated_session, "gl-nodata-unknown")
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-1",
        outcome="passed",
        decided_at=_GL_INSIDE,
        decided_by_role=None,
    )

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.status == STATUS_NO_DATA
    assert "1 adjudications have no recorded role" in metric.basis


def test_gate_load_is_partial_when_an_adjudication_has_no_recorded_role(migrated_session):
    revision, unit = _build_unit(migrated_session, "gl-unknown")
    _add_adjudication(
        migrated_session,
        revision.id,
        unit.id,
        ac_id="ac-1",
        outcome="passed",
        decided_at=_GL_INSIDE,
        decided_by_role=None,
    )
    _complete(migrated_session, unit.id, occurred_at=_GL_INSIDE)

    metric = _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL)

    assert metric.status == STATUS_PARTIAL
    assert metric.by_kind["human_adjudication"] == 0
    assert "1 adjudications have no recorded role" in metric.basis


def test_a_unit_completed_twice_in_the_window_is_one_completed_unit(migrated_session):
    _, unit = _build_unit(migrated_session, "gl-twice")
    _complete(migrated_session, unit.id, occurred_at=_GL_INSIDE)
    _complete(migrated_session, unit.id, occurred_at=_GL_INSIDE + timedelta(hours=1))

    assert _gate_load_window(migrated_session, _GL_SINCE, _GL_UNTIL).completed_units == 1


def test_the_decomposition_decisions_are_the_actions_the_writer_records():
    source = inspect.getsource(decomposition_module)
    written = set(re.findall(r'action="(decomposition\.[a-z_]+)"', source))

    assert written == set(_DECOMPOSITION_DECISIONS)


def test_gate_load_reads_what_the_real_services_write(migrated_session):
    """Each decision is made through its service, so the metric reads the rows those services
    really write: the retry's own transition is a system one and is not counted again, and a
    human completion carries the role the metric keys on."""
    package = register_package_intake(
        migrated_session,
        intake_command(
            package_id="pkg-gate-load",
            content_hash="sha256:gate-load",
            idempotency_key="package-intake-gate-load",
            acceptance_criteria=(acceptance_criterion("AC-001"), acceptance_criterion("AC-002")),
        ),
        human_actor(),
    )
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            package.id,
            package_ac_ids(migrated_session, package.id),
            idempotency_key="proposal-gate-load",
        ),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session,
        proposal_id=proposal.id,
        actor=human_actor(),
        reason="approved",
        idempotency_key="approve-gate-load",
    )
    migrated_session.commit()

    approved = register_unit(migrated_session, "gate-load-authority")
    record_approval(
        migrated_session,
        unit_id=approved.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="authority approved",
        idempotency_key="authority-gate-load",
        expected_version=approved.version,
    )
    migrated_session.commit()

    retried = register_unit(migrated_session, "gate-load-retry")
    retried.state = WorkUnitState.FAILED
    retried.attempt_count = retried.max_attempts
    migrated_session.commit()
    retry = authorize_retry(
        migrated_session,
        retried.id,
        ActorContext("human-1", ActorRole.HUMAN),
        new_max_attempts=retried.max_attempts + 1,
        reason="the runner environment was at fault",
        idempotency_key="retry-gate-load",
    )
    assert isinstance(retry, Approval)

    reviewed = register_unit(migrated_session, "gate-load-review")
    reviewed.state = WorkUnitState.SUBMITTED
    migrated_session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=reviewed.work_package_revision_id,
            ac_id="ac-1",
            condition="condition",
            evidence_type="human.review",
            evidence="evidence",
            approver="human-1",
        )
    )
    migrated_session.commit()
    adjudication = record_adjudication(
        migrated_session,
        work_package_revision_id=reviewed.work_package_revision_id,
        work_unit_id=reviewed.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-gate-load",
    )
    assert isinstance(adjudication, Adjudication)
    migrated_session.refresh(reviewed)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=reviewed.id,
            target=WorkUnitState.COMPLETED,
            actor=ActorContext("human-1", ActorRole.HUMAN),
            expected_version=reviewed.version,
            idempotency_key="complete-gate-load",
        ),
    )
    migrated_session.commit()

    metric = slo_report(migrated_session).gate_load

    assert metric.by_kind == {
        "action_approval": 0,
        "authority_approval": 1,
        "retry_approval": 1,
        "decomposition_decision": 1,
        "human_adjudication": 1,
        "human_transition": 1,
    }
    assert metric.completed_units == 1
    assert metric.value == 5.0
