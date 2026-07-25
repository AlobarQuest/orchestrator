"""On-demand, time-windowed SLO report over the event store (WS-P2.2).

A read-only projection: every metric carries an explicit status so a gap is stated, never
zero-filled. Timing derives from event/claim/adjudication timestamps -- never from
``work_units.updated_at`` (a trigger rewrites it on every mutation).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.persistence.models import (
    Adjudication,
    Claim,
    Event,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.consistency import SATISFIED_ACS
from orchestrator.services.lifecycle import required_ac_ids

STATUS_COMPUTED = "computed"
STATUS_NO_DATA = "no_data"
STATUS_PARTIAL = "partial"

DEFAULT_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class SloReportFilters:
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class MetricValue:
    status: str
    value: float | None
    basis: str


@dataclass(frozen=True)
class SloReport:
    since: datetime
    until: datetime
    intake_to_first_work: MetricValue
    queue_age: MetricValue
    claim_expiry_rate: MetricValue
    waiver_frequency: MetricValue
    revert_rate: MetricValue
    evidence_completeness: MetricValue
    cost_per_unit: MetricValue
    token_consumption: MetricValue
    improvisation: MetricValue
    budget_breach: MetricValue


def slo_report(session: Session, filters: SloReportFilters | None = None) -> SloReport:
    criteria = filters or SloReportFilters()
    now = TransactionClock().now(session)
    until = criteria.until or now
    since = criteria.since or (until - DEFAULT_WINDOW)
    return SloReport(
        since=since,
        until=until,
        intake_to_first_work=_intake_to_first_work(session, since, until, now),
        queue_age=_queue_age(session, since, until, now),
        claim_expiry_rate=_claim_expiry_rate(session, since, until, now),
        waiver_frequency=_waiver_frequency(session, since, until, now),
        revert_rate=_revert_rate(session, since, until, now),
        evidence_completeness=_evidence_completeness(session, since, until, now),
        cost_per_unit=_cost(session, since, until, now),
        token_consumption=_tokens(session, since, until, now),
        improvisation=_improvisation(session, since, until, now),
        budget_breach=_budget_breach(session, since, until, now),
    )


def _intake_to_first_work(session, since, until, now) -> MetricValue:
    revisions = session.scalars(
        select(WorkPackageRevision).where(
            WorkPackageRevision.registered_at >= since,
            WorkPackageRevision.registered_at < until,
        )
    ).all()
    if not revisions:
        return MetricValue(
            STATUS_NO_DATA, None, "no package revisions were registered in the window"
        )
    latencies: list[float] = []
    pending = 0
    for revision in revisions:
        first_claim = session.scalar(
            select(func.min(Claim.acquired_at))
            .join(WorkUnit, WorkUnit.id == Claim.work_unit_id)
            .where(WorkUnit.work_package_revision_id == revision.id)
        )
        if first_claim is None:
            pending += 1
            continue
        latencies.append((first_claim - revision.registered_at).total_seconds())
    if not latencies:
        return MetricValue(
            STATUS_NO_DATA,
            None,
            f"{len(revisions)} revisions registered in window, none has a first claim yet",
        )
    return MetricValue(
        STATUS_COMPUTED,
        _median(latencies),
        f"median seconds intake->first-claim over {len(latencies)} revisions "
        f"({pending} registered-but-unclaimed excluded)",
    )


def _queue_age(session, since, until, now) -> MetricValue:
    ready_units = session.scalars(select(WorkUnit).where(WorkUnit.state == "ready")).all()
    if not ready_units:
        return MetricValue(STATUS_NO_DATA, None, "no work units are currently in the ready state")
    ages: list[float] = []
    for unit in ready_units:
        entered = session.scalar(
            select(func.max(Event.occurred_at)).where(
                Event.subject_type == "work_unit",
                Event.subject_id == unit.id,
                Event.to_state == "ready",
            )
        )
        if entered is not None:
            ages.append((now - entered).total_seconds())
    if not ages:
        return MetricValue(
            STATUS_NO_DATA,
            None,
            "ready units exist but none has a recorded ready-entry transition event",
        )
    return MetricValue(
        STATUS_COMPUTED,
        _median(ages),
        f"median seconds in ready over {len(ages)} units currently queued",
    )


def _claim_expiry_rate(session, since, until, now) -> MetricValue:
    total = (
        session.scalar(
            select(func.count(Claim.id)).where(
                Claim.acquired_at >= since, Claim.acquired_at < until
            )
        )
        or 0
    )
    if total == 0:
        return MetricValue(STATUS_NO_DATA, None, "no claims were acquired in the window")
    expired = (
        session.scalar(
            select(func.count(Claim.id)).where(
                Claim.acquired_at >= since,
                Claim.acquired_at < until,
                Claim.terminal_reason == "lease_expired",
            )
        )
        or 0
    )
    return MetricValue(
        STATUS_COMPUTED,
        expired / total,
        f"claims acquired in window: {total}; lease_expired: {expired}",
    )


def _waiver_frequency(session, since, until, now) -> MetricValue:
    total = (
        session.scalar(
            select(func.count(Adjudication.id)).where(
                Adjudication.decided_at >= since, Adjudication.decided_at < until
            )
        )
        or 0
    )
    if total == 0:
        return MetricValue(STATUS_NO_DATA, None, "no adjudications were decided in the window")
    waived = (
        session.scalar(
            select(func.count(Adjudication.id)).where(
                Adjudication.decided_at >= since,
                Adjudication.decided_at < until,
                Adjudication.outcome == "waived",
            )
        )
        or 0
    )
    return MetricValue(
        STATUS_COMPUTED,
        waived / total,
        f"adjudications in window: {total}; waived: {waived}",
    )


_REVERT_STATES = ("revision_required", "failed")
_REVERT_SOURCES = ("submitted", "verifying", "awaiting_review")


def _revert_rate(session, since, until, now) -> MetricValue:
    submits = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.to_state == "submitted",
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    if submits == 0:
        return MetricValue(STATUS_NO_DATA, None, "no submit transitions occurred in the window")
    reverts = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.to_state.in_(_REVERT_STATES),
                Event.from_state.in_(_REVERT_SOURCES),
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    return MetricValue(
        STATUS_PARTIAL,
        reverts / submits,
        f"code reverts (to revision_required/failed after submit): {reverts}; submits: {submits}. "
        "Numerator and denominator are counted independently over the window, so the ratio can "
        "exceed 1.0. PARTIAL: release-revert is not recorded as an explicit fact "
        "(divergence detection only).",
    )


def _evidence_completeness(session, since, until, now) -> MetricValue:
    active_ids = session.scalars(
        select(Event.subject_id)
        .where(
            Event.action == "work_unit.transitioned",
            Event.subject_type == "work_unit",
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
        .distinct()
    ).all()
    if not active_ids:
        return MetricValue(STATUS_NO_DATA, None, "no work units had transitions in the window")
    total_required = 0
    total_satisfied = 0
    considered = 0
    skipped = 0
    for unit_id in active_ids:
        unit = session.get(WorkUnit, unit_id)
        if unit is None:
            continue
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            continue
        required = required_ac_ids(session, revision, unit)
        if required is None or not required:
            skipped += 1
            continue
        considered += 1
        satisfied = set(session.scalars(SATISFIED_ACS, {"unit_id": unit.id, "now": now}))
        total_required += len(required)
        total_satisfied += len(set(required) & satisfied)
    if total_required == 0:
        return MetricValue(
            STATUS_NO_DATA,
            None,
            f"{len(active_ids)} active units in window, none has required acceptance criteria",
        )
    return MetricValue(
        STATUS_COMPUTED,
        total_satisfied / total_required,
        f"satisfied {total_satisfied}/{total_required} required criteria over {considered} units "
        f"({skipped} without required criteria excluded)",
    )


def _improvisation(session, since, until, now) -> MetricValue:
    total_transitions = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    if total_transitions == 0:
        return MetricValue(STATUS_NO_DATA, None, "no lifecycle transitions occurred in the window")
    overrides = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.improvisation.is_(True),
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    return MetricValue(
        STATUS_COMPUTED,
        float(overrides),
        f"human operator overrides (cancels + verifier-bypass completes): {overrides} "
        f"of {total_transitions} transitions; designed human gates excluded.",
    )


def _budget_breach(session, since, until, now) -> MetricValue:
    breaches = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.to_state == "failed",
                Event.payload["reason"].astext == "budget_exceeded",
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    if breaches == 0:
        return MetricValue(
            STATUS_NO_DATA, None, "no llm-call budget breaches occurred in the window"
        )
    return MetricValue(
        STATUS_COMPUTED,
        float(breaches),
        f"{breaches} unit(s) halted at their llm-call cap in the window",
    )


def _median(values: list[float]) -> float:
    return float(median(values))


_COST_ACTION = "attempt.cost_recorded"


def _cost_events_in_window(session, since, until) -> tuple[int, int]:
    known = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == _COST_ACTION,
                Event.occurred_at >= since,
                Event.occurred_at < until,
                Event.payload["cost_known"].astext == "true",
            )
        )
        or 0
    )
    unknown = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == _COST_ACTION,
                Event.occurred_at >= since,
                Event.occurred_at < until,
                Event.payload["cost_known"].astext == "false",
            )
        )
        or 0
    )
    return known, unknown


def _cost(session, since, until, now) -> MetricValue:
    known, unknown = _cost_events_in_window(session, since, until)
    if known == 0:
        if unknown == 0:
            return MetricValue(STATUS_NO_DATA, None, "no cost actuals were recorded in the window")
        return MetricValue(
            STATUS_NO_DATA,
            None,
            f"no known cost actuals in window ({unknown} attempts had unknown cost)",
        )
    total = (
        session.scalar(
            select(func.sum(cast(Event.payload["cost_usd"].astext, Float))).where(
                Event.action == _COST_ACTION,
                Event.occurred_at >= since,
                Event.occurred_at < until,
                Event.payload["cost_known"].astext == "true",
            )
        )
        or 0.0
    )
    status = STATUS_PARTIAL if unknown else STATUS_COMPUTED
    return MetricValue(
        status,
        float(total),
        f"summed cost_usd over {known} cost-known attempts in window"
        + (f"; {unknown} attempts had unknown cost (excluded)" if unknown else ""),
    )


def _tokens(session, since, until, now) -> MetricValue:
    known, unknown = _cost_events_in_window(session, since, until)
    if known == 0:
        if unknown == 0:
            return MetricValue(STATUS_NO_DATA, None, "no token actuals were recorded in the window")
        return MetricValue(
            STATUS_NO_DATA,
            None,
            f"no known token actuals in window ({unknown} attempts had unknown cost)",
        )
    total = (
        session.scalar(
            select(
                func.sum(
                    cast(Event.payload["input_tokens"].astext, Float)
                    + cast(Event.payload["output_tokens"].astext, Float)
                )
            ).where(
                Event.action == _COST_ACTION,
                Event.occurred_at >= since,
                Event.occurred_at < until,
                Event.payload["cost_known"].astext == "true",
            )
        )
        or 0.0
    )
    status = STATUS_PARTIAL if unknown else STATUS_COMPUTED
    return MetricValue(
        status,
        float(total),
        f"summed input+output tokens over {known} cost-known attempts in window"
        + (f"; {unknown} attempts had unknown cost (excluded)" if unknown else ""),
    )
