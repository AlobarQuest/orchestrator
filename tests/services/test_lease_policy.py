"""WS-P2.18 Increment 6 -- the per-reach lease, replacing one arbitrary fifteen minutes.

A lease is the period this orchestrator refuses to hand a unit to a second claimant. Three things
about it need proving rather than asserting, and each has a control here.

**The reclaim path.** ``reclaim_expired_claim`` grants a fresh claim without ever calling
``claim_unit``, so a duration read only there is ignored on exactly the path a lapsed lease leads
to. It is proven by reclaiming and reading the reclaimed lease, not the first one -- and the same
for ``renew_claim``, which is the third grant site and which the plan for this increment did not
name.

**The polarity.** The artifact can only ever lengthen a hold: the loader refuses a declared lease at
or below the build's default and above the build's ceiling. Both directions are exercised, together
with the control that a value between them loads.

**What it is NOT.** A lapse transitions nothing and nothing reclaims on its own, so the lease bounds
no hung worker and this increment does not claim it does. That is asserted directly, on a unit left
sitting past its own expiry.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import FactoryPolicy, load_factory_policy
from orchestrator.kernel.leases import DEFAULT_LEASE, LEASE_CEILING
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Claim
from orchestrator.services.claims import (
    LeaseGrant,
    claim_unit,
    reclaim_expired_claim,
    renew_claim,
)
from orchestrator.services.dispatch import dispatch_work_unit
from tests.services.test_claims import worker
from tests.services.test_dispatch import (
    FakeGitHubDispatcher,
    dispatch_command,
    ready_unit,
    settings,
)
from tests.services.test_reclaim import SYSTEM, expire

# What the shipped artifact declares. Named here so a change to the document is a change to a
# constant a reader can see, rather than a number buried in six assertions.
ESTATE_LEASE = timedelta(minutes=30)
EXTERNAL_LEASE = timedelta(minutes=60)

DECIDED = 'decided = "2026-08-02"'
# Two rows declaring DIFFERENT leases, neither of them the default, so "keyed on reach" is
# distinguishable from "one number applied everywhere". The other two declare none, which is how
# the default-contributing case is exercised in the same document.
LEASED = f"""
version = 5

[reach.source_repository]
rationale = "repository only"
{DECIDED}

[reach.live_estate]
rationale = "something already serving"
{DECIDED}

[reach.live_estate.lease]
rationale = "an estate change waits on a build and a health check"
{DECIDED}
minutes = 25

[reach.external_system]
rationale = "outside the estate"
{DECIDED}

[reach.external_system.lease]
rationale = "its wall clock belongs to somebody else"
{DECIDED}
minutes = 90

[reach.operator_machine]
rationale = "the operator's own machine"
{DECIDED}
"""


def leased(tmp_path: Path, text: str = LEASED) -> FactoryPolicy:
    path = tmp_path / "factory-policy.toml"
    path.write_text(text, encoding="utf-8")
    return load_factory_policy(path)


def lease_table(minutes: object) -> str:
    return LEASED.replace("minutes = 25", f"minutes = {minutes}")


def granted(session: Session, grant: LeaseGrant) -> timedelta:
    """The hold actually written to the claim row, measured against when it was acquired.

    Read from the database rather than from the grant, because the grant reports what the service
    returned and the row is what a later renewal, reclaim or evidence write is judged against.
    """
    claim = session.get(Claim, grant.claim_id)
    assert claim is not None
    return claim.lease_expires_at - claim.acquired_at


# ---------------------------------------------------------------------------------------------
# 1. What the shipped artifact declares, and where it deliberately declares nothing
# ---------------------------------------------------------------------------------------------


def test_the_shipped_artifact_declares_a_lease_only_where_it_says_it_does() -> None:
    """Two declared, two deliberately absent -- and absence is the default, not the lack of one.

    The two rows carrying nothing are the two whose work is repository-shaped: an edit lands, and
    a second claimant arriving early costs a duplicate rather than damage. Declaring a longer hold
    for them would be a number chosen to look decided.
    """
    rows = load_factory_policy().rows

    assert {member for member, row in rows.items() if row.lease is not None} == {
        "live_estate",
        "external_system",
    }
    assert rows["live_estate"].lease is not None
    assert rows["live_estate"].lease.duration == ESTATE_LEASE
    assert rows["external_system"].lease is not None
    assert rows["external_system"].lease.duration == EXTERNAL_LEASE
    assert rows["source_repository"].lease is None
    assert rows["operator_machine"].lease is None


def test_every_shipped_lease_lengthens_the_hold_the_build_already_grants() -> None:
    """The polarity, checked against the document rather than only against the loader.

    A row that shortened a hold would be the one shape this artifact must never contain, so it is
    worth asserting over what is actually shipped and not only over what the parser would reject.
    """
    rows = load_factory_policy().rows
    declared = [row.lease.duration for row in rows.values() if row.lease is not None]

    assert declared  # the control: a scan that finds nothing proves nothing
    assert all(DEFAULT_LEASE < duration <= LEASE_CEILING for duration in declared)


# ---------------------------------------------------------------------------------------------
# 2. The answer is keyed on reach, and composes by maximum
# ---------------------------------------------------------------------------------------------


def test_each_reach_gets_its_own_declared_lease(tmp_path: Path) -> None:
    """Reach X gets X's duration and reach Y gets Y's -- neither of them the old constant."""
    policy = leased(tmp_path)

    assert policy.lease_for(("live_estate",)) == timedelta(minutes=25)
    assert policy.lease_for(("external_system",)) == timedelta(minutes=90)
    assert policy.lease_for(("source_repository",)) == DEFAULT_LEASE


def test_a_reach_set_gets_the_longest_hold_any_member_was_decided_to_need(
    tmp_path: Path,
) -> None:
    """Composition is the maximum, and a row declaring nothing contributes the default.

    Stated as monotonicity rather than as three examples: over every subset of the vocabulary,
    adding a member never shortens the answer. That is the same direction the refusal sets compose
    in -- adding a member can only lengthen the list of objections -- and it is what makes an
    incomplete declaration safe rather than lenient.
    """
    policy = leased(tmp_path)
    members = ("source_repository", "live_estate", "external_system", "operator_machine")

    assert policy.lease_for(("source_repository", "external_system")) == timedelta(minutes=90)
    assert policy.lease_for(("live_estate", "operator_machine")) == timedelta(minutes=25)
    for index, member in enumerate(members):
        prefix = members[:index]
        assert policy.lease_for(prefix + (member,)) >= policy.lease_for(prefix or None)


def test_undeclared_and_unrecognised_reach_both_get_the_named_default(tmp_path: Path) -> None:
    """Neither a crash nor an unbounded hold: the one duration the build owns, by name.

    The unrecognised case cannot arrive through ``reach_from_snapshot``, which reads all-or-nothing
    and reports such a declaration as undeclared -- so it is asked of the policy directly, which is
    the only way to reach it.
    """
    policy = leased(tmp_path)

    assert policy.lease_for(None) == DEFAULT_LEASE
    assert policy.lease_for(()) == DEFAULT_LEASE
    assert policy.lease_for(("a_reach_this_build_has_never_heard_of",)) == DEFAULT_LEASE
    assert policy.lease_for(("external_system", "not_a_member")) == timedelta(minutes=90)


# ---------------------------------------------------------------------------------------------
# 3. The loader can express a longer hold and nothing else
# ---------------------------------------------------------------------------------------------


def test_a_lease_between_the_two_bounds_loads(tmp_path: Path) -> None:
    """The control for every refusal below. Without it they are satisfied by a parser that
    rejects everything."""
    policy = leased(tmp_path, lease_table(int(LEASE_CEILING.total_seconds() // 60)))

    row = policy.rows["live_estate"]
    assert row.lease is not None
    assert row.lease.duration == LEASE_CEILING


@pytest.mark.parametrize(
    "minutes",
    [
        1,  # shorter than the default: the direction that hands a live worker's unit away sooner
        14,
        15,  # exactly the default: a row restating a number that already lives in the kernel
        121,  # past the ceiling
        525_600,  # a year -- switching reassignment off without saying so
        0,
        -30,
    ],
)
def test_a_lease_outside_the_two_bounds_stops_the_document_loading(
    tmp_path: Path, minutes: int
) -> None:
    with pytest.raises(DomainError) as error:
        leased(tmp_path, lease_table(minutes))

    assert error.value.code == "factory_policy_invalid"


@pytest.mark.parametrize("minutes", ['"30"', "30.0", "true", "[30]"])
def test_a_lease_that_is_not_a_whole_number_of_minutes_stops_the_document_loading(
    tmp_path: Path, minutes: str
) -> None:
    # `true` matters on its own: a bool is an int in Python, so an unguarded check would read
    # `minutes = true` as a one-minute hold rather than as the shape error it is.
    with pytest.raises(DomainError) as error:
        leased(tmp_path, lease_table(minutes))

    assert error.value.code == "factory_policy_invalid"


@pytest.mark.parametrize(
    "table",
    [
        '[reach.live_estate.lease]\nrationale = "r"\ndecided = "2026-08-02"\n',
        '[reach.live_estate.lease]\nminutes = 30\ndecided = "2026-08-02"\n',
        '[reach.live_estate.lease]\nrationale = "r"\nminutes = 30\n',
        '[reach.live_estate.lease]\nrationale = "r"\ndecided = "2026-08-02"\n'
        'minutes = 30\nnote = "an extra nobody reads"\n',
        '[reach.live_estate.lease]\nrationale = ""\ndecided = "2026-08-02"\nminutes = 30\n',
    ],
)
def test_a_lease_table_missing_or_gaining_a_field_stops_the_document_loading(
    tmp_path: Path, table: str
) -> None:
    """Every declared lease carries its reason and its date, exactly as every other row does.

    A duration with no rationale is a number somebody typed; the whole editing contract of this
    document is that a change is a decision with provenance attached.
    """
    start = LEASED.index("[reach.live_estate.lease]")
    end = LEASED.index("[reach.external_system]")

    with pytest.raises(DomainError) as error:
        leased(tmp_path, LEASED[:start] + table + "\n" + LEASED[end:])

    assert error.value.code == "factory_policy_invalid"


# ---------------------------------------------------------------------------------------------
# 4. Every path that grants or extends a hold reads the same source
# ---------------------------------------------------------------------------------------------


def test_a_claim_gets_the_lease_its_reach_was_decided_to_need(migrated_session: Session) -> None:
    """The first grant site, with the control that a different reach gets a different answer."""
    external = ready_unit(migrated_session, key="lease-external", reach=["external_system"])
    repository = ready_unit(migrated_session, key="lease-repository", reach=["source_repository"])

    long_hold = claim_unit(migrated_session, external.id, worker(), "claim-external")
    short_hold = claim_unit(migrated_session, repository.id, worker(), "claim-repository")

    assert isinstance(long_hold, LeaseGrant)
    assert isinstance(short_hold, LeaseGrant)
    assert granted(migrated_session, long_hold) == EXTERNAL_LEASE
    assert granted(migrated_session, short_hold) == DEFAULT_LEASE


def test_a_unit_whose_package_declared_no_reach_keeps_the_hold_it_has_always_had(
    migrated_session: Session, ready_unit
) -> None:
    """The population that exists today. Nothing about it changes, which is the point."""
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-undeclared")

    assert isinstance(grant, LeaseGrant)
    assert granted(migrated_session, grant) == DEFAULT_LEASE


def test_a_RECLAIMED_claim_gets_the_policy_lease_and_not_the_default(
    migrated_session: Session,
) -> None:
    """§2.1 -- the one that fails silently.

    ``reclaim_expired_claim`` reaches ``_acquire_reclaimed_claim`` without passing through
    ``claim_unit``, so a duration read only in the latter is honoured on the first attempt and
    dropped on every one after it. That is the worst place to drop it: a reclaim happens precisely
    because the previous hold lapsed, so the attempt that most needs the considered duration is the
    one that would not get it.

    Discriminating in two directions at once: the reclaimed hold is asserted to be the reach's own
    duration AND asserted not to be the default, so a regression that reverted this line would fail
    on the second clause even if the first were somehow satisfied.
    """
    unit = ready_unit(migrated_session, key="lease-reclaim", reach=["external_system"])
    first = claim_unit(migrated_session, unit.id, worker(), "reclaim-lease-1")
    assert isinstance(first, LeaseGrant)
    expire(migrated_session, first.claim_id)

    second = reclaim_expired_claim(
        migrated_session, unit.id, SYSTEM, worker("worker-2"), "reclaim-lease-2"
    )

    assert isinstance(second, LeaseGrant)
    assert second.attempt == 2
    assert granted(migrated_session, second) == EXTERNAL_LEASE
    assert granted(migrated_session, second) != DEFAULT_LEASE


def test_a_RENEWED_claim_is_extended_by_the_policy_lease_and_not_the_default(
    migrated_session: Session,
) -> None:
    """The third grant site, which the plan for this increment did not name.

    A renewal that reset the hold to the kernel default would silently undo a considered one on
    every extension -- and it would do so on the path a long-running attempt takes by definition,
    since renewing is what a run reaching past its first hold does.
    """
    unit = ready_unit(migrated_session, key="lease-renew", reach=["live_estate"])
    grant = claim_unit(migrated_session, unit.id, worker(), "renew-lease-1")
    assert isinstance(grant, LeaseGrant)

    renewed = renew_claim(migrated_session, unit.id, worker(), grant.attempt, grant.lease_token)

    assert isinstance(renewed, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.renewed_at is not None
    assert claim.lease_expires_at - claim.renewed_at == ESTATE_LEASE
    assert claim.lease_expires_at - claim.renewed_at != DEFAULT_LEASE


def test_an_unreadable_artifact_grants_the_default_rather_than_refusing_a_hold(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing toward the default here is not the permissive reading, and the control shows why.

    Refusing would be refusing to give a lease to a worker that already holds the unit, which
    restrains the wrong actor. Whether such a unit should have been sent is the admission question,
    and ``reach_admission`` answers it by refusing outright -- so an artifact this process cannot
    read stops work arriving, and does not strand work that already arrived.
    """
    unit = ready_unit(migrated_session, key="lease-unreadable", reach=["external_system"])

    readable = claim_unit(migrated_session, unit.id, worker(), "unreadable-control")
    assert isinstance(readable, LeaseGrant)
    assert granted(migrated_session, readable) == EXTERNAL_LEASE
    expire(migrated_session, readable.claim_id)

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr("orchestrator.services.lease_policy.load_factory_policy", unreadable)
    reclaimed = reclaim_expired_claim(
        migrated_session, unit.id, SYSTEM, worker("worker-2"), "unreadable-reclaim"
    )

    assert isinstance(reclaimed, LeaseGrant)
    assert granted(migrated_session, reclaimed) == DEFAULT_LEASE


# ---------------------------------------------------------------------------------------------
# 5. What the lease is not
# ---------------------------------------------------------------------------------------------


def test_a_lapsed_lease_transitions_nothing_and_nobody_reclaims_on_its_own(
    migrated_session: Session,
) -> None:
    """R6, made concrete: the lease is not stall control and this increment has not made it one.

    A unit whose hold has lapsed sits exactly where it was, in ``claimed``, with its claim
    unreleased, until a SYSTEM actor asks for it. Bounding a worker that has hung is a real hole
    and it is WS-P2.19's; a longer or shorter number here does not touch it.
    """
    unit = ready_unit(migrated_session, key="lease-not-stall-control", reach=["source_repository"])
    grant = claim_unit(migrated_session, unit.id, worker(), "lapse-1")
    assert isinstance(grant, LeaseGrant)
    expire(migrated_session, grant.claim_id)

    migrated_session.expire_all()
    claim = migrated_session.get(Claim, grant.claim_id)
    reloaded = migrated_session.get(type(unit), unit.id)

    assert claim is not None and claim.released_at is None and claim.terminal_reason is None
    assert reloaded is not None
    assert WorkUnitState(reloaded.state) is WorkUnitState.CLAIMED


def test_the_off_switch_outranks_a_reach_policy_has_spoken_about(
    migrated_session: Session,
) -> None:
    """R4, proven non-vacuously on the reach carrying the longest declared hold.

    Without the first call, "nothing was sent" is satisfied by a unit nothing recognised. With it,
    the claim is the real one: a reach that policy has said the most about is still refused by the
    one switch, because policy is consulted after it and cannot read it at all -- and no duration
    is consulted at admission in the first place, since a lease is granted only once work has
    already been sent and claimed.
    """
    unit = ready_unit(migrated_session, key="switch-outranks-lease", reach=["external_system"])
    github = FakeGitHubDispatcher([])

    admitted = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)
    refused = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=2),
        settings(enabled=False),
        github,
    )

    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert (refused.status, refused.reason_code) == ("skipped", "dispatch_disabled")
    assert len(github.calls) == 1
