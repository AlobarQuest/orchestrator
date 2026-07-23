"""On-demand, time-windowed SLO report over the event store (WS-P2.2).

A read-only projection: every metric carries an explicit status so a gap is stated, never
zero-filled. Timing derives from event/claim/adjudication timestamps -- never from
``work_units.updated_at`` (a trigger rewrites it on every mutation).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock

STATUS_COMPUTED = "computed"
STATUS_NO_DATA = "no_data"
STATUS_NOT_INSTRUMENTED = "not_instrumented"
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
    )


_NO_DATA_STUB = "not yet implemented"


def _intake_to_first_work(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _queue_age(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _claim_expiry_rate(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _waiver_frequency(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _revert_rate(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _evidence_completeness(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _improvisation(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _cost(session, since, until, now) -> MetricValue:
    return MetricValue(
        STATUS_NOT_INSTRUMENTED,
        None,
        "no per-unit cost actual is recorded anywhere in the store; only the declared "
        "max_llm_calls ceiling exists. Requires the actuals-capture increment "
        "(WS-P2.4 prerequisite).",
    )


def _tokens(session, since, until, now) -> MetricValue:
    return MetricValue(
        STATUS_NOT_INSTRUMENTED,
        None,
        "no token-consumption actual is recorded anywhere in the store; see cost_per_unit.",
    )
