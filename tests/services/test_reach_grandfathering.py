"""WS-P2.18 Increment 4 -- reach becomes required, and the exemption from it expires itself.

Increment 3 shipped a known-good mechanism that recognised nothing, because no package declared
reach: 0 of 24, and 0 of 21 approved revisions in production. This increment binds the key to real
work by refusing to admit a unit whose package said nothing about what its work touches, which is
a change that would brick the one approved revision predating the key. The exemption for that
revision is the subject of most of this file, because an exemption is where a safety rule goes to
die quietly.

Every proof runs in both directions. The two rules this one replaced -- a cutoff date, and "reach
is absent" -- are each tested against directly: the point is not that the shipped rule works, it is
that the shipped rule REFUSES the cases those two would have absolved.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from orchestrator.api.schemas import FactoryPolicyResponse
from orchestrator.errors import DomainError
from orchestrator.factory_policy import (
    REACH_UNDECLARED,
    REACH_UNRECOGNISED,
    Grandfathering,
    load_factory_policy,
)
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Approval, WorkPackageRevision, WorkUnit
from orchestrator.services.authority_gate import human_authority_gate
from orchestrator.services.dispatch import dispatch_work_unit
from orchestrator.services.packages import register_approved_unit
from orchestrator.services.reach_admission import (
    REACH_POLICY_UNREADABLE,
    reach_admission_refusal,
)
from tests.services.test_authority_known_good import uv_bump
from tests.services.test_dispatch import (
    HUMAN,
    NOW,
    FakeGitHubDispatcher,
    dispatch_command,
    ready_unit,
    recognised_unit,
    recognising_settings,
    settings,
)

# The one revision the shipped artifact grandfathers. Read from the artifact rather than repeated
# as a literal: this file asserts the SHAPE of the exemption, and pinning its contents here would
# be the second copy ADR-0010 forbids.
GRANDFATHERED = tuple(sorted(load_factory_policy().grandfathered.revisions))  # type: ignore[union-attr]
NOT_GRANDFATHERED = uuid.UUID("00000000-0000-4000-8000-000000000001")
REPOSITORY_REACH = ("source_repository",)

DECIDED = 'decided = "2026-08-01"'
GRANDFATHERED_TABLE = f"""
[grandfathered]
rationale = "the one revision that predates the key"
{DECIDED}
revisions = ["{NOT_GRANDFATHERED}"]
"""
VALID = f"""
version = 5

[reach.source_repository]
rationale = "repository only"
{DECIDED}

[reach.live_estate]
rationale = "something already serving"
{DECIDED}

[reach.external_system]
rationale = "outside the estate"
{DECIDED}

[reach.operator_machine]
rationale = "the operator's own machine"
{DECIDED}
{GRANDFATHERED_TABLE}"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "factory-policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# The boundary is a list of ids, and it is the list that makes it a boundary
# ---------------------------------------------------------------------------------------------


def test_the_shipped_artifact_grandfathers_a_named_finite_list_of_revisions() -> None:
    policy = load_factory_policy()

    assert policy.grandfathered is not None
    assert policy.grandfathered.revisions
    assert all(isinstance(revision, uuid.UUID) for revision in policy.grandfathered.revisions)
    assert policy.grandfathered.rationale.strip()


def test_a_revision_created_after_the_boundary_is_still_refused() -> None:
    """§3.2 -- the test that distinguishes a bounded rule from a permanent hole.

    A package authored tomorrow gets a revision id nobody could have written into a document that
    ships inside the image, so it cannot be a member however little it declares. Asserted over
    freshly minted ids rather than one literal, because the claim is about every id that is not on
    the list, not about a particular one that is not.
    """
    policy = load_factory_policy()

    for _ in range(16):
        assert policy.admission_refusals(None, uuid.uuid4()) == (REACH_UNDECLARED,)


def test_the_exemption_is_keyed_on_the_revision_and_not_on_the_absence_it_exempts() -> None:
    """The rule the handoff warned against, tested directly.

    "Revisions with no declared reach are grandfathered" would answer both of these the same way,
    because the input policy sees is identical: no reach at all. The shipped rule answers them
    oppositely, and the ONLY difference between the two calls is which revision is asking.
    """
    policy = load_factory_policy()

    assert policy.admission_refusals(None, GRANDFATHERED[0]) == ()
    assert policy.admission_refusals(None, NOT_GRANDFATHERED) == (REACH_UNDECLARED,)


def test_a_date_boundary_would_have_absolved_what_this_one_refuses() -> None:
    """Why not a date. A date is a property of the package; this is a property of the record.

    A package authored before any cutoff and broken down after it still satisfies "authored before
    the cutoff" forever, so a date-keyed rule has no way to stop covering it. Membership cannot be
    acquired by ageing, by waiting, or by declaring nothing -- only by being written down.
    """
    policy = load_factory_policy()
    assert policy.grandfathered is not None

    # Everything that is not literally on the list, including ids that would predate any cutoff.
    for candidate in (NOT_GRANDFATHERED, uuid.uuid1(), uuid.uuid4()):
        assert (candidate in policy.grandfathered.revisions) == (
            policy.admission_refusals(None, candidate) == ()
        )


def test_grandfathering_withholds_only_the_undeclared_objection() -> None:
    # Declaring something broken is not a pre-existing condition: it could not have happened before
    # the key existed, so it is not a thing to be exempted from.
    policy = load_factory_policy()

    assert policy.admission_refusals(("invented",), GRANDFATHERED[0]) == (REACH_UNRECOGNISED,)
    assert policy.admission_refusals(None, GRANDFATHERED[0]) == ()  # control


def test_a_declared_reach_needs_no_exemption_and_a_missing_revision_id_gets_none() -> None:
    policy = load_factory_policy()

    assert policy.admission_refusals(REPOSITORY_REACH, None) == ()
    assert policy.admission_refusals(None, None) == (REACH_UNDECLARED,)


# ---------------------------------------------------------------------------------------------
# The exemption does not widen into the human gate
# ---------------------------------------------------------------------------------------------


def test_a_grandfathered_revision_still_draws_the_human_gate(migrated_session: Session) -> None:
    """The fail-open this increment came closest to shipping, pinned.

    `authority_refusals` returns early when reach is None. Had grandfathering been applied there
    too, an empty refusal set would have meant not "a pattern recognised this" but "there was
    nothing to consult" -- lifting the human requirement on an envelope nothing had looked at. The
    exemption says a revision need not have DECLARED reach to be admitted; it has never said a
    person need not read its envelope.
    """
    unit = ready_unit(migrated_session, key="grandfathered-still-asks", reach=[])
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    # Stand this revision in for the grandfathered one: the id is what the exemption is keyed on.
    revision.id = GRANDFATHERED[0]

    gate = human_authority_gate(unit, revision)

    assert gate.refusals == (REACH_UNDECLARED,)
    assert gate.recognised_by == ()


def test_the_recognised_envelope_is_still_recognised_the_control() -> None:
    # The control for the test above: the same policy DOES lift the requirement, on a unit whose
    # package declared reach. Without this the assertion above is satisfied by a gate that never
    # lifts anything.
    unit_id = uuid.uuid4()
    recognition = load_factory_policy().authority_refusals(
        REPOSITORY_REACH, normalize_authority(uv_bump(unit_id)), unit_id
    )

    assert (recognition.refusals, bool(recognition.recognised_by)) == ((), True)


# ---------------------------------------------------------------------------------------------
# The list is required to die
# ---------------------------------------------------------------------------------------------


def test_a_spent_grandfathering_stops_the_artifact_answering_at_all() -> None:
    policy = load_factory_policy()

    with pytest.raises(DomainError) as raised:
        policy.require_live_subject([])

    assert raised.value.code == "factory_policy_grandfathering_spent"
    assert raised.value.recovery is not None


def test_one_live_subject_is_enough_and_an_unrelated_live_revision_is_not() -> None:
    policy = load_factory_policy()

    assert policy.require_live_subject(GRANDFATHERED) is None  # control

    with pytest.raises(DomainError):
        policy.require_live_subject([NOT_GRANDFATHERED])


def test_an_artifact_with_no_grandfathering_never_expires_and_exempts_nobody(
    tmp_path: Path,
) -> None:
    # The END STATE: the table has been deleted, so there is nothing to keep alive and nobody is
    # exempt. Absence reads permissive and is the safe direction -- it withholds no objection.
    policy = load_factory_policy(write(tmp_path, VALID.replace(GRANDFATHERED_TABLE, "")))

    assert policy.grandfathered is None
    assert policy.require_live_subject([]) is None
    assert policy.admission_refusals(None, NOT_GRANDFATHERED) == (REACH_UNDECLARED,)


MALFORMED_GRANDFATHERING: tuple[tuple[str, str], ...] = (
    ("not a table", VALID.replace(GRANDFATHERED_TABLE, '\ngrandfathered = "everything"\n')),
    ("a missing field", VALID.replace(f"{DECIDED}\nrevisions", "revisions")),
    ("an extra field", VALID.replace(DECIDED, f'{DECIDED}\nuntil = "2027-01-01"', 4)),
    ("no revisions at all", VALID.replace(f'["{NOT_GRANDFATHERED}"]', "[]")),
    ("a revision that is not an id", VALID.replace(f'"{NOT_GRANDFATHERED}"', '"the-old-ones"')),
    (
        "the same revision twice",
        VALID.replace(
            f'["{NOT_GRANDFATHERED}"]', f'["{NOT_GRANDFATHERED}", "{NOT_GRANDFATHERED}"]'
        ),
    ),
    (
        "an empty rationale",
        VALID.replace('rationale = "the one revision that predates the key"', 'rationale = "  "'),
    ),
    (
        "a decided value that is not a date",
        VALID.replace(f"{DECIDED}\nrevisions", 'decided = "recently"\nrevisions'),
    ),
)


@pytest.mark.parametrize(
    ("label", "text"),
    MALFORMED_GRANDFATHERING,
    ids=[case[0] for case in MALFORMED_GRANDFATHERING],
)
def test_a_malformed_grandfathering_is_a_named_loud_failure(
    label: str, text: str, tmp_path: Path
) -> None:
    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, text))

    assert raised.value.code == "factory_policy_invalid", label
    assert raised.value.recovery is not None, label


def test_the_valid_artifact_is_the_control_for_every_malformation(tmp_path: Path) -> None:
    policy = load_factory_policy(write(tmp_path, VALID))

    assert policy.grandfathered == Grandfathering(
        rationale="the one revision that predates the key",
        decided=date(2026, 8, 1),
        revisions=frozenset({NOT_GRANDFATHERED}),
    )


# ---------------------------------------------------------------------------------------------
# Liveness is read from the record, and being SPENT is the half that needs proof
# ---------------------------------------------------------------------------------------------


def grandfathering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *revisions: uuid.UUID) -> None:
    """Point the admission reader at an artifact that grandfathers exactly these revisions.

    The shipped list names a production revision, which no test database holds. Standing a test
    revision in for it by rewriting a primary key would move a row every unit points at; naming
    the test's own revision in a temporary artifact exercises the same code on real rows.
    """
    listed = ", ".join(f'"{revision}"' for revision in revisions)
    text = VALID.replace(f'["{NOT_GRANDFATHERED}"]', f"[{listed}]")
    policy = load_factory_policy(write(tmp_path, text))
    monkeypatch.setattr("orchestrator.services.reach_admission.load_factory_policy", lambda: policy)


def settle(session: Session, unit: WorkUnit, state: WorkUnitState) -> None:
    # Fixture setup for a LIVENESS fact, not a lifecycle assertion: what is under test is what
    # `_live_grandfathered_revisions` concludes from the states it finds, and driving a unit to
    # CANCELLED through its legal edges would exercise the transition machinery instead.
    unit.state = str(state)
    session.flush()


def test_a_revision_that_has_produced_no_units_is_a_live_subject(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The state production is actually in: one approved revision, never broken down.

    It has made no units and will make them when a decomposition is approved, so the exemption
    still has something to exempt -- and its undeclared reach is forgiven, which is the whole
    point of listing it.
    """
    undeclared = ready_unit(migrated_session, key="undecomposed", reach=[])
    revision = migrated_session.get(WorkPackageRevision, undeclared.work_package_revision_id)
    assert revision is not None
    migrated_session.delete(undeclared)
    migrated_session.flush()
    grandfathering(monkeypatch, tmp_path, revision.id)

    assert reach_admission_refusal(migrated_session, revision) is None


def test_a_revision_whose_every_unit_has_stopped_is_spent_and_the_artifact_stops_answering(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A document that does not load permits nothing -- including the work it was never about.

    The blast radius is deliberate and total: on the day the last grandfathered revision settles,
    every admission refuses until the table is deleted and a build ships. That is the forcing
    function. A rule that expires itself beats a rule plus a note to remove it, and this repository
    has already paid once for a guard that could be switched off and reported nothing.

    Note the refusal lands on a unit that declared its reach perfectly -- proving the halt is the
    ARTIFACT refusing to answer, not this unit failing a check.
    """
    spent = ready_unit(migrated_session, key="spent-revision", reach=[])
    settle(migrated_session, spent, WorkUnitState.COMPLETED)
    declared = ready_unit(migrated_session, key="innocent-bystander")
    revision = migrated_session.get(WorkPackageRevision, declared.work_package_revision_id)
    assert revision is not None
    grandfathering(monkeypatch, tmp_path, spent.work_package_revision_id)

    assert reach_admission_refusal(migrated_session, revision) == REACH_POLICY_UNREADABLE


def test_one_unit_still_moving_keeps_the_whole_revision_live(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The control for the test above: same revision, same completed unit, one more unit that has
    # not stopped. Spent means EVERY unit has stopped, so this is not it.
    #
    # The second unit is BORN on that revision rather than moved onto it: a database trigger
    # (`enforce_work_unit_revision_immutable`) refuses to repoint a unit, which is the right
    # answer and the reason this reads longer than reassigning a column would.
    settled = ready_unit(migrated_session, key="one-of-two-settled", reach=[])
    register_approved_unit(
        migrated_session,
        revision_id=settled.work_package_revision_id,
        unit_key="two-of-two-moving",
        title="Still going",
        outcome="Not finished",
        required_capability="repo.edit",
        authority=normalize_authority(uv_bump(uuid.uuid4())),
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    settle(migrated_session, settled, WorkUnitState.COMPLETED)
    declared = ready_unit(migrated_session, key="bystander-admitted")
    revision = migrated_session.get(WorkPackageRevision, declared.work_package_revision_id)
    assert revision is not None
    grandfathering(monkeypatch, tmp_path, settled.work_package_revision_id)

    assert reach_admission_refusal(migrated_session, revision) is None


def test_a_failed_unit_is_not_a_stopped_one(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `FAILED -> READY` is a legal edge, so a failed unit can still run and its revision is still
    # a live subject. Folding FAILED into the settled set would expire the exemption early, on a
    # revision whose work is not finished.
    failed = ready_unit(migrated_session, key="failed-not-settled", reach=[])
    settle(migrated_session, failed, WorkUnitState.FAILED)
    declared = ready_unit(migrated_session, key="bystander-failed-case")
    revision = migrated_session.get(WorkPackageRevision, declared.work_package_revision_id)
    assert revision is not None
    grandfathering(monkeypatch, tmp_path, failed.work_package_revision_id)

    assert reach_admission_refusal(migrated_session, revision) is None


def test_a_listed_id_this_database_has_never_seen_reads_as_live_not_as_finished(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Spent requires proof. Concluding "finished" from "not found" would turn a restored database
    # or a mistyped id into a factory-wide halt on missing data, which is fail-closed in the wrong
    # direction; the cost of the other reading is a table that exempts nobody.
    declared = ready_unit(migrated_session, key="unknown-listed-id")
    revision = migrated_session.get(WorkPackageRevision, declared.work_package_revision_id)
    assert revision is not None
    grandfathering(monkeypatch, tmp_path, NOT_GRANDFATHERED)

    assert reach_admission_refusal(migrated_session, revision) is None


def test_an_unreadable_artifact_refuses_rather_than_falling_silent(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = ready_unit(migrated_session, key="unreadable-policy")
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr("orchestrator.services.reach_admission.load_factory_policy", unreadable)

    assert reach_admission_refusal(migrated_session, revision) == REACH_POLICY_UNREADABLE


# ---------------------------------------------------------------------------------------------
# Through admission itself
# ---------------------------------------------------------------------------------------------


def test_an_undeclared_reach_is_refused_and_a_declared_one_is_not(
    migrated_session: Session,
) -> None:
    undeclared = ready_unit(migrated_session, key="admission-undeclared", reach=[])
    declared = ready_unit(migrated_session, key="admission-declared")
    github = FakeGitHubDispatcher([])

    refused = dispatch_work_unit(
        migrated_session, dispatch_command(undeclared.id), settings(), github
    )
    admitted = dispatch_work_unit(
        migrated_session, dispatch_command(declared.id), settings(), github
    )

    assert (refused.status, refused.reason_code) == ("blocked", REACH_UNDECLARED)
    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert len(github.calls) == 1


def test_a_grandfathered_revision_is_admitted_and_the_exemption_widens_nothing_else(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exemption lets old work through the reach term and hands it straight to a person.

    `ready_unit` records the authority approval, so the unit below would otherwise run. Removing
    that approval is what makes the second half discriminating: the unit clears the term the
    exemption covers and is stopped by the one it does not.
    """
    unit = ready_unit(migrated_session, key="grandfathered-admitted", reach=[])
    grandfathering(monkeypatch, tmp_path, unit.work_package_revision_id)
    github = FakeGitHubDispatcher([])

    admitted = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)

    unit.authority_approval_id = None
    migrated_session.flush()
    unapproved = dispatch_work_unit(
        migrated_session, dispatch_command(unit.id, attempt=2), settings(), github
    )

    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert (unapproved.status, unapproved.reason_code) == ("blocked", "authority_approval_missing")


def test_the_off_switch_outranks_the_exemption(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§6, proven NON-VACUOUSLY: the same unit, admitted with the switch on.

    Without the first half, "nothing was admitted" is satisfied by a unit nothing recognised, and
    the claim about precedence is empty.
    """
    unit = ready_unit(migrated_session, key="switch-outranks-exemption", reach=[])
    grandfathering(monkeypatch, tmp_path, unit.work_package_revision_id)
    github = FakeGitHubDispatcher([])

    admitted = dispatch_work_unit(migrated_session, dispatch_command(unit.id), settings(), github)
    switched_off = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id, attempt=2),
        settings(enabled=False),
        github,
    )

    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert (switched_off.status, switched_off.reason_code) == ("skipped", "dispatch_disabled")
    assert len(github.calls) == 1


def test_the_exemption_writes_no_approval_row_read_through_a_second_session(
    migrated_session: Session,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nothing here may attribute a decision to a person who made none, and it cannot be undone.

    Re-read through a DIFFERENT session, which is the only reader that cannot see an uncommitted
    write: `expire_all` on this session would re-SELECT inside the same open transaction and pass
    under the exact defect it is written to catch.
    """
    # `recognised_unit` deliberately records NO approval, so a row found afterwards was written by
    # the path under test rather than by the harness.
    unit = recognised_unit(migrated_session, key="exemption-writes-no-approval", reach=None)
    grandfathering(monkeypatch, tmp_path, unit.work_package_revision_id)
    migrated_session.commit()

    record = dispatch_work_unit(
        migrated_session,
        dispatch_command(unit.id),
        recognising_settings(),
        FakeGitHubDispatcher([]),
    )
    migrated_session.commit()

    with Session(migrated_engine) as reader:
        approvals = reader.scalars(select(Approval).where(Approval.subject_id == unit.id)).all()

    assert approvals == []
    # The exemption carried it past the reach term and the human gate stopped it, which is the
    # state in which writing an approval would have been most tempting and most wrong.
    assert (record.status, record.reason_code) == ("blocked", "authority_approval_missing")


# ---------------------------------------------------------------------------------------------
# What an operator can see
# ---------------------------------------------------------------------------------------------


def test_the_boundary_is_visible_in_the_report_and_declared_by_the_response_model() -> None:
    """§3.3. A response model DROPS every key it does not declare, silently and in one direction.

    That is how WS-P2.12 served an empty enrichment while every service-level assertion passed, so
    the report and the model are pinned to each other rather than to two separate expectations.
    """
    report = load_factory_policy().report()

    assert set(FactoryPolicyResponse.model_fields) == set(report)
    assert report["grandfathered"]["revisions"] == [str(revision) for revision in GRANDFATHERED]
    assert report["grandfathered"]["rationale"].strip()


def test_the_report_says_nothing_where_there_is_nothing_to_say(tmp_path: Path) -> None:
    policy = load_factory_policy(write(tmp_path, VALID.replace(GRANDFATHERED_TABLE, "")))

    assert policy.report()["grandfathered"] is None
