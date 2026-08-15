"""A unit whose hold ended and whose work never did (WS-P2.19).

Three reports already notice something that stopped: a post-release verification that never came
back, a release binding nobody confirmed, and a human gate nobody answered. None of them looks at
the case this module is for -- a unit that was claimed, started, and then went quiet. Nothing did,
and the lease was not doing it either: WS-P2.18 Increment 6 established that a lapse transitions
nothing and that nobody reclaims on their own, which left the only thing incidentally adjacent to
a hung worker doing nothing about one.

**What a stall is here.** A unit still in a state that holds its claim, whose current claim's hold
ended more than the grace ago. The clock is the claim's own
``lease_expires_at`` -- the hold this unit's package asked for, given what its work reaches -- not a
duration invented in this module. Two things follow for free. Work declared as slow is granted a
longer hold and is never reported for taking it. And a second, disagreeing copy of "how long may
this take" never comes into existence beside the one that already answers it.

**The CURRENT claim only.** Every earlier attempt keeps its claim row, and a claim is not released
when the work succeeds: production carries 29 unreleased claims whose hold ended days ago, on units
that all finished. Keyed on any claim rather than the newest, this would report the whole history
of the estate as stalled, and would report a unit on its second attempt for the first attempt's
long-dead hold. The state gate is what makes that safe, and the newest-attempt gate is what makes
it exact.

**There is deliberately no ``released_at IS NULL`` clause**, although the activeness test this
mirrors has one. Every caller of ``release_claim`` transitions the unit out of these two states in
the same transaction, so the clause has no reachable case of its own -- proven by removing it and
watching nothing red. Left in, its only possible effect would be to HIDE a unit that some future
path had stranded with a released claim and a holding state, which is precisely the unit this
report exists for. So the omission is the fail-safe direction, not an oversight.

**What it can and cannot tell you.** It cannot see progress. A worker that is running perfectly
well but never renews looks exactly like one that hung, because nothing renews on a cadence and a
run has no reason to send the only in-band signal of life there is. So the claim made here is
narrower, and it is true either way: *this attempt can no longer report anything*. A lapsed claim
refuses a renewal (``lease_expired``) and refuses an evidence write (``claim_not_active``), so by
the time a unit is visible here its worker is already locked out -- alive or dead. That lock-out
happened at the lapse, before the grace began, which is why surfacing it cannot destroy work that
was about to arrive: there is no longer a route by which it could arrive.

**It reports.** No transition, no reclaim, no failure, no write of any kind. The actions a person
may take already exist -- cancelling the unit is theirs directly, recovering the expired claim is
the system's on request -- and what was missing was noticing. Derived live from the two source
tables, so an entry disappears the moment the unit moves and there is nothing persisted to drift.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.persistence.models import Claim, WorkUnit
from orchestrator.services.claims import CLAIM_HOLDING_STATES


@dataclass(frozen=True)
class StalledExecution:
    work_unit_id: uuid.UUID
    title: str
    state: str
    attempt: int
    hold_ended_at: datetime
    stalled_seconds: int


def stalled_executions(session: Session, *, grace_seconds: int) -> tuple[StalledExecution, ...]:
    """Every unit holding a claim whose hold ended more than ``grace_seconds`` ago.

    ``grace_seconds`` is a plain int and takes no "off" value, for the reason
    ``dead_letter_stalled_approval_seconds`` does not: its ancestor was nullable, was null in
    production, and reported nothing for an entire workstream. The bound that stops a large value
    doing the same thing lives on the setting, where the reachable values are.
    """
    now = TransactionClock().now(session)
    cutoff = now - timedelta(seconds=grace_seconds)
    newest_attempt = (
        select(func.max(Claim.attempt))
        .where(Claim.work_unit_id == WorkUnit.id)
        .correlate(WorkUnit)
        .scalar_subquery()
    )
    rows = session.execute(
        select(WorkUnit, Claim)
        .join(Claim, Claim.work_unit_id == WorkUnit.id)
        .where(
            WorkUnit.state.in_(sorted(str(state) for state in CLAIM_HOLDING_STATES)),
            Claim.attempt == newest_attempt,
            Claim.lease_expires_at <= cutoff,
        )
        .order_by(Claim.lease_expires_at, WorkUnit.id)
    ).all()
    return tuple(
        StalledExecution(
            work_unit_id=unit.id,
            title=unit.title,
            state=unit.state,
            attempt=claim.attempt,
            hold_ended_at=claim.lease_expires_at,
            stalled_seconds=int((now - claim.lease_expires_at).total_seconds()),
        )
        for unit, claim in rows
    )
