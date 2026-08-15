"""How long a claim on this unit holds it, given what its package says the work touches.

WS-P2.18 Increment 6. Until now every claim in this system's history got the same fifteen minutes,
chosen once and never revisited. A lease is the period in which this orchestrator refuses to hand a
unit to a second claimant, and how much of that a run needs depends entirely on what the run is
waiting for -- a repository edit finishes in under a minute, an estate change waits on a build and
a health check, and work against somebody else's system of record waits on somebody else.

**Every path that grants or extends a hold reads its duration from here.** There are three, and the
third is the one that matters: ``reclaim_expired_claim`` grants a fresh claim through
``_acquire_reclaimed_claim`` without ever calling ``claim_unit``, so a duration read only in the
latter would be ignored on exactly the path a lapsed lease leads to. ``renew_claim`` is the third,
and it must agree too, or a renewal quietly resets a considered hold to the default.

**This is not stall control and must not be read as though it were.** Nothing here notices a worker
that has hung; a lapse only makes the unit reclaimable by a SYSTEM actor who asks, and nothing asks
on its own. The lease was never bounding a hung worker -- it was merely the only thing incidentally
adjacent to it -- and tuning it bounds nothing. That hole is real and it is WS-P2.19's.

**An unreadable artifact yields the default rather than a refusal, and that is not a hole.** A
refusal here would be a refusal to grant a lease to a worker that already holds the unit, which
restrains the wrong actor at the wrong moment. Whether such a unit should have been sent at all is
the admission question, and ``services.reach_admission`` answers it there by refusing outright, so
a policy this process cannot read stops work arriving rather than stops work finishing.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.leases import DEFAULT_LEASE
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.reach_vocabulary import reach_from_snapshot


def claim_lease(session: Session, unit: WorkUnit) -> timedelta:
    """The hold this unit's next claim, renewal or reclaim gets.

    Resolved from the revision rather than from the unit, because reach is what the PACKAGE says
    its work touches: breaking that work down splits it up, it does not change where it lands.
    Read through ``reach_from_snapshot``, which returns ``None`` for any declaration it cannot read
    whole, so a snapshot naming a member this build predates gets the default rather than the
    recognisable part of itself.
    """
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        return DEFAULT_LEASE
    try:
        policy = load_factory_policy()
    except DomainError:
        return DEFAULT_LEASE
    return policy.lease_for(reach_from_snapshot(revision.enforcement_snapshot))
