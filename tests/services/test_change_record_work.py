"""What a change record caused, and whether it is done (ADR-0029).

THE RULE IS THE SUBJECT OF THIS FILE, not the plumbing. A record names a package revision, a
revision may decompose into more than one unit, and a package may be revised — so "is the work
done?" has several plausible answers and the wrong one retires a record whose work is still live.
The rule is: **at least one unit, and every one of them completed.**

Each clause gets a case that fails if the clause is dropped, and the neighbouring unit states get
cases of their own, because `failed` and `cancelled` are the two that a looser rule would swallow:
a failed unit may be granted another attempt, and a cancelled one is a decision this lane reserves
for a person.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.services.change_record_work import work_for_change_record
from orchestrator.services.packages import register_approved_unit, register_revision
from tests.services.test_package_registration import AUTHORITY, NOW

RECORD = 61
OTHER_RECORD = 62


def _revision(session: Session, *, change_record_id: int | None, revision: int = 1):
    """A registered revision carrying (or not carrying) an originating change record.

    `change_record_id` is a REGISTRATION parameter rather than something a test can set later:
    `work_package_revisions` is append-only at the database, so a revision that was registered
    without a cause can never be given one. That is the production property too.
    """
    suffix = f"{change_record_id}-{revision}"
    return register_revision(
        session,
        package_id=f"pkg-{suffix}",
        source_repository="owner/repo",
        revision=revision,
        content_hash=f"sha256:{suffix}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        change_record_id=change_record_id,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _unit(session: Session, revision, key: str, state: str):
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.state = state
    session.commit()
    return unit


def test_a_record_nothing_has_carried_is_not_complete(migrated_session: Session) -> None:
    """THE clause that matters most, and the one a naive rule gets wrong.

    `all()` over an empty sequence is True, so a verdict written as "every unit is completed"
    without the emptiness clause reports every record the carry has not reached yet as DONE — and
    that is the entire approved queue this lane exists to serve. It would retire the work before
    anybody built it.
    """
    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is False
    assert answer.revision_ids == ()
    assert answer.units == ()


def test_a_carried_record_awaiting_a_breakdown_is_not_complete(migrated_session: Session) -> None:
    """An intake exists and no unit does. Distinguishable from the case above only by the
    revisions, which is why they are reported rather than counted."""
    revision = _revision(migrated_session, change_record_id=RECORD)

    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is False
    assert answer.revision_ids == (revision.id,)
    assert answer.units == ()


def test_a_record_whose_only_unit_completed_is_complete(migrated_session: Session) -> None:
    revision = _revision(migrated_session, change_record_id=RECORD)
    unit = _unit(migrated_session, revision, "only", "completed")

    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is True
    assert [u.unit_id for u in answer.units] == [unit.id]
    assert [u.state for u in answer.units] == ["completed"]
    assert [u.revision_id for u in answer.units] == [revision.id]


def test_a_unit_still_in_flight_holds_the_record_open(migrated_session: Session) -> None:
    revision = _revision(migrated_session, change_record_id=RECORD)
    _unit(migrated_session, revision, "a-done", "completed")
    _unit(migrated_session, revision, "b-running", "executing")

    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is False
    assert sorted(u.state for u in answer.units) == ["completed", "executing"]


def test_a_failed_unit_holds_the_record_open_because_it_may_be_retried(
    migrated_session: Session,
) -> None:
    """`failed` is not terminal for this question. A rule keyed on "no longer in flight" would
    retire a record whose work can still be granted another attempt."""
    revision = _revision(migrated_session, change_record_id=RECORD)
    _unit(migrated_session, revision, "a-done", "completed")
    _unit(migrated_session, revision, "b-failed", "failed")

    assert work_for_change_record(migrated_session, RECORD).all_units_completed is False


def test_a_cancelled_unit_holds_the_record_open_for_a_person(migrated_session: Session) -> None:
    """A cancellation is a human's decision, and so is the matching record decision. The machine
    must not convert one into the other."""
    revision = _revision(migrated_session, change_record_id=RECORD)
    _unit(migrated_session, revision, "a-done", "completed")
    _unit(migrated_session, revision, "b-cancelled", "cancelled")

    assert work_for_change_record(migrated_session, RECORD).all_units_completed is False


def test_every_revision_of_a_record_must_be_complete(migrated_session: Session) -> None:
    """Nothing constrains a record to ONE revision — a package can be revised, and each revision
    carries the originating reference forward. Requiring all of them is the fail-closed reading.

    Without the second revision this case is indistinguishable from the single-revision one, so a
    rule that looked at only the first revision found would pass every other test in this file.
    """
    first = _revision(migrated_session, change_record_id=RECORD, revision=1)
    second = _revision(migrated_session, change_record_id=RECORD, revision=2)
    _unit(migrated_session, first, "a-done", "completed")
    _unit(migrated_session, second, "b-running", "executing")

    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is False
    assert set(answer.revision_ids) == {first.id, second.id}
    assert len(answer.units) == 2


def test_two_revisions_both_complete_are_complete(migrated_session: Session) -> None:
    """The positive control for the case above. Without it a rule that always answered False for
    more than one revision would pass, and the record would never retire."""
    first = _revision(migrated_session, change_record_id=RECORD, revision=1)
    second = _revision(migrated_session, change_record_id=RECORD, revision=2)
    _unit(migrated_session, first, "a-done", "completed")
    _unit(migrated_session, second, "b-done", "completed")

    assert work_for_change_record(migrated_session, RECORD).all_units_completed is True


def test_another_records_work_is_not_counted(migrated_session: Session) -> None:
    """The join is on the change record, and a query that dropped the filter would report every
    record complete the moment any record's work was."""
    mine = _revision(migrated_session, change_record_id=RECORD)
    theirs = _revision(migrated_session, change_record_id=OTHER_RECORD)
    _unit(migrated_session, mine, "mine-running", "executing")
    _unit(migrated_session, theirs, "theirs-done", "completed")

    mine_answer = work_for_change_record(migrated_session, RECORD)
    theirs_answer = work_for_change_record(migrated_session, OTHER_RECORD)

    assert mine_answer.all_units_completed is False
    assert theirs_answer.all_units_completed is True
    assert [u.unit_key for u in theirs_answer.units] == ["theirs-done"]


def test_a_revision_with_no_cause_is_never_matched(migrated_session: Session) -> None:
    """`change_record_id` is NULL for every revision registered before the column and every one a
    human intakes without a cause. NULL means "nothing recorded a cause", never "no cause exists",
    so such a revision must not be swept up by a record it has nothing to do with."""
    causeless = _revision(migrated_session, change_record_id=None)
    _unit(migrated_session, causeless, "orphan", "completed")

    answer = work_for_change_record(migrated_session, RECORD)

    assert answer.all_units_completed is False
    assert answer.revision_ids == ()
