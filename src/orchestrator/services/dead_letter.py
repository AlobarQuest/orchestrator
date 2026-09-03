"""The dead-letter view: terminal failures AND stalled approval gates made visible.

Derived LIVE from the source tables. There is no materialized dead-letter queue, so there is
nothing to drift out of sync with the reality it reports. Read-only: this module performs no
write, no transition, and no commit.

WS-P2.15 widened the view's contract. It used to report only terminal failures. It now also
reports STALLED APPROVAL GATES -- a unit sitting in `awaiting_approval` or `awaiting_review`
that no human has answered past a threshold.

Two things about that, both deliberate:

  * It is a DERIVED READ, not a written record. A stalled gate is not an event that happened;
    it is a fact about the present -- a predicate over (state, updated_at). The predecessor
    design wrote a `blocked` DispatchRecord instead, which (a) could not use runner_attempt=0
    (`CheckConstraint("runner_attempt > 0")`), and (b) with any other runner_attempt silently
    consumed a dispatch slot, so a later genuine dispatch of the same unit would be
    short-circuited into returning the stale record and never dispatch. When every variant of
    a mechanism is broken, the mechanism is wrong. `_open_circuit_breakers` below is the
    precedent: also derived, also unpersisted.

  * SILENCE IS NEVER APPROVAL. This REPORTS. It transitions nothing, and it cannot: every edge
    out of an approval state requires a named HUMAN actor (asserted in
    tests/kernel/test_approval_edges_require_a_human.py). A stalled gate is therefore reported
    and NOT requeue-eligible -- which falls out of the existing `_requeue_eligible` predicate
    for free, since an approval state is not in REQUEUE_STATES.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.persistence.models import Claim, DispatchRecord, WorkUnit
from orchestrator.services.dispatch import circuit_open

DEAD_LETTER_UNIT_STATES = ("failed", "blocked", "cancelled")
DEAD_LETTER_DISPATCH_STATUSES = ("failed", "blocked")
# `blocked` is here because requeue TARGETS it. An action whose subject is invisible in the
# surface it is offered from is not an operator affordance.
# not-a-vocabulary: internal policy subset of WorkUnitState (which states requeue targets), not a
# value shared across a repo or subsystem boundary.
REQUEUE_STATES = ("failed", "blocked")
# The gates a human must answer. Nothing here can be answered by time passing.
APPROVAL_STATES = ("awaiting_approval", "awaiting_review")
# The states a VERIFIER owes an answer on. Nothing here can be answered by time passing either:
# no schedule runs the verifier, an operator drives it. Kept separate from APPROVAL_STATES rather
# than folded into it because the two reports differ in WHO OWES THE DECISION and therefore in
# the remedy -- an approval gate needs a human to decide, a stalled verification needs the
# verifier run. Telling an operator the wrong one is worse than telling them nothing.
VERIFICATION_STATES = ("submitted", "verifying")


@dataclass(frozen=True)
class DeadLetterEntry:
    source: str
    work_unit_id: uuid.UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None


def dead_letter(
    session: Session,
    *,
    failure_signature_threshold: int,
    stalled_approval_seconds: int,
    stalled_verification_seconds: int,
) -> tuple[DeadLetterEntry, ...]:
    """`stalled_approval_seconds` is a plain int on purpose. It has no "off" value.

    Its predecessor was `int | None = None`, and that None is precisely why the age-out it
    configured sat unwired and invisible for an entire workstream. A reporting obligation that
    can be switched off is a reporting obligation that will be.
    """
    return (
        *_terminal_units(session),
        *_failed_dispatch_records(session),
        *_open_circuit_breakers(session, failure_signature_threshold),
        *_stalled_approvals(session, stalled_approval_seconds),
        *_stalled_verifications(session, stalled_verification_seconds),
    )


def _stalled_verifications(
    session: Session, stalled_verification_seconds: int
) -> tuple[DeadLetterEntry, ...]:
    """A unit that submitted and was never verified. Reported, never resolved.

    Added 2026-09-03 after a unit was found parked in `submitted` for fifteen days with a pull
    request whose checks had failed the whole time, reported by nothing. `_stalled_approvals`
    above does not cover it -- WS-P2.15 widened this view to the gates a HUMAN owes, and a
    verifier-owed state was never in that scope. Neither does the reconciliation detect-pass,
    which keys on SUBMITTED but joins the observation's post-deploy unit, so it sees only the
    units minted by a release and never an ordinary implementation unit.

    A unit ALSO reported there is reported here too, deliberately: that surface records a
    divergence for the machine, this one is the operator's single view of what needs attention,
    and narrowing either to avoid the overlap is how a hole gets moved rather than closed.
    """
    cutoff = TransactionClock().now(session) - timedelta(seconds=stalled_verification_seconds)
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.in_(VERIFICATION_STATES), WorkUnit.updated_at <= cutoff)
        .order_by(WorkUnit.updated_at, WorkUnit.id)
    ).all()
    return tuple(
        DeadLetterEntry(
            source="stalled_verification",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code="verification_undecided",
            detail=f"awaiting a verifier decision since {unit.updated_at.isoformat()}",
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            # False, from the existing predicate: neither state is a REQUEUE_STATE. A stalled
            # verification needs the verifier run, not another attempt.
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=unit.updated_at,
        )
        for unit in units
    )


def _stalled_approvals(
    session: Session, stalled_approval_seconds: int
) -> tuple[DeadLetterEntry, ...]:
    """A human gate nobody answered. Reported, never resolved -- silence is not approval."""
    cutoff = TransactionClock().now(session) - timedelta(seconds=stalled_approval_seconds)
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.in_(APPROVAL_STATES), WorkUnit.updated_at <= cutoff)
        .order_by(WorkUnit.updated_at, WorkUnit.id)
    ).all()
    return tuple(
        DeadLetterEntry(
            source="stalled_approval",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code="approval_unanswered",
            detail=f"awaiting a human decision since {unit.updated_at.isoformat()}",
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            # False, from the existing predicate: an approval state is not a REQUEUE_STATE.
            # A stalled gate needs a human decision, not a retry.
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=unit.updated_at,
        )
        for unit in units
    )


def _terminal_units(session: Session) -> tuple[DeadLetterEntry, ...]:
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.in_(DEAD_LETTER_UNIT_STATES))
        .order_by(WorkUnit.unit_key, WorkUnit.id)
    ).all()
    return tuple(_unit_entry(session, unit) for unit in units)


def _unit_entry(session: Session, unit: WorkUnit) -> DeadLetterEntry:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc(), Claim.id.desc())
        .limit(1)
    )
    return DeadLetterEntry(
        source="work_unit",
        work_unit_id=unit.id,
        unit_key=unit.unit_key,
        unit_state=unit.state,
        reason_code=claim.terminal_reason if claim is not None else None,
        detail=str(claim.id) if claim is not None else None,
        attempt_count=unit.attempt_count,
        max_attempts=unit.max_attempts,
        requeue_eligible=_requeue_eligible(unit),
        occurred_at=claim.released_at if claim is not None else None,
    )


def _failed_dispatch_records(session: Session) -> tuple[DeadLetterEntry, ...]:
    rows = session.execute(
        select(DispatchRecord, WorkUnit)
        .join(WorkUnit, WorkUnit.id == DispatchRecord.work_unit_id)
        .where(DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES))
        .order_by(WorkUnit.unit_key, DispatchRecord.runner_attempt, DispatchRecord.id)
    ).all()
    return tuple(
        DeadLetterEntry(
            source="dispatch_record",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code=record.reason_code,
            detail=record.failure_signature,
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=None,
        )
        for record, unit in rows
    )


def _open_circuit_breakers(
    session: Session,
    failure_signature_threshold: int,
) -> tuple[DeadLetterEntry, ...]:
    """Derived -- there is no persisted breaker entity; "open" is a predicate over the records.

    `circuit_open` is called with the AT-REST count, never `count + 1`. Dispatch's own call site
    is PROSPECTIVE: it counts the failure it is about to write. Reusing that call here would
    report a breaker open one failure early -- flagging a unit as circuit-broken while it still
    has a dispatch left. One shared predicate, two tenses; the `+ 1` lives only at dispatch.
    """
    grouped = session.execute(
        select(
            DispatchRecord.work_unit_id,
            DispatchRecord.failure_signature,
            func.count().label("failures"),
        )
        .where(
            DispatchRecord.failure_signature.is_not(None),
            DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES),
        )
        .group_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
        .order_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
    ).all()
    entries: list[DeadLetterEntry] = []
    for unit_id, signature, failures in grouped:
        if not circuit_open(failures, failure_signature_threshold):
            continue
        unit = session.get(WorkUnit, unit_id)
        if unit is None:
            continue
        entries.append(
            DeadLetterEntry(
                source="circuit_breaker",
                work_unit_id=unit.id,
                unit_key=unit.unit_key,
                unit_state=unit.state,
                reason_code="failure_signature_circuit_open",
                detail=signature,
                attempt_count=unit.attempt_count,
                max_attempts=unit.max_attempts,
                requeue_eligible=_requeue_eligible(unit),
                occurred_at=None,
            )
        )
    return tuple(entries)


def _requeue_eligible(unit: WorkUnit) -> bool:
    """An exhausted unit is NOT requeue-eligible.

    Requeue would land it READY, where `claim_unit` rejects it with `attempts_exhausted` -- and
    it would drop out of this view at the same time: invisible AND unrunnable. `retry` (which
    raises the budget) is the operator's path for that case.
    """
    return unit.state in REQUEUE_STATES and unit.attempt_count < unit.max_attempts
