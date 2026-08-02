"""WS-P2.18 Increment 5 -- the change window: work runs only in hours somebody decided it may.

The window is the first policy in this artifact that depends on something outside the document, so
it is the first that can be flaky, wrong twice a year, or right only on the machine that wrote it.
Every proof here therefore injects the clock and runs in both directions: each refusal is paired
with the control showing the same predicate admitting the input it is meant to admit.

Three claims get more than an example. Daylight saving is exercised against a real zone on the two
real days it goes wrong -- the hour that happens twice and the hour that does not happen at all --
by sweeping every minute rather than by picking one. Precedence is proven non-vacuously, by
admitting the same unit with the switch on. And the rule that a window governs admission and never
execution is proven on a unit that is already running when the window shuts.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from orchestrator.api.schemas import FactoryPolicyReachResponse
from orchestrator.errors import DomainError
from orchestrator.factory_policy import (
    OUTSIDE_CHANGE_WINDOW,
    FactoryPolicy,
    load_factory_policy,
)
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.dispatch import dispatch_work_unit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.reach_admission import REACH_POLICY_UNREADABLE, change_window_refusal
from tests.services.test_dispatch import (
    FakeGitHubDispatcher,
    dispatch_command,
    ready_unit,
    settings,
)

NEW_YORK = ZoneInfo("America/New_York")
WORKER = ActorContext("worker", ActorRole.WORKER)

MACHINE = ("operator_machine",)
ESTATE = ("live_estate",)
NO_WINDOW = ("source_repository",)

DECIDED = 'decided = "2026-08-01"'
# A daytime window and a wrapping night one, so the two shapes are tested apart. Their hours do NOT
# overlap, which is exactly the composition case §7 below needs and exactly the arrangement the
# shipped artifact avoids.
WINDOWED = f"""
version = 4

[reach.source_repository]
rationale = "repository only"
{DECIDED}

[reach.live_estate]
rationale = "something already serving"
{DECIDED}

[reach.live_estate.change_window]
rationale = "wraps midnight, which is the shape a nightly window has"
{DECIDED}
timezone = "America/New_York"
start = "22:00"
end = "02:00"

[reach.external_system]
rationale = "outside the estate"
{DECIDED}

[reach.operator_machine]
rationale = "the operator's own machine"
{DECIDED}

[reach.operator_machine.change_window]
rationale = "an ordinary daytime window, so the non-wrapping shape is tested apart"
{DECIDED}
timezone = "America/New_York"
start = "09:00"
end = "17:00"
"""

WINDOW_TABLE = """
[reach.operator_machine.change_window]
rationale = "an ordinary daytime window, so the non-wrapping shape is tested apart"
decided = "2026-08-01"
timezone = "America/New_York"
start = "09:00"
end = "17:00"
"""


@dataclass(frozen=True)
class FrozenClock:
    """A clock that reads whatever the test says it reads.

    The database's own clock is the production reader and it cannot be moved. The related trap is
    already documented in this repository: a row's ``updated_at`` is rewritten by a trigger, so
    ageing data is not a way to exercise anything that reads a clock either. Injection is.
    """

    instant: datetime

    # `session` is unused and must stay: it is the `Clock` protocol's shape, and the production
    # implementation reads the transaction's timestamp through it.
    def now(self, session: Session) -> datetime:
        return self.instant


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """An instant named in New York local terms.

    Safe for every date used with it here, and used ONLY on dates with no transition on them: a
    local reading names two instants on one day a year and none on another, which is the whole
    reason production converts an instant into the zone rather than reading a clock in it. The two
    transition days below are therefore built from UTC instead.
    """
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK)


def windowed(tmp_path: Path, text: str = WINDOWED) -> FactoryPolicy:
    path = tmp_path / "factory-policy.toml"
    path.write_text(text, encoding="utf-8")
    return load_factory_policy(path)


def a_day_of_instants(day: datetime, step: timedelta = timedelta(minutes=30)) -> list[datetime]:
    return [day + step * index for index in range(int(timedelta(days=1) / step))]


# ---------------------------------------------------------------------------------------------
# 1. What the shipped artifact declares, and where it deliberately declares nothing
# ---------------------------------------------------------------------------------------------


def test_the_shipped_artifact_declares_a_window_only_where_it_says_it_does() -> None:
    """R13's sorting, as shipped, plus the reason two rows carry nothing.

    An absent window is this policy having no objection on those grounds. It is also what keeps
    every other suite in this repository independent of the time of day: the default reach of a
    test unit is ``source_repository``, and that row declares no window on purpose, because work
    that lands in a repository and nowhere else is inert until something separately acts on it.
    """
    rows = load_factory_policy().rows

    assert {member for member, row in rows.items() if row.change_window is not None} == {
        "live_estate",
        "operator_machine",
    }
    assert rows["source_repository"].change_window is None
    assert rows["external_system"].change_window is None


def test_the_shipped_windows_overlap_because_composition_is_intersection() -> None:
    """Two rows whose hours did not overlap make work reaching both UNRUNNABLE, not restrained.

    Asserted over the hours themselves rather than by comparing the two tables, so a later edit
    that changes one row's hours without changing the other's is caught by what it costs rather
    than by an equality nobody would have to keep.
    """
    rows = load_factory_policy().rows
    both = ("live_estate", "operator_machine")
    policy = load_factory_policy()

    open_for_both = [
        instant
        for instant in a_day_of_instants(local(2026, 8, 12, 0), timedelta(minutes=15))
        if policy.window_refusal(both, instant) is None
    ]

    assert open_for_both, "no instant satisfies both shipped windows; such work could never run"
    assert all(rows[member].change_window is not None for member in both)


# ---------------------------------------------------------------------------------------------
# 2. Both directions, on the same reach
# ---------------------------------------------------------------------------------------------


def test_in_window_work_draws_no_objection_and_out_of_window_work_draws_a_named_one(
    tmp_path: Path,
) -> None:
    policy = windowed(tmp_path)

    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 11)) is None
    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 20)) == OUTSIDE_CHANGE_WINDOW


def test_the_boundaries_are_closed_at_the_start_and_open_at_the_end(tmp_path: Path) -> None:
    # A half-open interval, so a window written as 09:00-17:00 and one written as 17:00-21:00 do
    # not both claim 17:00. Nothing hangs on which end is which; something hangs on it being stated.
    policy = windowed(tmp_path)

    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 9, 0)) is None
    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 16, 59)) is None
    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 17, 0)) == OUTSIDE_CHANGE_WINDOW
    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 8, 59)) == OUTSIDE_CHANGE_WINDOW


def test_a_window_that_wraps_midnight_is_open_across_it(tmp_path: Path) -> None:
    # The shape a nightly window naturally has, and the one an implementation comparing two times
    # gets wrong: 22:00 to 02:00 is not an empty interval.
    policy = windowed(tmp_path)

    assert policy.window_refusal(ESTATE, local(2026, 8, 12, 23)) is None
    assert policy.window_refusal(ESTATE, local(2026, 8, 12, 1)) is None
    assert policy.window_refusal(ESTATE, local(2026, 8, 12, 12)) == OUTSIDE_CHANGE_WINDOW


def test_a_reach_with_no_window_is_never_refused_on_window_grounds(tmp_path: Path) -> None:
    """Swept across a whole day, with the control that the sweep can fail.

    Without the second half this is satisfied by a predicate that never refuses anything, which is
    precisely the failure this workstream keeps finding.
    """
    policy = windowed(tmp_path)
    day = a_day_of_instants(local(2026, 8, 12, 0))

    assert [policy.window_refusal(NO_WINDOW, instant) for instant in day] == [None] * len(day)
    assert any(policy.window_refusal(MACHINE, instant) is not None for instant in day)


def test_composition_over_a_reach_set_can_only_narrow(tmp_path: Path) -> None:
    # ADR-0009's intersection-of-permission. The fixture's two windows are disjoint on purpose, so
    # an instant inside one is outside the other and the pair is refused whichever it is.
    policy = windowed(tmp_path)
    midday = local(2026, 8, 12, 11)
    midnight = local(2026, 8, 12, 23)

    assert (policy.window_refusal(MACHINE, midday), policy.window_refusal(ESTATE, midday)) == (
        None,
        OUTSIDE_CHANGE_WINDOW,
    )
    assert policy.window_refusal(("live_estate", "operator_machine"), midday) == (
        OUTSIDE_CHANGE_WINDOW
    )
    assert policy.window_refusal(("live_estate", "operator_machine"), midnight) == (
        OUTSIDE_CHANGE_WINDOW
    )


def test_a_reach_nobody_declared_draws_no_window_objection(tmp_path: Path) -> None:
    """The one place the window deliberately does not reach, with the control beside it.

    Every window hangs off a reach row, so an undeclared reach has no row to consult and no honest
    way to pick one -- reach is declared, never inferred. Requiring every window at once would be
    fail-closed in name only: the fixture's two windows are disjoint, so such work would become
    permanently unrunnable rather than restrained. The exposure is exactly the set the admission
    term still lets through, which is the named grandfathering list, which deletes itself.
    """
    policy = windowed(tmp_path)
    shut = local(2026, 8, 12, 20)

    assert policy.window_refusal(None, shut) is None
    assert policy.window_refusal((), shut) is None
    assert policy.window_refusal(MACHINE, shut) == OUTSIDE_CHANGE_WINDOW  # control


def test_a_member_outside_the_vocabulary_contributes_no_window_and_no_crash(
    tmp_path: Path,
) -> None:
    # Reached only through a caller that did not ask the reach term first. It must not raise: the
    # membership lookup is by `.get`, so an unknown name is a row that is not there rather than a
    # KeyError surfacing as an unhandled 500.
    policy = windowed(tmp_path)

    assert policy.window_refusal(("invented",), local(2026, 8, 12, 20)) is None


# ---------------------------------------------------------------------------------------------
# 3. Daylight saving, on the two days it goes wrong
# ---------------------------------------------------------------------------------------------

DOUBLED_HOUR = """
[reach.operator_machine.change_window]
rationale = "confined to the hour that occurs twice on the November transition"
decided = "2026-08-01"
timezone = "America/New_York"
start = "01:00"
end = "02:00"
"""

MISSING_HOUR = """
[reach.operator_machine.change_window]
rationale = "confined to the hour that does not occur on the March transition"
decided = "2026-08-01"
timezone = "America/New_York"
start = "02:00"
end = "03:00"
"""


def test_the_hour_that_happens_twice_is_inside_the_window_both_times(tmp_path: Path) -> None:
    """2026-11-01: 01:30 local happens once at UTC-4 and again at UTC-5.

    Both are real, distinct instants, and both convert to 01:30 in New York -- so a window covering
    that hour is open across both of them. One extra hour of openness, once a year, in the widening
    direction. Named here because it is a consequence of converting an instant rather than reading
    a clock, and because nobody should have to rediscover it from a surprised run.
    """
    policy = windowed(tmp_path, WINDOWED.replace(WINDOW_TABLE, DOUBLED_HOUR))
    before = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    after = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)

    assert before.astimezone(NEW_YORK).utcoffset() != after.astimezone(NEW_YORK).utcoffset()
    assert before.astimezone(NEW_YORK).hour == after.astimezone(NEW_YORK).hour == 1
    assert policy.window_refusal(MACHINE, before) is None
    assert policy.window_refusal(MACHINE, after) is None


def test_the_hour_that_does_not_happen_is_open_for_no_time_at_all(tmp_path: Path) -> None:
    """2026-03-08: local time steps from 01:59:59 to 03:00:00, so 02:00-03:00 never occurs.

    A window confined to it is therefore shut all day -- the narrowing direction, which is the safe
    one to be surprised by, and work of that reach simply waits a day. Swept minute by minute over
    the whole UTC day rather than probed at a chosen instant, because a probe at the wrong instant
    would agree with a broken implementation.
    """
    policy = windowed(tmp_path, WINDOWED.replace(WINDOW_TABLE, MISSING_HOUR))
    transition = a_day_of_instants(datetime(2026, 3, 8, tzinfo=UTC), timedelta(minutes=1))
    ordinary = a_day_of_instants(datetime(2026, 3, 9, tzinfo=UTC), timedelta(minutes=1))

    assert all(policy.window_refusal(MACHINE, instant) for instant in transition)
    # The control on the very next day, when the same hour exists again and lasts exactly an hour.
    assert sum(policy.window_refusal(MACHINE, instant) is None for instant in ordinary) == 60


# ---------------------------------------------------------------------------------------------
# 4. The clock is given, never read
# ---------------------------------------------------------------------------------------------


def test_a_time_carrying_no_zone_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """Converting a naive time assumes the zone of whatever machine is running.

    That is the bug this design exists to make unwritable, so it fails loudly rather than answering
    about the wrong day.
    """
    policy = windowed(tmp_path)

    with pytest.raises(DomainError) as raised:
        policy.window_refusal(MACHINE, datetime(2026, 8, 12, 11))

    assert raised.value.code == "factory_policy_clock_invalid"
    assert policy.window_refusal(MACHINE, local(2026, 8, 12, 11)) is None  # control


def test_the_naive_clock_guard_fires_even_where_no_window_would_be_consulted(
    tmp_path: Path,
) -> None:
    # Before the early return, not after it. A guard that only fires on the paths that were going
    # to answer anyway has a hole in the shape of its cheapest case.
    policy = windowed(tmp_path)

    for reach in (None, (), NO_WINDOW, ("invented",)):
        with pytest.raises(DomainError):
            policy.window_refusal(reach, datetime(2026, 8, 12, 11))


def _wall_clock_reads(source: str) -> list[str]:
    """Calls through which `source` could read the time it was not given.

    Parsed rather than grepped: ``clock.now(session)`` is the injected reader and must not be
    confused with ``datetime.now()``, which is the thing being forbidden.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id in {"datetime", "date", "time"}:
            if node.func.attr in {"now", "utcnow", "today"}:
                found.append(f"{owner.id}.{node.func.attr}")
    return sorted(set(found))


def test_nothing_that_decides_a_window_reads_a_clock_it_was_not_given() -> None:
    """Structural, in production and in this file both.

    The production side cannot answer about a moment nobody handed it, and no test here depends on
    when it runs -- which is what stops these proofs passing on a fast runner and failing on a slow
    one, or passing all year and failing in November.
    """
    subjects = [
        Path("src/orchestrator/factory_policy.py"),
        Path("src/orchestrator/services/reach_admission.py"),
        Path(__file__),
    ]

    assert {
        str(path): _wall_clock_reads(path.read_text(encoding="utf-8")) for path in subjects
    } == {str(path): [] for path in subjects}


def test_the_wall_clock_detector_fires_on_a_module_that_does_read_one() -> None:
    # The control. Without it the assertion above is satisfied by a predicate that finds nothing.
    assert _wall_clock_reads("import datetime\nx = datetime.now()\ny = clock.now(s)\n") == [
        "datetime.now"
    ]


# ---------------------------------------------------------------------------------------------
# 5. A malformed window is loud, where an absent one is silent
# ---------------------------------------------------------------------------------------------

MALFORMED_WINDOW: tuple[tuple[str, str], ...] = (
    ("not a table", 'change_window = "09:00-17:00"\n'),
    ("a missing field", WINDOW_TABLE.replace('timezone = "America/New_York"\n', "")),
    ("an extra field", WINDOW_TABLE + 'days = ["monday"]\n'),
    ("an unknown timezone", WINDOW_TABLE.replace("America/New_York", "Mars/Olympus_Mons")),
    ("a timezone that is not a string", WINDOW_TABLE.replace('"America/New_York"', "-5")),
    ("an empty timezone", WINDOW_TABLE.replace('"America/New_York"', '"  "')),
    ("a start that is not a time", WINDOW_TABLE.replace('"09:00"', '"morning"')),
    ("a start carrying seconds", WINDOW_TABLE.replace('"09:00"', '"09:00:00"')),
    ("a start carrying an offset", WINDOW_TABLE.replace('"09:00"', '"09:00-05:00"')),
    ("a start with no leading zero", WINDOW_TABLE.replace('"09:00"', '"9:00"')),
    ("an hour that does not exist", WINDOW_TABLE.replace('"09:00"', '"25:00"')),
    ("a start equal to the end", WINDOW_TABLE.replace('"17:00"', '"09:00"')),
    (
        "an empty rationale",
        WINDOW_TABLE.replace(
            'rationale = "an ordinary daytime window, so the non-wrapping shape is tested apart"',
            'rationale = "  "',
        ),
    ),
    ("a decided value that is not a date", WINDOW_TABLE.replace('"2026-08-01"', '"tonight"')),
)


@pytest.mark.parametrize(
    ("label", "text"), MALFORMED_WINDOW, ids=[case[0] for case in MALFORMED_WINDOW]
)
def test_a_malformed_window_stops_the_document_loading(
    label: str, text: str, tmp_path: Path
) -> None:
    with pytest.raises(DomainError) as raised:
        windowed(tmp_path, WINDOWED.replace(WINDOW_TABLE, text))

    assert raised.value.code == "factory_policy_invalid", label
    assert raised.value.recovery is not None, label


def test_an_absent_window_and_a_broken_one_are_not_the_same_thing(tmp_path: Path) -> None:
    """§3.1: "no objection" must not be confusable with "could not be read".

    Deleting the table leaves a policy that loads and objects to nothing on window grounds.
    Breaking it leaves no policy at all, so nothing is admitted anywhere -- which is the same
    fail-closed halt a missing reach row produces, and for the same reason.
    """
    absent = windowed(tmp_path, WINDOWED.replace(WINDOW_TABLE, ""))

    assert absent.rows["operator_machine"].change_window is None
    assert absent.window_refusal(MACHINE, local(2026, 8, 12, 20)) is None

    with pytest.raises(DomainError):
        windowed(tmp_path, WINDOWED.replace('start = "09:00"', 'start = "elevenish"'))


def test_the_valid_fixture_is_the_control_for_every_malformation(tmp_path: Path) -> None:
    window = windowed(tmp_path).rows["operator_machine"].change_window

    assert window is not None
    assert (window.zone.key, str(window.start), str(window.end)) == (
        "America/New_York",
        "09:00:00",
        "17:00:00",
    )


# ---------------------------------------------------------------------------------------------
# 6. Through admission itself
# ---------------------------------------------------------------------------------------------

# Instants the SHIPPED artifact is open and shut at, for the units below. 03:00 and 12:00 in New
# York on a day with no transition on it, expressed as the UTC instants they are.
OPEN = datetime(2026, 8, 12, 7, tzinfo=UTC)
SHUT = datetime(2026, 8, 12, 16, tzinfo=UTC)


def test_out_of_window_work_is_refused_at_admission_and_in_window_work_is_not(
    migrated_session: Session,
) -> None:
    """The same unit, the same everything, two clocks.

    Nothing distinguishes these two calls except what time policy is told it is, which is what
    makes the pair a proof about the window rather than about the unit.
    """
    unit = ready_unit(migrated_session, key="window-both-ways", reach=["operator_machine"])
    github = FakeGitHubDispatcher([])

    refused = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), settings(), github, FrozenClock(SHUT)
    )
    admitted = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=2),
        settings(),
        github,
        FrozenClock(OPEN),
    )

    assert (refused.status, refused.reason_code) == ("skipped", OUTSIDE_CHANGE_WINDOW)
    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert len(github.calls) == 1


def test_a_window_refusal_is_recorded_as_skipped_not_as_a_unit_needing_attention(
    migrated_session: Session,
) -> None:
    # Alongside the off-switch, and for the same reason: nothing is wrong with the unit and nobody
    # has to act. Recording a nightly recurrence as `blocked` would put it in front of an operator
    # reading for units that need them.
    unit = ready_unit(migrated_session, key="window-is-skipped", reach=["live_estate"])

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        settings(),
        FakeGitHubDispatcher([]),
        FrozenClock(SHUT),
    )

    assert (record.status, record.failure_signature) == ("skipped", None)


def test_the_off_switch_outranks_the_window_and_the_window_outranks_nothing(
    migrated_session: Session,
) -> None:
    """§4.1, proven NON-VACUOUSLY: the same unit is admitted with the switch on.

    Without the first half, "nothing was sent" is satisfied by a unit nothing recognised, and the
    claim about precedence is empty. The third call is the direction that matters most: with the
    switch off AND the window shut, the reason recorded is the switch's, because policy is
    consulted after it and cannot be consulted before it -- it cannot see the setting at all.
    """
    unit = ready_unit(migrated_session, key="switch-outranks-window", reach=["operator_machine"])
    github = FakeGitHubDispatcher([])

    admitted = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), settings(), github, FrozenClock(OPEN)
    )
    off_in_window = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=2),
        settings(enabled=False),
        github,
        FrozenClock(OPEN),
    )
    off_out_of_window = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=3),
        settings(enabled=False),
        github,
        FrozenClock(SHUT),
    )

    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert (off_in_window.status, off_in_window.reason_code) == ("skipped", "dispatch_disabled")
    assert (off_out_of_window.status, off_out_of_window.reason_code) == (
        "skipped",
        "dispatch_disabled",
    )
    assert len(github.calls) == 1


def test_a_standing_defect_is_reported_ahead_of_the_self_clearing_one(
    migrated_session: Session,
) -> None:
    """Ordering, which is the whole reason the window is a term of its own.

    A unit that is both unapproved and out of hours has one thing somebody must do and one thing
    that will fix itself. Reporting the second would send an operator away to wait for a moment at
    which nothing had changed.
    """
    unit = ready_unit(migrated_session, key="standing-before-transient", reach=["operator_machine"])
    unit.authority_approval_id = None
    migrated_session.flush()
    github = FakeGitHubDispatcher([])

    unapproved = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), settings(), github, FrozenClock(SHUT)
    )

    approved = ready_unit(migrated_session, key="transient-only", reach=["operator_machine"])
    windowed_out = dispatch_work_unit(
        migrated_session, dispatch_command(approved.id), settings(), github, FrozenClock(SHUT)
    )

    assert (unapproved.status, unapproved.reason_code) == ("blocked", "authority_approval_missing")
    assert (windowed_out.status, windowed_out.reason_code) == ("skipped", OUTSIDE_CHANGE_WINDOW)


def test_an_unreadable_artifact_refuses_on_window_grounds_too(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both terms in this module fail toward refusing, and both report the same fault, because a
    # policy that cannot be read has objected to nothing and recognised nothing -- the one state in
    # which those two are not the same thing.
    unit = ready_unit(migrated_session, key="window-policy-unreadable", reach=["operator_machine"])
    revision = unit.work_package_revision_id

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr("orchestrator.services.reach_admission.load_factory_policy", unreadable)
    stored = migrated_session.get(WorkPackageRevision, revision)
    assert stored is not None

    assert change_window_refusal(migrated_session, stored) == REACH_POLICY_UNREADABLE


# ---------------------------------------------------------------------------------------------
# 7. A window governs admission, never execution
# ---------------------------------------------------------------------------------------------


def test_a_unit_already_running_survives_the_window_closing_over_it(
    migrated_session: Session,
) -> None:
    """§4.3, and it is not a nicety.

    The worker calls this orchestrator back at the END of its run, and the call that reports a
    failure fails the same way the call that reports success does. So stopping a live run at the
    window boundary would not stop it cleanly -- it would strand the unit in ``executing`` with its
    attempt spent, which is a documented outage shape rather than a hypothetical one.

    The control is what makes this discriminating: at the very same instant, admitting fresh work
    of the same reach IS refused. So the window really has shut, and the running unit really did
    finish through it.
    """
    unit = ready_unit(migrated_session, key="running-through-closing", reach=["operator_machine"])
    github = FakeGitHubDispatcher([])
    sent = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id), settings(), github, FrozenClock(OPEN)
    )
    grant = claim_unit(migrated_session, unit.id, WORKER, "running-claim")
    assert isinstance(grant, LeaseGrant), grant
    migrated_session.refresh(unit)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="running-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    migrated_session.refresh(unit)

    # The window shuts underneath it. Nothing about the running unit is consulted again.
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.SUBMITTED,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="running-submit",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    migrated_session.refresh(unit)

    fresh = ready_unit(migrated_session, key="fresh-while-shut", reach=["operator_machine"])
    refused = dispatch_work_unit(
        migrated_session, dispatch_command(fresh.id), settings(), github, FrozenClock(SHUT)
    )

    assert sent.status == "dispatched"
    assert unit.state == str(WorkUnitState.SUBMITTED)
    assert (refused.status, refused.reason_code) == ("skipped", OUTSIDE_CHANGE_WINDOW)


# ---------------------------------------------------------------------------------------------
# 8. What an operator can see
# ---------------------------------------------------------------------------------------------


def test_the_window_is_visible_in_the_report_and_declared_by_the_response_model() -> None:
    """A response model DROPS every key it does not declare, silently and in one direction.

    That is how WS-P2.12 served an empty enrichment while every service-level assertion passed, so
    the row's report and the row's model are pinned to each other rather than to two separate
    expectations.
    """
    policy = load_factory_policy()
    rows = {row["member"]: row for row in policy.report()["reach"]}
    declared = policy.rows["operator_machine"].change_window
    assert declared is not None

    assert set(FactoryPolicyReachResponse.model_fields) == set(rows["live_estate"])
    assert rows["source_repository"]["change_window"] is None
    assert rows["operator_machine"]["change_window"] == {
        "rationale": declared.rationale,
        "decided": declared.decided,
        # An offset would be true for half the year. The reason the zone is in the artifact at all
        # is that the question is about somebody's day, so the answer has to name the same thing.
        "timezone": "America/New_York",
        "start": "02:00",
        "end": "06:00",
    }
