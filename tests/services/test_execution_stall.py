"""WS-P2.19 -- the stall detector, and the four things about it that need proving.

**Both directions of the threshold, by moving the THRESHOLD.** A unit past the grace is reported
and one inside it is not, from a single fixture whose rows are never re-timed between the two
assertions. That is the shape `test_stalled_approvals.py` uses for the same reason: a report
exercised only in the direction that fires is a report nobody has shown can stay quiet.

**Legitimately slow work is not reported for being slow.** The threshold is a margin on the
claim's own hold, and the hold is granted per what the package says its work reaches. So two units
claimed the same length of time ago, one repository-shaped and one reaching an outside system, get
different answers -- and the only input that differs between them is the reach they declared. That
is the discriminator, and it is the ONLY one: this detector cannot see progress, and the test that
would claim otherwise is deliberately absent (see the module docstring, and the renewal test
below, which shows why no in-band signal of life survives a lapse).

**It reports.** No transition, no reclaim, no failure, no write. Asserted the way Increment 8
asserted the graduation ledger graduates nothing: by re-reading the unit and the claim afterwards
and by counting the events, not by trusting the objects the call returned.

**The threshold cannot be disabled**, by null or by size. Its ancestor was nullable, was null in
production, and reported nothing for a whole workstream.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.kernel.leases import DEFAULT_LEASE, LEASE_CEILING
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Claim, Event, WorkUnit
from orchestrator.services.claims import (
    CLAIM_HOLDING_STATES,
    LeaseGrant,
    claim_unit,
    renew_claim,
)
from orchestrator.services.execution_stall import stalled_executions
from tests.services.test_claims import worker
from tests.services.test_dispatch import ready_unit
from tests.services.test_reclaim import expire

# 0 reports at the lapse itself: maximally on, and it needs no sleep.
AT_THE_LAPSE = 0
# Comfortably longer than the second by which `expire` puts a hold in the past.
AN_HOUR = 3_600


def _held_unit(
    session: Session,
    *,
    key: str,
    state: WorkUnitState = WorkUnitState.EXECUTING,
    reach: list[str] | None = None,
) -> tuple[WorkUnit, Claim, LeaseGrant]:
    """A unit holding a live claim, in one of the two states that hold one.

    The state is written directly. That is fixture setup for a state a worker reaches through its
    own transition, not a shortcut around runtime behaviour -- what is under test here is a
    predicate over (state, claim), and the route into the state is `test_lifecycle_events`'.
    """
    unit = ready_unit(session, key=key, reach=reach)
    grant = claim_unit(session, unit.id, worker(), f"{key}-claim")
    assert isinstance(grant, LeaseGrant)
    unit.state = state
    session.commit()
    claim = session.get(Claim, grant.claim_id)
    assert claim is not None
    return unit, claim, grant


def _shift_back(session: Session, claim: Claim, delta: timedelta) -> None:
    """Move a claim's whole life earlier, preserving the hold it was granted.

    Both timestamps move together, so the claim still says exactly what it always said about how
    long this unit's work was given -- only its age changes. `work_units.updated_at` cannot be
    treated this way (a trigger rewrites it on every update); `claims` carries no trigger, and
    manipulating lease expiry as deterministic fixture setup is the established idiom here.
    """
    session.execute(
        text(
            "UPDATE claims SET acquired_at = acquired_at - :delta, "
            "lease_expires_at = lease_expires_at - :delta WHERE id = :claim_id"
        ),
        {"delta": delta, "claim_id": claim.id},
    )
    session.commit()
    session.expire_all()


def _reported(session: Session, grace_seconds: int) -> list[uuid.UUID]:
    return [
        stalled.work_unit_id for stalled in stalled_executions(session, grace_seconds=grace_seconds)
    ]


# ---------------------------------------------------------------------------------------------
# 1. Both directions of the threshold, from one fixture, by moving the threshold
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(CLAIM_HOLDING_STATES), ids=lambda s: s.value)
def test_a_unit_past_the_grace_is_reported_and_the_same_unit_inside_it_is_not(
    migrated_session: Session, state: WorkUnitState
) -> None:
    """Parametrized over BOTH claim-holding states.

    `executing` is the case the workstream is named for. `claimed` is here because a worker that
    dies between taking the unit and starting it leaves exactly the same unreachable unit, and the
    write path's own definition of "has an active claim" is these two together -- a report keyed
    on the narrower one would leave a hole one state wide.
    """
    unit, claim, _ = _held_unit(migrated_session, key=f"stall-{state.value}", state=state)
    expire(migrated_session, claim.id)

    assert _reported(migrated_session, AT_THE_LAPSE) == [unit.id]
    assert _reported(migrated_session, AN_HOUR) == []


def test_a_unit_whose_hold_has_not_ended_is_not_reported(migrated_session: Session) -> None:
    """The control that the report is keyed on the hold at all.

    Without it, "past the grace" is satisfied by a detector that reports every held unit and is
    merely being silenced by a large threshold above.
    """
    _held_unit(migrated_session, key="stall-live-hold")

    assert _reported(migrated_session, AT_THE_LAPSE) == []


def test_a_settled_unit_holding_a_lapsed_claim_is_not_reported(migrated_session: Session) -> None:
    """The measured cry-wolf case, and the reason the state gate is load-bearing.

    A claim is NOT released when the work succeeds. Production carries 29 unreleased claims whose
    hold ended days ago, every one of them on a unit that finished (43 units, all terminal,
    2026-08-02). Keyed on the claim alone this would report the entire history of the estate.
    """
    unit, claim, _ = _held_unit(migrated_session, key="stall-settled")
    expire(migrated_session, claim.id)
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    assert _reported(migrated_session, AT_THE_LAPSE) == []


def test_only_the_newest_attempt_is_read(migrated_session: Session) -> None:
    """An earlier attempt's lapsed claim says nothing about the attempt running now.

    The first attempt is left UNRELEASED, because that is the reachable case: a claim is not
    released when its attempt succeeds, and a unit that comes back round to `ready` (via
    `revision_required`, say) is claimed again on top of an unreleased, long-lapsed row. A reclaim
    would NOT exercise this -- it releases the superseded claim, so the row would be excluded for
    a second reason and this test would pass with the newest-attempt gate deleted. It was written
    that way first, and the control caught it.
    """
    unit, first, _ = _held_unit(
        migrated_session, key="stall-superseded", state=WorkUnitState.CLAIMED
    )
    expire(migrated_session, first.id)
    unit.state = WorkUnitState.READY
    migrated_session.commit()
    second = claim_unit(migrated_session, unit.id, worker("worker-2"), "stall-superseded-2")
    assert isinstance(second, LeaseGrant)
    migrated_session.expire_all()
    superseded = migrated_session.get(Claim, first.id)
    assert superseded is not None and superseded.released_at is None

    assert _reported(migrated_session, AT_THE_LAPSE) == []


# ---------------------------------------------------------------------------------------------
# 2. Legitimately slow work, and the honest limit of what "slow" can mean here
# ---------------------------------------------------------------------------------------------


def test_work_declared_slow_is_not_reported_for_taking_the_time_it_declared(
    migrated_session: Session,
) -> None:
    """Two units, the same age, opposite answers -- and reach is the only input that differs.

    A repository edit is granted the default fifteen minutes; work against somebody else's system
    of record is granted the shipped artifact's sixty. Twenty minutes in, the first is five
    minutes past its hold and the second has forty left. This is the whole of the
    legitimately-slow discriminator, and it works because the threshold is a margin on a hold that
    was already decided per reach rather than a duration this report chose for everybody.

    Both claims are shifted by the SAME amount, so neither hold is edited -- only their age. The
    two holds are read back and asserted, so a change to the artifact that collapsed them onto one
    number would red this rather than quietly making it prove nothing.
    """
    quick, quick_claim, _ = _held_unit(
        migrated_session, key="stall-repository", reach=["source_repository"]
    )
    slow, slow_claim, _ = _held_unit(
        migrated_session, key="stall-external", reach=["external_system"]
    )
    assert quick_claim.lease_expires_at - quick_claim.acquired_at == DEFAULT_LEASE
    assert slow_claim.lease_expires_at - slow_claim.acquired_at > DEFAULT_LEASE
    twenty_minutes = timedelta(minutes=20)
    _shift_back(migrated_session, quick_claim, twenty_minutes)
    _shift_back(migrated_session, slow_claim, twenty_minutes)

    assert _reported(migrated_session, AT_THE_LAPSE) == [quick.id]
    assert slow.id not in _reported(migrated_session, AT_THE_LAPSE)


def test_a_renewal_cannot_rescue_a_lapsed_claim(migrated_session: Session) -> None:
    """Why observed progress is not available as a discriminator, stated as a mechanism.

    The only in-band signal that a worker is alive is a renewal, and a renewal is refused once the
    hold has ended. So there is no state in which a live worker can distinguish itself from a dead
    one after the lapse -- which is exactly why this report claims only that the ATTEMPT is
    finished, and never that the worker is.
    """
    unit, claim, grant = _held_unit(migrated_session, key="stall-renewal")
    expire(migrated_session, claim.id)

    refused = renew_claim(migrated_session, unit.id, worker(), grant.attempt, grant.lease_token)

    # The real token, so the refusal is the LAPSE and not a credential mismatch that would pass
    # this test while proving nothing.
    assert isinstance(refused, DomainError)
    assert refused.code == "lease_expired"
    assert _reported(migrated_session, AT_THE_LAPSE) == [unit.id]


# ---------------------------------------------------------------------------------------------
# 3. It reports
# ---------------------------------------------------------------------------------------------


def test_the_report_transitions_nothing_reclaims_nothing_and_fails_nothing(
    migrated_session: Session,
) -> None:
    """The unit, its claim and the event log are all exactly as they were.

    Re-read from the database rather than inspected on the returned objects: the WS-P2.1 defect
    was a writer whose in-session object looked right while the row was discarded. Here there is
    no writer at all, and this is what says so.
    """
    unit, claim, _ = _held_unit(migrated_session, key="stall-untouched")
    expire(migrated_session, claim.id)
    version_before, attempts_before = unit.version, unit.attempt_count
    events_before = migrated_session.scalar(select(func.count()).select_from(Event))

    assert _reported(migrated_session, AT_THE_LAPSE) == [unit.id]

    migrated_session.expire_all()
    refreshed_unit = migrated_session.get(WorkUnit, unit.id)
    refreshed_claim = migrated_session.get(Claim, claim.id)
    assert refreshed_unit is not None and refreshed_claim is not None
    assert refreshed_unit.state == WorkUnitState.EXECUTING
    assert (refreshed_unit.version, refreshed_unit.attempt_count) == (
        version_before,
        attempts_before,
    )
    assert refreshed_claim.released_at is None
    assert refreshed_claim.terminal_reason is None
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Claim).where(Claim.work_unit_id == unit.id)
        )
        == 1
    )
    assert migrated_session.scalar(select(func.count()).select_from(Event)) == events_before


# ---------------------------------------------------------------------------------------------
# 4. The threshold cannot be switched off -- by null, and by size
# ---------------------------------------------------------------------------------------------


def test_the_grace_has_no_off_value() -> None:
    field = Settings.model_fields["execution_stall_grace_seconds"]

    assert field.default is not None
    assert field.default > 0
    assert (
        Settings(database_url="postgresql://unused/unused").execution_stall_grace_seconds
        == field.default
    )


@pytest.mark.parametrize("seconds", [-1, 86_401, 604_800, 31_536_000])
def test_a_grace_outside_the_bounds_is_refused(seconds: int) -> None:
    """The size evasion. A value large enough silences the report as completely as a null.

    The upper bound is what makes "cannot be switched off" true of the values an operator can
    actually set, rather than merely of the type.
    """
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://unused/unused", execution_stall_grace_seconds=seconds)


def test_the_bounds_leave_room_for_a_real_decision() -> None:
    """The control for the refusals above: without it they are satisfied by a field that rejects
    everything, and the cap is only meaningful if it is comfortably above the longest hold this
    build will ever grant."""
    accepted = Settings(
        database_url="postgresql://unused/unused",
        execution_stall_grace_seconds=int(LEASE_CEILING.total_seconds()),
    )

    assert accepted.execution_stall_grace_seconds == int(LEASE_CEILING.total_seconds())
