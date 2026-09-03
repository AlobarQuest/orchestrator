"""A unit that submitted and was never verified is REPORTED.

Found 2026-09-03 by asking production what was in flight: work unit `98d07af9`
(`infraops-mcp-server-ac-001`) had been sitting in `submitted` for FIFTEEN DAYS with a pull
request whose checks had failed the whole time, and **nothing reported it.** Both surfaces that
could have were checked at source rather than recalled:

  * `dead_letter._stalled_approvals` keys on `APPROVAL_STATES = ("awaiting_approval",
    "awaiting_review")`. `submitted` is not in it -- WS-P2.15 widened the view to cover the gates
    a HUMAN owes, and a verifier-owed state was never in that scope.
  * `reconciliation_detection._detect_stalled_verifications` DOES key on SUBMITTED, but it joins
    `DeploymentObservation.post_deploy_work_unit_id`. It therefore sees a submitted unit only when
    that unit is a post-deploy verification unit attached to a deployment observation. An ordinary
    implementation unit is excluded by the join.

Neither is a decision: no ADR covers it, and the dead-letter docstring's reasoning is explicitly
about approval gates ("the gates a human must answer"). It was a hole in a state set, which is the
same family as the nullable threshold that silenced `age_out_human_gates` for a whole workstream.

These tests pin the properties that matter, in the shape `test_stalled_approvals.py` set.
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
# A zero window makes a just-parked unit already overdue. The row CANNOT be aged instead: a
# database trigger (`set_work_unit_updated_at`, migration 0001) rewrites `updated_at` on every
# update of `work_units`, so a back-dated timestamp is silently overwritten -- which is also why
# `updated_at` is the right column to key on, since nothing but a real state change touches the
# row.
STALE_NOW = 0


def _park_in(session: Session, state: WorkUnitState, *, key: str) -> WorkUnit:
    """Put a unit into a state. Fixture setup, not runtime behaviour."""
    unit = register_unit(session, key=key)
    persisted = session.get(WorkUnit, unit.id)
    assert persisted is not None
    persisted.state = state.value
    session.commit()
    session.expire_all()
    return persisted


def _call(session: Session, *, verification_seconds: int, approval_seconds: int = WEEK):
    return dead_letter(
        session,
        failure_signature_threshold=THRESHOLD,
        stalled_approval_seconds=approval_seconds,
        stalled_verification_seconds=verification_seconds,
    )


@pytest.mark.parametrize(
    "state",
    [WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING],
    ids=lambda s: s.value,
)
def test_a_unit_awaiting_a_verifier_past_the_threshold_is_reported(
    migrated_session: Session, state: WorkUnitState
) -> None:
    """The live specimen's shape: submitted, nobody verified, no report anywhere."""
    unit = _park_in(migrated_session, state, key=f"unverified-{state.value}")

    entries = _call(migrated_session, verification_seconds=STALE_NOW)

    stalled = [entry for entry in entries if entry.source == "stalled_verification"]
    assert [entry.work_unit_id for entry in stalled] == [unit.id]
    assert stalled[0].unit_state == state.value
    assert stalled[0].reason_code == "verification_undecided"


def test_a_unit_submitted_within_the_threshold_is_not_reported(migrated_session: Session) -> None:
    """The discriminating half. Without it the report is "every submitted unit", which is noise.

    A submit is normally verified within minutes, so the common case must stay quiet.
    """
    _park_in(migrated_session, WorkUnitState.SUBMITTED, key="freshly-submitted")

    entries = _call(migrated_session, verification_seconds=WEEK)

    assert [entry for entry in entries if entry.source == "stalled_verification"] == []


def test_a_stalled_verification_is_not_requeue_eligible(migrated_session: Session) -> None:
    """It needs the VERIFIER driven, not a retry -- a different remedy from an approval gate.

    Requeue targets `failed`/`blocked`, so this falls out of the existing predicate for free.
    Pinned anyway: offering requeue here would be the wrong affordance, and "for free" is exactly
    the kind of property a later refactor silently changes.
    """
    _park_in(migrated_session, WorkUnitState.SUBMITTED, key="unverified-not-requeueable")

    entries = _call(migrated_session, verification_seconds=STALE_NOW)

    stalled = [entry for entry in entries if entry.source == "stalled_verification"]
    assert stalled and not any(entry.requeue_eligible for entry in stalled)


def test_the_two_stall_reports_do_not_report_each_others_states(
    migrated_session: Session,
) -> None:
    """The report is keyed on WHO OWES THE DECISION, and the two owners are disjoint.

    Both thresholds are maximally on here, so a state claimed by both reports would show up
    twice. Without this the obvious implementation -- adding SUBMITTED to APPROVAL_STATES --
    passes every other test in this file while telling an operator a verifier-owed unit is
    waiting on a human, whose remedy is the wrong one.
    """
    verifier_owed = _park_in(migrated_session, WorkUnitState.SUBMITTED, key="owed-by-verifier")
    human_owed = _park_in(migrated_session, WorkUnitState.AWAITING_REVIEW, key="owed-by-human")

    entries = _call(migrated_session, verification_seconds=STALE_NOW, approval_seconds=STALE_NOW)

    by_source = {
        source: sorted(e.work_unit_id for e in entries if e.source == source)
        for source in ("stalled_verification", "stalled_approval")
    }
    assert by_source["stalled_verification"] == [verifier_owed.id]
    assert by_source["stalled_approval"] == [human_owed.id]


def test_reporting_a_stalled_verification_transitions_nothing(migrated_session: Session) -> None:
    """Read-only, asserted by re-reading the row rather than by trusting the returned object."""
    unit = _park_in(migrated_session, WorkUnitState.SUBMITTED, key="untouched-by-the-report")
    version_before = unit.version

    _call(migrated_session, verification_seconds=STALE_NOW)

    migrated_session.expire_all()
    refreshed = migrated_session.get(WorkUnit, unit.id)
    assert refreshed is not None
    assert refreshed.state == WorkUnitState.SUBMITTED.value
    assert refreshed.version == version_before


def test_the_stalled_verification_threshold_cannot_be_switched_off() -> None:
    """A plain int, a real default, and a CAP -- the cap is the point.

    Its sibling's comment says why: a large value silences a report as effectively as a `None`
    ever did, so "cannot be switched off" is only true of the values an operator can actually
    set. The floor is not the risk: 0 reports everything, which is maximally on and is what these
    tests use so they need no sleep.
    """
    field = Settings.model_fields["dead_letter_stalled_verification_seconds"]

    assert field.default is not None
    assert field.default > 0
    caps = [m for m in field.metadata if getattr(m, "le", None) is not None]
    assert caps, "the threshold must be capped, or a large value silences the report"
    assert caps[0].le <= 2_592_000
