"""ADR-0031: a supervised act may start outside the hours policy declares.

Every proof here is a PAIR. A single out-of-hours assertion passes for the wrong reason whenever
the real clock also happens to be outside the window -- which is most of the day -- so a term that
ignored its injected clock, or an override that removed the term instead of switching it off,
would look correct except in the hours that matter. The control beside each case is the same
subject at the same instant without the override, and it is what makes this a switch rather than a
removal.

Two acts, two sections, and the third section is the one the whole design turns on: neither act's
override is the other's. Starting a run produces a pull request that changes nothing outside a
repository; landing it changes what is already serving.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from sqlalchemy.orm import Session

import orchestrator.services.pr_merge_admission as admission_module
from orchestrator.change_window_override import (
    REASON_REQUIRED,
    ChangeWindowOverride,
    override_record,
    suppressed,
)
from orchestrator.errors import DomainError
from orchestrator.factory_policy import OUTSIDE_CHANGE_WINDOW, load_factory_policy
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import DispatchRecord, Event, WorkUnit
from orchestrator.reach_vocabulary import LIVE_ESTATE
from orchestrator.services.dispatch import (
    DispatchCommand,
    dispatch_work_unit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_merge import MergeCommand, land_unit_pull_request
from orchestrator.services.pr_merge_admission import (
    MERGE_CHANGE_WINDOW_NOT_DECLARED,
    MERGE_OUTSIDE_CHANGE_WINDOW,
    MERGE_POLICY_UNREADABLE,
    admission_for,
)
from orchestrator.services.reach_admission import REACH_POLICY_UNREADABLE
from tests.services.change_record_doubles import approved_record_source
from tests.services.estate_doubles import inert_source, redeploying_source
from tests.services.test_change_window import OPEN, SHUT, FrozenClock
from tests.services.test_dispatch import FakeGitHubDispatcher, ready_unit, settings
from tests.services.test_pr_merge import (
    IN_WINDOW,
    OUT_OF_WINDOW,
    FakeGateway,
    FixedClock,
)
from tests.services.test_pr_merge_admission import (
    TARGET,
    _ready_unit,
    _revision,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
REASON = "supervised build session, 2026-08-26"


def _override(reason: str = REASON) -> ChangeWindowOverride:
    return ChangeWindowOverride(reason=reason)


def _start(
    session: Session,
    unit: WorkUnit,
    *,
    attempt: int,
    clock: FrozenClock,
    override: ChangeWindowOverride | None = None,
    github: FakeGitHubDispatcher | None = None,
    dispatch_settings=None,
) -> DispatchRecord:
    return dispatch_work_unit(
        session,
        DispatchCommand(
            unit_id=unit.id,
            runner_attempt=attempt,
            actor=SYSTEM,
            # A fresh ordinal is not enough on its own: a reused key returns the EXISTING record at
            # a status that reads like success, so every call here mints both.
            idempotency_key=f"override:{unit.id}:{attempt}",
            change_window_override=override,
        ),
        dispatch_settings or settings(),
        github or FakeGitHubDispatcher([]),
        inert_source(),
        clock,
    )


def _recorded(record: DispatchRecord) -> dict[str, object] | None:
    payload = record.payload or {}
    stored = payload.get("change_window_override")
    return stored if isinstance(stored, dict) else None


def _event_override(session: Session, record: DispatchRecord) -> dict[str, object] | None:
    event = session.get(Event, record.event_id)
    assert event is not None
    stored = (event.payload or {}).get("change_window_override")
    return stored if isinstance(stored, dict) else None


# ---------------------------------------------------------------------------------------------
# 1. The reason, which is the whole of what makes the record worth reading later
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_an_override_with_nothing_to_say_cannot_be_built(reason: str) -> None:
    """The invalid shape is unrepresentable rather than refused at one entry point.

    A guard in the services would be a second rule set over a type that could still be built
    wrong, and -- because both call sites construct this from a request body before anything else
    happens -- a branch no test could reach. Raising at construction fires for every caller.
    """
    with pytest.raises(DomainError) as raised:
        ChangeWindowOverride(reason=reason)

    assert raised.value.code == REASON_REQUIRED


def test_a_stated_reason_is_kept_verbatim() -> None:
    """The control for the case above. Without it, a constructor that refused everything would
    satisfy the refusal tests and admit nothing."""
    assert _override().reason == REASON


def test_the_suppression_is_keyed_on_the_hour_refusal_and_on_no_other() -> None:
    """The predicate both acts share, exercised at the layer that holds it.

    The window term also reports a policy artifact this process could not read, and -- on the
    landing act -- one that declares no hours at all. Both are faults somebody has to fix here,
    and an operator saying a run is watched has answered neither.
    """
    assert suppressed(OUTSIDE_CHANGE_WINDOW, _override()) == (None, True)
    assert suppressed(OUTSIDE_CHANGE_WINDOW, None) == (OUTSIDE_CHANGE_WINDOW, False)
    assert suppressed(REACH_POLICY_UNREADABLE, _override()) == (REACH_POLICY_UNREADABLE, False)
    assert suppressed(None, _override()) == (None, False)


def test_the_record_reaches_the_human_approval_it_rests_on() -> None:
    """Attribution is INHERITED, so the record has to make the inheritance legible on its own."""
    approval = uuid.uuid4()

    assert override_record(
        _override(),
        applied=True,
        authority_approval_id=approval,
        authority_fingerprint="fp-1",
    ) == {
        "reason": REASON,
        "applied": True,
        "authority_approval_id": str(approval),
        "authority_fingerprint": "fp-1",
    }
    assert (
        override_record(None, applied=False, authority_approval_id=None, authority_fingerprint=None)
        is None
    )


# ---------------------------------------------------------------------------------------------
# 2. Starting a run
# ---------------------------------------------------------------------------------------------


def test_a_supervised_run_starts_outside_the_window_and_one_without_the_override_does_not(
    migrated_session: Session,
) -> None:
    """Acceptance 1 and 2, as one pair.

    The same unit, the same instant, two calls differing in nothing but the override. Separating
    them would leave each half provable by an accident of the hour; together they can only pass if
    the override is what changed the answer.
    """
    unit = ready_unit(migrated_session, key="supervised-start", reach=["operator_machine"])
    github = FakeGitHubDispatcher([])

    supervised = _start(
        migrated_session,
        unit,
        attempt=1,
        clock=FrozenClock(SHUT),
        override=_override(),
        github=github,
    )
    unsupervised = _start(migrated_session, unit, attempt=2, clock=FrozenClock(SHUT), github=github)

    assert (supervised.status, supervised.reason_code) == ("dispatched", None)
    assert (unsupervised.status, unsupervised.reason_code) == ("skipped", OUTSIDE_CHANGE_WINDOW)
    assert supervised.id != unsupervised.id
    assert len(github.calls) == 1


def test_the_record_of_a_supervised_run_says_why_it_started_when_it_did(
    migrated_session: Session,
) -> None:
    """Acceptance 1 and 6's substrate: recorded, not merely honoured."""
    unit = ready_unit(migrated_session, key="supervised-recorded", reach=["operator_machine"])

    record = _start(
        migrated_session, unit, attempt=1, clock=FrozenClock(SHUT), override=_override()
    )

    stored = _recorded(record)
    assert stored is not None
    assert stored["reason"] == REASON
    assert stored["applied"] is True
    assert stored["authority_approval_id"] == str(unit.authority_approval_id)
    assert stored["authority_fingerprint"] == unit.authority_fingerprint
    assert _event_override(migrated_session, record) == stored


def test_the_record_is_readable_from_another_session(
    migrated_session: Session, migrated_engine
) -> None:
    """Re-read through a DIFFERENT session -- the only reader that cannot see an uncommitted
    write, and the check `expire_all` provably does not perform."""
    unit = ready_unit(migrated_session, key="supervised-persisted", reach=["operator_machine"])
    record_id = _start(
        migrated_session, unit, attempt=1, clock=FrozenClock(SHUT), override=_override()
    ).id

    with Session(migrated_engine) as reader:
        stored = reader.get(DispatchRecord, record_id)
        assert stored is not None
        assert (stored.payload or {})["change_window_override"]["reason"] == REASON


def test_an_override_carried_inside_the_window_suppressed_nothing_and_says_so(
    migrated_session: Session,
) -> None:
    """Carried is not applied. A run inside the declared hours needed no override, and recording
    one as though it had done something would assert a suppression that never happened."""
    unit = ready_unit(migrated_session, key="carried-not-applied", reach=["operator_machine"])

    record = _start(
        migrated_session, unit, attempt=1, clock=FrozenClock(OPEN), override=_override()
    )

    stored = _recorded(record)
    assert record.status == "dispatched"
    assert stored is not None
    assert (stored["reason"], stored["applied"]) == (REASON, False)


def test_a_run_carrying_no_override_records_none(migrated_session: Session) -> None:
    """The control for every payload assertion above: the key is absent-valued rather than
    always populated, so a reader can tell an override apart from a default."""
    unit = ready_unit(migrated_session, key="no-override-recorded", reach=["operator_machine"])

    record = _start(migrated_session, unit, attempt=1, clock=FrozenClock(OPEN))

    assert _recorded(record) is None
    assert _event_override(migrated_session, record) is None


def test_an_override_does_not_admit_work_whose_policy_could_not_be_read(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sharpest of the not-suppressed cases: the same term reports it, and it is a fault in
    this process rather than a statement about the hour."""
    unit = ready_unit(migrated_session, key="override-unreadable", reach=["operator_machine"])

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr("orchestrator.services.reach_admission.load_factory_policy", unreadable)
    github = FakeGitHubDispatcher([])

    record = _start(
        migrated_session,
        unit,
        attempt=1,
        clock=FrozenClock(OPEN),
        override=_override(),
        github=github,
    )

    assert record.reason_code == REACH_POLICY_UNREADABLE
    assert github.calls == []
    stored = _recorded(record)
    assert stored is not None
    assert stored["applied"] is False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"enabled": False}, "dispatch_disabled"),
        ({"allowed_target_repositories": frozenset()}, "target_repository_not_allowed"),
        ({"enabled_capabilities": frozenset()}, "capability_not_enabled"),
        ({"allowed_change_classes": frozenset()}, "change_class_not_allowed"),
        ({"github_app_configured": False}, "github_app_credentials_missing"),
    ],
)
def test_no_other_refusal_is_suppressed_while_an_override_is_carried(
    migrated_session: Session, overrides: dict[str, object], expected: str
) -> None:
    """Acceptance 5. It means "this term does not apply to a supervised run", never "skip
    admission"."""
    key = f"other-terms-{expected}"
    unit = ready_unit(migrated_session, key=key, reach=["operator_machine"])
    github = FakeGitHubDispatcher([])

    record = _start(
        migrated_session,
        unit,
        attempt=1,
        clock=FrozenClock(SHUT),
        override=_override(),
        github=github,
        dispatch_settings=settings(**overrides),
    )

    stored = _recorded(record)
    assert record.reason_code == expected
    assert github.calls == []
    # The three envelope terms are answered in the SAME expression as the window, so this is
    # where "applied" can most easily become a lie: the window would have refused this instant,
    # and the override would have suppressed it, but the envelope refused first and the window's
    # answer was never the reason for anything.
    assert stored is not None
    assert (stored["reason"], stored["applied"]) == (REASON, False)


def test_a_term_ordered_above_the_window_leaves_the_override_carried_and_unapplied(
    migrated_session: Session,
) -> None:
    """A unit refused before the window was ever consulted. The override cannot have suppressed
    an answer nobody asked for, and the record must not claim it did."""
    unit = ready_unit(migrated_session, key="above-the-window", reach=["operator_machine"])
    unit.authority_approval_id = None
    migrated_session.flush()

    record = _start(
        migrated_session, unit, attempt=1, clock=FrozenClock(SHUT), override=_override()
    )

    stored = _recorded(record)
    assert record.reason_code == "authority_approval_missing"
    assert stored is not None
    assert (stored["reason"], stored["applied"]) == (REASON, False)


# ---------------------------------------------------------------------------------------------
# 3. Landing a pull request
# ---------------------------------------------------------------------------------------------


def _landing_answer(
    session: Session,
    unit: WorkUnit,
    *,
    clock: FixedClock,
    override: ChangeWindowOverride | None = None,
):
    return admission_for(
        session,
        unit,
        _revision(session, unit),
        redeploying_source(),
        approved_record_source(TARGET, 7),
        clock,
        override,
    )


def test_a_supervised_landing_is_admitted_outside_the_window_and_one_without_it_is_not(
    migrated_session: Session,
) -> None:
    """The landing act's half of acceptance 1 and 2, as one pair, for the same reason."""
    unit = _ready_unit(migrated_session, "supervised-landing")

    supervised = _landing_answer(
        migrated_session, unit, clock=FixedClock(OUT_OF_WINDOW), override=_override()
    )
    unsupervised = _landing_answer(migrated_session, unit, clock=FixedClock(OUT_OF_WINDOW))

    assert supervised.satisfied is True
    assert supervised.refusals == ()
    assert unsupervised.satisfied is False
    assert unsupervised.refusals == (MERGE_OUTSIDE_CHANGE_WINDOW,)


def test_a_supervised_landing_happens_and_the_event_says_why(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "supervised-landing-act")
    gateway = FakeGateway()

    record = land_unit_pull_request(
        migrated_session,
        MergeCommand(
            unit_id=unit.id,
            actor=SYSTEM,
            idempotency_key="supervised-landing-act",
            expected_version=unit.version,
            change_window_override=_override(),
        ),
        gateway,
        redeploying_source(),
        approved_record_source(TARGET, 7),
        clock=FixedClock(OUT_OF_WINDOW),
    )

    event = migrated_session.get(Event, record.event_id)
    assert record.status == "merged"
    assert len(gateway.merges) == 1
    assert event is not None
    stored = (event.payload or {})["change_window_override"]
    assert stored["reason"] == REASON
    assert stored["applied"] is True
    assert stored["authority_fingerprint"] == unit.authority_fingerprint


def test_a_landing_the_estate_says_changes_nothing_serving_records_the_override_unapplied(
    migrated_session: Session,
) -> None:
    """An inert repository never reaches the window term, so the override suppressed nothing --
    at an hour at which it would have, had the term been asked."""
    unit = _ready_unit(migrated_session, "inert-landing")
    gateway = FakeGateway()

    record = land_unit_pull_request(
        migrated_session,
        MergeCommand(
            unit_id=unit.id,
            actor=SYSTEM,
            idempotency_key="inert-landing",
            expected_version=unit.version,
            change_window_override=_override(),
        ),
        gateway,
        inert_source(),
        approved_record_source(TARGET, 7),
        clock=FixedClock(OUT_OF_WINDOW),
    )

    event = migrated_session.get(Event, record.event_id)
    assert record.status == "merged"
    assert event is not None
    assert (event.payload or {})["change_window_override"]["applied"] is False


def test_a_landing_override_does_not_suppress_an_undeclared_window(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting or renaming the row is a fault somebody has to fix, and a run being watched says
    nothing about it. Suppressing this would admit at any hour with the document still loading."""
    unit = _ready_unit(migrated_session, "landing-window-undeclared")
    policy = load_factory_policy()
    row = policy.rows[LIVE_ESTATE]
    windowless = dataclasses.replace(
        policy, rows={**policy.rows, LIVE_ESTATE: dataclasses.replace(row, change_window=None)}
    )
    monkeypatch.setattr(admission_module, "load_factory_policy", lambda: windowless)

    answer = _landing_answer(
        migrated_session, unit, clock=FixedClock(IN_WINDOW), override=_override()
    )

    assert answer.satisfied is False
    assert answer.refusals == (MERGE_CHANGE_WINDOW_NOT_DECLARED,)


def test_a_landing_override_does_not_suppress_an_unreadable_policy(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _ready_unit(migrated_session, "landing-policy-unreadable")

    def broken() -> object:
        raise DomainError("factory_policy_invalid", "the artifact is invalid", None)

    monkeypatch.setattr(admission_module, "load_factory_policy", broken)

    answer = _landing_answer(
        migrated_session, unit, clock=FixedClock(IN_WINDOW), override=_override()
    )

    assert answer.satisfied is False
    assert answer.refusals == (MERGE_POLICY_UNREADABLE,)


def test_a_landing_override_suppresses_no_other_term(migrated_session: Session) -> None:
    """Acceptance 5 on the landing act: the change record is still required, and still at an hour
    the window would otherwise have refused."""
    unit = _ready_unit(migrated_session, "landing-other-terms")

    answer = admission_for(
        migrated_session,
        unit,
        _revision(migrated_session, unit),
        redeploying_source(),
        approved_record_source("owner/other", 99),
        FixedClock(OUT_OF_WINDOW),
        _override(),
    )

    assert answer.satisfied is False
    assert answer.refusals == ("change_record_absent",)


# ---------------------------------------------------------------------------------------------
# 4. Neither act's override is the other's
# ---------------------------------------------------------------------------------------------


def test_a_recorded_start_override_grants_nothing_to_landing_the_pull_request(
    migrated_session: Session,
) -> None:
    """Acceptance 3, and the load-bearing claim of the whole design.

    The subject carries a real record from the other act, written by the production path and
    holding an override in its payload -- which is exactly the state a later reader would be
    tempted to consult. The landing answer is asked with none of its own, at an hour the window
    refuses, and it must still refuse. A run produced a pull request that changed nothing outside
    a repository; landing it changes what is already serving, and the second decision has not
    been made.
    """
    unit = _ready_unit(migrated_session, "start-grants-nothing")
    started = dispatch_work_unit(
        migrated_session,
        DispatchCommand(
            unit_id=unit.id,
            runner_attempt=unit.attempt_count + 1,
            actor=SYSTEM,
            idempotency_key=f"start-grants-nothing:{unit.id}",
            change_window_override=_override(),
        ),
        settings(),
        FakeGitHubDispatcher([]),
        inert_source(),
        FrozenClock(SHUT),
    )
    assert _recorded(started) is not None

    answer = _landing_answer(migrated_session, unit, clock=FixedClock(OUT_OF_WINDOW))

    assert answer.satisfied is False
    assert answer.refusals == (MERGE_OUTSIDE_CHANGE_WINDOW,)


def test_a_landing_override_is_read_from_this_call_and_admits_the_same_subject(
    migrated_session: Session,
) -> None:
    """The control for the case above: the same unit at the same instant IS admitted once the
    landing act is given an override of its own. Without this, the refusal above is satisfiable
    by a subject nothing would ever admit."""
    unit = _ready_unit(migrated_session, "landing-grants-itself")

    answer = _landing_answer(
        migrated_session, unit, clock=FixedClock(OUT_OF_WINDOW), override=_override()
    )

    assert answer.satisfied is True


def test_the_two_acts_read_their_own_clocks_and_their_own_overrides(
    migrated_session: Session,
) -> None:
    """Both directions on both acts, in one place, because a term that ignored either input would
    otherwise be caught only during the hours the window is shut."""
    unit = ready_unit(migrated_session, key="both-acts-both-ways", reach=["operator_machine"])
    landable = _ready_unit(migrated_session, "both-acts-landable")
    github = FakeGitHubDispatcher([])

    started_in = _start(migrated_session, unit, attempt=1, clock=FrozenClock(OPEN), github=github)
    started_out = _start(migrated_session, unit, attempt=2, clock=FrozenClock(SHUT), github=github)
    landing_in = _landing_answer(migrated_session, landable, clock=FixedClock(IN_WINDOW))
    landing_out = _landing_answer(migrated_session, landable, clock=FixedClock(OUT_OF_WINDOW))

    assert started_in.status == "dispatched"
    assert (started_out.status, started_out.reason_code) == ("skipped", OUTSIDE_CHANGE_WINDOW)
    assert landing_in.satisfied is True
    assert landing_out.satisfied is False
