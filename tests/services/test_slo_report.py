import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Adjudication, Claim, Event, Evidence, WorkUnit
from orchestrator.services.budget import BREACH_ACTION
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.slo_report import (
    STATUS_COMPUTED,
    STATUS_NO_DATA,
    STATUS_PARTIAL,
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
):
    adj = Adjudication(
        work_package_revision_id=revision_id,
        work_unit_id=unit_id,
        ac_id=ac_id,
        outcome=outcome,
        decided_by="verifier-1",
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
    from orchestrator.services.lifecycle import required_ac_ids

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
