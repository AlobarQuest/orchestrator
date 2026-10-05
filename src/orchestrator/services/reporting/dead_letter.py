"""The dead-letter view: unresolved failures AND stalled approval gates made visible.

The view lists only what still needs a decision. A unit that reached `completed` or `cancelled`
is resolved, and that resolution is the acknowledgement: neither the unit nor the dispatch
failures and breakers it left behind appear here. There is no separate acknowledged state.

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
    and NOT requeue-eligible -- which falls out of `requeue_refusal` for free, since requeue
    takes only failed and blocked units.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.kernel.readiness import ReadinessStatus
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Claim, DispatchRecord, WorkUnit
from orchestrator.services.execution.dispatch import circuit_open
from orchestrator.services.lifecycle.budget import is_over_budget
from orchestrator.services.lifecycle.claims import requeue_refusal
from orchestrator.services.lifecycle.readiness import evaluate_readiness

# `blocked` units are listed because requeue TARGETS them. An action whose subject is invisible in
# the surface it is offered from is not an operator affordance.
DEAD_LETTER_UNIT_STATES = ("failed", "blocked")
DEAD_LETTER_DISPATCH_STATUSES = ("failed", "blocked")
# A resolved unit has reached the end of its story, and resolving it is the acknowledgement: its old
# dispatch failures and breakers no longer ask anyone for a decision, so the view leaves them out.
# not-a-vocabulary: internal policy subset of WorkUnitState (which states end a unit's story), not
# a value shared across a repo or subsystem boundary.
RESOLVED_UNIT_STATES = ("completed", "cancelled")
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
    recovery_action: str


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
            # False, from requeue's own predicate: it takes only failed and blocked units. A
            # stalled verification needs the verifier run, not another attempt.
            requeue_eligible=requeue_refusal(session, unit) is None,
            recovery_action=recovery_action(session, unit),
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
            # False, from requeue's own predicate: it takes only failed and blocked units.
            # A stalled gate needs a human decision, not a retry.
            requeue_eligible=requeue_refusal(session, unit) is None,
            recovery_action=recovery_action(session, unit),
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
        requeue_eligible=requeue_refusal(session, unit) is None,
        recovery_action=recovery_action(session, unit),
        occurred_at=claim.released_at if claim is not None else None,
    )


def _failed_dispatch_records(session: Session) -> tuple[DeadLetterEntry, ...]:
    rows = session.execute(
        select(DispatchRecord, WorkUnit)
        .join(WorkUnit, WorkUnit.id == DispatchRecord.work_unit_id)
        .where(
            DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES),
            WorkUnit.state.not_in(RESOLVED_UNIT_STATES),
        )
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
            requeue_eligible=requeue_refusal(session, unit) is None,
            recovery_action=recovery_action(session, unit),
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
        if unit is None or unit.state in RESOLVED_UNIT_STATES:
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
                requeue_eligible=requeue_refusal(session, unit) is None,
                recovery_action=recovery_action(session, unit),
                occurred_at=None,
            )
        )
    return tuple(entries)


# The states whose way forward belongs to one owner and depends on nothing else about the unit.
_OWNER_ACTIONS = {
    WorkUnitState.READY: (
        "Have the system dispatch this unit again once the failure's cause is fixed"
    ),
    WorkUnitState.DRAFT: "None yet: the orchestrator readies this unit once its readiness holds",
    # Every command that reaches `ready` from here is a HUMAN command on `/api`, which production
    # routes to no person; the page's approval form records an approval but takes no edge.
    WorkUnitState.AWAITING_APPROVAL: (
        "Cancel this unit on its review page; recording an approval there does not ready it"
    ),
    WorkUnitState.AWAITING_REVIEW: "Complete this unit or request a revision on its review page",
    WorkUnitState.SUBMITTED: "Have the verifier evaluate this unit",
    WorkUnitState.VERIFYING: "Have the verifier evaluate this unit",
}


def recovery_action(session: Session, unit: WorkUnit) -> str:
    """The action that moves this unit on, and who may take it (review 7b).

    It follows the unit, not the entry: a dispatch failure or a breaker on a unit is recovered the
    way the unit is.
    """
    state = WorkUnitState(unit.state)
    if state in (WorkUnitState.FAILED, WorkUnitState.BLOCKED):
        return _requeue_target_action(session, unit, state)
    return _OWNER_ACTIONS.get(
        state, f"None: the unit is {unit.state} and has moved past this failure"
    )


# Who restores a readiness term: an authority approval is recorded on the review page, and a
# dependency is resolved through an `/api` route that production serves only to machines.
_READINESS_OWNERS = "(an approval on its review page, or the system resolving a dependency)"


def _requeue_target_action(session: Session, unit: WorkUnit, state: WorkUnitState) -> str:
    """Built from the three facts that decide whether the unit can run again, each read on its
    own: requeue reports only the first it fails, which would name the wrong blocker.

    A spent LLM-call budget is final, since nothing raises it. A spent attempt budget is raised
    only by `authorize_retry`, which takes failed and blocked units. Readiness that no longer
    holds is recoverable, so it never reads as a dead end. Only a failed unit can be cancelled: no
    edge leads from `blocked` to `cancelled`.
    """
    blocked = state is WorkUnitState.BLOCKED
    if is_over_budget(session, unit):
        if blocked:
            return "None: its LLM-call budget is spent, and nothing raises it (budget_exceeded)"
        return "Cancel this unit: its LLM-call budget is spent, and nothing raises it"
    exhausted = unit.attempt_count >= unit.max_attempts
    route = (
        "authorize a retry with a raised attempt limit"
        if exhausted
        else "have the system requeue it"
    )
    if evaluate_readiness(session, unit.id, for_update=False).status is not ReadinessStatus.READY:
        route = f"restore its readiness {_READINESS_OWNERS}, then {route}"
    if blocked:
        return route[0].upper() + route[1:]
    return f"Cancel this unit, or {route}"
