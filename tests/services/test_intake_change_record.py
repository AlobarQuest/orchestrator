"""The join ADR-0026 decision 3 asks for: a revision that names what caused it.

Without it the traceability chain can answer what a work unit caused and not what caused the
work, which is the half the Phase-3 plan calls its hard clause.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, WorkPackageRevision
from orchestrator.services.package_intake import _command_identity, register_package_intake
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.traceability import TraceabilityAnchor, traceability_response
from tests.services.test_package_intake import AUTHORITY, human_actor, intake_command

CHANGE_RECORD = 4321


def test_the_revision_carries_the_change_record(migrated_session: Session) -> None:
    revision = register_package_intake(
        migrated_session,
        intake_command(change_record_id=CHANGE_RECORD),
        human_actor(),
    )
    migrated_session.commit()
    assert revision.change_record_id == CHANGE_RECORD


def test_a_revision_with_no_cause_carries_none(migrated_session: Session) -> None:
    """NULL means nobody recorded a cause -- every intake before ADR-0026, and every intake a
    human registers without one. It never means no cause exists."""
    revision = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()
    assert revision.change_record_id is None


def test_the_change_record_survives_a_reread_through_a_different_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A flushed-but-uncommitted row is visible to the session that wrote it, so re-reading in
    the same session asserts that a call returned an object. Only a DIFFERENT session cannot
    see an uncommitted write."""
    revision = register_package_intake(
        migrated_session, intake_command(change_record_id=CHANGE_RECORD), human_actor()
    )
    migrated_session.commit()
    revision_id = revision.id
    with Session(migrated_engine) as reader:
        stored = reader.get(WorkPackageRevision, revision_id)
        assert stored is not None
        assert stored.change_record_id == CHANGE_RECORD


def test_two_intakes_differing_only_by_change_record_are_not_replays(
    migrated_session: Session,
) -> None:
    """The field is IN the command identity, so a second registration naming a different cause
    is a conflict rather than a silent replay of the first.

    Left out of the identity, the second call would return the first revision and the second
    change record would be lost with nothing said -- which defeats recording a cause at all.
    """
    register_package_intake(
        migrated_session, intake_command(change_record_id=CHANGE_RECORD), human_actor()
    )
    migrated_session.commit()
    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session,
            intake_command(change_record_id=CHANGE_RECORD + 1),
            human_actor(),
        )
    assert raised.value.code == "idempotency_conflict"


def test_an_identical_intake_still_replays(migrated_session: Session) -> None:
    """The inverse. Without it the test above passes on an identity comparison that has simply
    stopped matching anything."""
    first = register_package_intake(
        migrated_session, intake_command(change_record_id=CHANGE_RECORD), human_actor()
    )
    migrated_session.commit()
    second = register_package_intake(
        migrated_session, intake_command(change_record_id=CHANGE_RECORD), human_actor()
    )
    assert first.id == second.id


def test_an_event_written_before_the_key_existed_still_replays(migrated_session: Session) -> None:
    """The legacy exemption, driven the way it will actually be met.

    Every intake event in production predates ADR-0026 and carries no `change_record_id` key.
    Without the exemption, replaying one raises `idempotency_conflict` -- so the first drill or
    retry against an existing intake would fail for a field it never had.

    The legacy event is INSERTED rather than edited: `events` is append-only at the database
    (`reject_append_only_mutation`), so an UPDATE fails on the trigger rather than on the
    assertion, which reads as a bug in the change under test. The identity is built by the
    production function and then has exactly the one key removed, so the fixture cannot drift
    from what the comparison expects.
    """
    command = intake_command()
    actor = human_actor()
    revision = register_revision(
        migrated_session,
        package_id=command.package_id,
        source_repository=command.source_repository,
        revision=command.revision,
        content_hash=command.content_hash,
        source_path=command.source_path,
        source_commit=command.source_commit,
        approved_by=command.approved_by,
        approved_at=command.approved_at,
        approval_event_id=command.approval_event_id,
        enforcement_snapshot=command.enforcement_snapshot,
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="package_cli",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    migrated_session.flush()
    legacy = dict(_command_identity(command, actor))
    assert "change_record_id" in legacy, "the key is not in the identity; the exemption is untested"
    del legacy["change_record_id"]
    migrated_session.add(
        Event(
            occurred_at=datetime(2026, 7, 5, tzinfo=UTC),
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            from_state=None,
            to_state=None,
            payload={"command": legacy},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.commit()

    replayed = register_package_intake(migrated_session, command, actor)
    assert replayed.id == revision.id


def test_a_command_naming_a_cause_does_not_replay_against_a_legacy_event(
    migrated_session: Session,
) -> None:
    """The exemption's FAIL-OPEN direction, which its conditional exists to close.

    Applied unconditionally, `legacy` always lacks the key -- so a stored event that predates
    ADR-0026 (and therefore also lacks it) compares EQUAL to a command that names a change
    record, and the registration replays. The caller is handed the existing revision, told it
    succeeded, and the cause it supplied is silently dropped. That is the failure
    `_legacy_identity_matches` documents for `follow_up`, in the direction that loses data
    rather than the one that raises.

    Mutation found that the obvious test does not see this: replaying a NO-cause command against
    a WITH-cause event conflicts either way, because `observed` still carries a key `legacy` has
    popped. The commands have to run the other way round.
    """
    command = intake_command()
    actor = human_actor()
    revision = register_revision(
        migrated_session,
        package_id=command.package_id,
        source_repository=command.source_repository,
        revision=command.revision,
        content_hash=command.content_hash,
        source_path=command.source_path,
        source_commit=command.source_commit,
        approved_by=command.approved_by,
        approved_at=command.approved_at,
        approval_event_id=command.approval_event_id,
        enforcement_snapshot=command.enforcement_snapshot,
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="package_cli",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    migrated_session.flush()
    legacy = dict(_command_identity(command, actor))
    del legacy["change_record_id"]
    migrated_session.add(
        Event(
            occurred_at=datetime(2026, 7, 5, tzinfo=UTC),
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            from_state=None,
            to_state=None,
            payload={"command": legacy},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session, intake_command(change_record_id=CHANGE_RECORD), actor
        )
    assert raised.value.code == "idempotency_conflict"


def test_the_exemption_is_not_applied_when_the_stored_event_carries_the_key(
    migrated_session: Session,
) -> None:
    """The exemption must be conditional on the OBSERVED event lacking the key.

    Applied unconditionally, an event that legitimately carries a change record would compare
    against an expected identity that had it popped -- so a replay naming NO cause would be
    accepted against an intake that named one, silently agreeing the two are the same
    registration. That is the failure `_legacy_identity_matches` documents for `follow_up`.
    """
    register_package_intake(
        migrated_session, intake_command(change_record_id=CHANGE_RECORD), human_actor()
    )
    migrated_session.commit()
    with pytest.raises(DomainError) as raised:
        register_package_intake(migrated_session, intake_command(), human_actor())
    assert raised.value.code == "idempotency_conflict"


def _unit_on_a_revision_causedy(migrated_session: Session, change_record_id: int | None):
    """A revision carrying (or not carrying) a cause, and a unit on it.

    Registered through the WS-3.1 bootstrap lane, which is the fixture shape every other
    traceability test in this repository uses: a `package_cli` revision requires an approved
    decomposition before it can carry a unit, and building one here would exercise the
    decomposition service rather than the hop under test. The hop reads
    `revision.change_record_id` whatever lane wrote it, and the intake half of the join is
    asserted by the tests above.
    """
    revision = register_revision(
        migrated_session,
        package_id=f"pkg-trace-{change_record_id}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash=f"sha256:trace-{change_record_id}",
        source_path="intent.md",
        source_commit="c" * 40,
        approved_by="human-1",
        approved_at=datetime(2026, 7, 5, tzinfo=UTC),
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["AC-001"]},
        authority=AUTHORITY,
        registry_version=1,
        change_record_id=change_record_id,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key=f"unit-trace-{change_record_id}",
        title="Do the work",
        outcome="It is done",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=datetime(2026, 7, 5, tzinfo=UTC),
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    migrated_session.commit()
    return unit


def test_the_traceability_intent_hop_names_the_change_record(migrated_session: Session) -> None:
    """The chain must be able to USE the join, not merely store it.

    An observation would not do: the observation hop filters on `subject_type="work_unit"`, so
    a revision-scoped observation never reaches any chain at all.
    """
    unit = _unit_on_a_revision_causedy(migrated_session, CHANGE_RECORD)
    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )
    assert response.chains[0].intent.change_record_id == CHANGE_RECORD


def test_a_chain_for_a_revision_with_no_cause_says_so(migrated_session: Session) -> None:
    """The control: the hop must report absence rather than always reporting the value."""
    unit = _unit_on_a_revision_causedy(migrated_session, None)
    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )
    assert response.chains[0].intent.change_record_id is None
