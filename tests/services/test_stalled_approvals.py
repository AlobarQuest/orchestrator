"""A human approval gate nobody answered is REPORTED, and can never be RESOLVED by time.

WS-P2.15. The predecessor of this feature (`age_out_human_gates`) was fully implemented, fully
unit-tested, disabled by default, and had ZERO production callers -- a fourth instance of the
defect this workstream exists to eliminate. It was deleted rather than wired: a report has no
business being a write, and its blocked DispatchRecord could not be given a legal
`runner_attempt` (0 violates a CHECK constraint; anything else silently consumes a dispatch
slot).

What replaced it is a derived read over `(state, updated_at)` -- no route, no command, no
migration, no writer. These tests pin the two properties that matter:

  1. The report cannot be switched off. Its predecessor's threshold was `int | None = None`, and
     that None is exactly why it stayed invisible for a whole workstream.
  2. The report never transitions anything. Silence is never approval.
"""

import pytest
from sqlalchemy.orm import Session

from orchestrator.config import Settings
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import WorkUnit
from orchestrator.services.dead_letter import dead_letter
from tests.services.test_dependencies import register_unit

THRESHOLD = 3
WEEK = 604_800
# A zero window makes a just-parked unit already overdue -- see _park_in on why the row
# cannot be aged instead.
STALE_NOW = 0


def _park_in(session: Session, state: WorkUnitState, *, key: str) -> WorkUnit:
    """Put a unit into an approval state. Fixture setup, not runtime behaviour.

    NOTE: the unit CANNOT be aged by writing `updated_at`. A database trigger
    (`set_work_unit_updated_at`, migration 0001) sets `NEW.updated_at = now()` on EVERY update
    of `work_units`, so a back-dated timestamp is silently overwritten. That is also why
    `updated_at` is the right column to key the report on: nothing but a real state change
    touches the row, so for a unit parked in an approval state it IS "when it entered".

    So staleness is exercised by shrinking the THRESHOLD, not by ageing the row -- the same
    trick `reconcile_split_brain_stall_seconds` uses so its drill needs no sleep.
    """
    unit = register_unit(session, key=key)
    persisted = session.get(WorkUnit, unit.id)
    assert persisted is not None
    persisted.state = state.value
    session.commit()
    session.expire_all()
    return persisted


@pytest.mark.parametrize(
    "state",
    [WorkUnitState.AWAITING_APPROVAL, WorkUnitState.AWAITING_REVIEW],
    ids=lambda s: s.value,
)
def test_an_unanswered_approval_gate_past_the_threshold_is_reported(
    migrated_session: Session, state: WorkUnitState
) -> None:
    unit = _park_in(migrated_session, state, key=f"stalled-{state.value}")

    entries = dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALE_NOW,
        stalled_verification_seconds=604_800,
    )

    stalled = [entry for entry in entries if entry.source == "stalled_approval"]
    assert [entry.work_unit_id for entry in stalled] == [unit.id]
    assert stalled[0].unit_state == state.value
    assert stalled[0].reason_code == "approval_unanswered"


def test_a_gate_answered_within_the_threshold_is_not_reported(migrated_session: Session) -> None:
    _park_in(migrated_session, WorkUnitState.AWAITING_APPROVAL, key="fresh-gate")

    entries = dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=WEEK,
        stalled_verification_seconds=604_800,
    )

    assert [entry for entry in entries if entry.source == "stalled_approval"] == []


def test_a_stalled_gate_is_not_requeue_eligible(migrated_session: Session) -> None:
    """It needs a human DECISION, not a retry. Requeue would be the wrong affordance to offer."""
    _park_in(migrated_session, WorkUnitState.AWAITING_REVIEW, key="stalled-not-requeueable")

    entries = dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALE_NOW,
        stalled_verification_seconds=604_800,
    )

    stalled = [entry for entry in entries if entry.source == "stalled_approval"]
    assert stalled and not any(entry.requeue_eligible for entry in stalled)


def test_reporting_a_stalled_gate_transitions_nothing(migrated_session: Session) -> None:
    """SILENCE IS NEVER APPROVAL.

    Asserted by re-reading from the database, not by inspecting the object the call returned --
    the WS-P2.1 defect was a writer whose in-session object looked right while the row was
    discarded. Here there is no writer at all, and this proves it.
    """
    unit = _park_in(
        migrated_session, WorkUnitState.AWAITING_APPROVAL, key="untouched-by-the-report"
    )
    version_before = unit.version

    dead_letter(
        migrated_session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=STALE_NOW,
        stalled_verification_seconds=604_800,
    )

    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert refreshed.state == WorkUnitState.AWAITING_APPROVAL.value
    assert refreshed.version == version_before


def test_the_stalled_approval_threshold_has_no_off_switch() -> None:
    """The invisibility control.

    `age_out_human_gates` was configured by `dispatch_human_gate_age_out_seconds: int | None =
    None`, and returned () immediately when it was None. It was None in production. That is the
    whole reason a fully-implemented, fully-tested guard reported nothing for an entire
    workstream. A reporting obligation that can be switched off is one that will be.
    """
    default = Settings.model_fields["dead_letter_stalled_approval_seconds"]

    assert default.default is not None
    assert default.default > 0
    assert Settings.model_fields.get("dispatch_human_gate_age_out_seconds") is None, (
        "the nullable age-out knob is back; it is the mechanism by which this guard "
        "previously went silent"
    )


def test_the_deleted_config_knob_cannot_fail_startup_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a config field must not brick the next deploy.

    Production Coolify may still carry `ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS` from
    before WS-P2.15 deleted the field it fed. If Settings rejected unknown environment variables,
    the very next deploy would fail closed and take production down -- an outage caused by a
    DELETION, which is the last place anyone would look for it.

    It does not, because pydantic-settings ignores extras. That is load-bearing, so it is pinned
    here rather than left as a happy accident.
    """
    monkeypatch.setenv("ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS", "3600")

    settings = Settings(database_url="postgresql://unused/unused")

    assert not hasattr(settings, "dispatch_human_gate_age_out_seconds")
    assert settings.dead_letter_stalled_approval_seconds > 0
