import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

import orchestrator.services.package_intake as package_intake
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, PackageAcceptanceCriterion, WorkPackageRevision
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import (
    AcceptanceCriterionProjection,
    PackageIntakeCommand,
    register_package_intake,
)
from orchestrator.services.packages import register_approved_unit, register_revision

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = "evt-package-intake-1"


def acceptance_criterion(ac_id: str = "AC-001") -> AcceptanceCriterionProjection:
    return AcceptanceCriterionProjection(
        ac_id=ac_id,
        condition="The change is tested.",
        evidence_type="automated_test",
        evidence="gate: focused tests pass",
        approver="policy",
    )


def intake_command(**overrides: object) -> PackageIntakeCommand:
    base = PackageIntakeCommand(
        package_id="pkg-ws32",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:one",
        source_path="/tmp/pkg-ws32",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=APPROVAL_EVENT_ID,
        approval_ledger_commit="a" * 40,
        profile="software",
        status_at_intake="approved",
        verification_mode="caller_attested_cli_verified",
        verification_limitations={
            "api_recomputes_remote_git_object": False,
            "cli_verified_local_package_hash": True,
            "cli_verified_approval_lineage": True,
        },
        enforcement_snapshot={
            "title": "Implement one",
            "outcome": "One works",
            "scope": {"in": ["feature"]},
            "dependencies": [],
            "applicable_standards": ["STD-1"],
        },
        authority=AUTHORITY,
        registry_version=1,
        acceptance_criteria=(acceptance_criterion(),),
        idempotency_key="package-intake-1",
        expected_version=0,
    )
    return PackageIntakeCommand(**{**base.__dict__, **overrides})


def human_actor() -> ActorContext:
    return ActorContext("human-1", ActorRole.HUMAN)


FOLLOW_UP = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_intake_persists_the_follow_up_declaration(migrated_session: Session) -> None:
    command = intake_command(follow_up=FOLLOW_UP)

    revision = register_package_intake(migrated_session, command, human_actor())
    migrated_session.expire_all()
    reread = migrated_session.get(WorkPackageRevision, revision.id)

    assert reread is not None
    assert reread.follow_up == FOLLOW_UP


def test_intake_without_a_declaration_stores_null(migrated_session: Session) -> None:
    revision = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.expire_all()
    reread = migrated_session.get(WorkPackageRevision, revision.id)

    assert reread is not None
    assert reread.follow_up is None


def test_a_malformed_declaration_is_rejected_at_the_gate(migrated_session: Session) -> None:
    command = intake_command(follow_up={"required": "yes"})

    with pytest.raises(DomainError) as caught:
        register_package_intake(migrated_session, command, human_actor())

    assert caught.value.code == "follow_up_invalid"


def _snapshot_with(**extra: object) -> dict[str, object]:
    return {**intake_command().enforcement_snapshot, **extra}


def test_intake_persists_a_declared_reach_normalized(migrated_engine: Engine) -> None:
    # Re-read through a DIFFERENT session: the only reader that cannot see an uncommitted write.
    with Session(migrated_engine) as session:
        revision = register_package_intake(
            session,
            intake_command(
                package_id="pkg-reach",
                content_hash="sha256:reach",
                idempotency_key="package-intake-reach",
                # Declared in the author's order; stored sorted, so two packages that reach the
                # same places read identically at the gate.
                enforcement_snapshot=_snapshot_with(reach=["source_repository", "live_estate"]),
            ),
            human_actor(),
        )
        session.commit()
        revision_id = revision.id

    with Session(migrated_engine) as reader:
        reread = reader.get(WorkPackageRevision, revision_id)

        assert reread is not None
        assert reread.enforcement_snapshot["reach"] == ["live_estate", "source_repository"]


def test_intake_without_a_reach_declaration_stores_no_reach_key(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        revision = register_package_intake(
            session,
            intake_command(package_id="pkg-no-reach", content_hash="sha256:no-reach"),
            human_actor(),
        )
        session.commit()
        revision_id = revision.id

    with Session(migrated_engine) as reader:
        reread = reader.get(WorkPackageRevision, revision_id)

        assert reread is not None
        assert "reach" not in reread.enforcement_snapshot


def test_an_unrecognised_reach_value_is_rejected_at_the_gate(migrated_session: Session) -> None:
    command = intake_command(enforcement_snapshot=_snapshot_with(reach=["everywhere"]))

    with pytest.raises(DomainError) as caught:
        register_package_intake(migrated_session, command, human_actor())

    assert caught.value.code == "reach_invalid"


def test_a_pre_wsp28_intake_event_still_replays(migrated_session: Session) -> None:
    """An intake event written before the follow_up field existed must not become a conflict.

    `_command_identity` is compared field-for-field on replay. Without an exemption, every
    event already in production -- written after intake_purpose shipped but before follow_up
    did, so it carries intake_purpose but not this key -- would raise idempotency_conflict on
    its next replay. Same shape as the intake_purpose exemption.

    `events` is append-only (a `BEFORE UPDATE OR DELETE` trigger rejects both), so unlike the
    brief's draft this cannot mutate an already-persisted event in place; the legacy-shaped
    payload is written directly instead, mirroring
    test_package_intake_replays_ws32_event_identity_without_new_task6_fields below.
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
        enforcement_snapshot={
            **command.enforcement_snapshot,
            "acceptance_criteria": ["AC-001"],
        },
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
        expected_version=command.expected_version,
    )
    legacy_identity = package_intake._command_identity(command, actor)
    legacy_identity.pop("follow_up")
    migrated_session.add(
        Event(
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            payload={"command": legacy_identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.flush()

    replayed = register_package_intake(migrated_session, command, actor)

    assert replayed.id == revision.id


def test_a_real_declaration_does_not_replay_against_a_legacy_event_missing_it(
    migrated_session: Session,
) -> None:
    """Pins the predicate at `_legacy_identity_matches`: the follow_up pop only fires when
    `command.follow_up is None`. A command carrying a REAL declaration must never silently
    replay against a stored event that lacks the key -- flipping `and` to `or`, or deleting this
    clause, would make every declaration replay-invisible with the rest of the suite still green.
    """
    command = intake_command(follow_up=FOLLOW_UP)
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
        enforcement_snapshot={
            **command.enforcement_snapshot,
            "acceptance_criteria": ["AC-001"],
        },
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="package_cli",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        follow_up=command.follow_up,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        expected_version=command.expected_version,
    )
    legacy_identity = package_intake._command_identity(command, actor)
    legacy_identity.pop("follow_up")
    migrated_session.add(
        Event(
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            payload={"command": legacy_identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.flush()

    with pytest.raises(DomainError) as error:
        register_package_intake(migrated_session, command, actor)

    assert error.value.code == "idempotency_conflict"


def test_a_command_without_a_declaration_does_not_replay_against_an_event_that_has_one(
    migrated_session: Session,
) -> None:
    """Mirror of the above: a stored event carrying a follow_up declaration must not be treated
    as a replay of a command that now declares none -- losing a value silently would be exactly
    as wrong as gaining one silently.
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
        enforcement_snapshot={
            **command.enforcement_snapshot,
            "acceptance_criteria": ["AC-001"],
        },
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
        expected_version=command.expected_version,
    )
    stored_identity = package_intake._command_identity(command, actor)
    stored_identity["follow_up"] = FOLLOW_UP
    migrated_session.add(
        Event(
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            payload={"command": stored_identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.flush()

    with pytest.raises(DomainError) as error:
        register_package_intake(migrated_session, command, actor)

    assert error.value.code == "idempotency_conflict"


def test_package_intake_rejects_draft_status(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(status_at_intake="draft"),
            human_actor(),
        )

    assert error.value.code == "package_intake_status_invalid"


def test_package_intake_rejects_executable_status(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(status_at_intake="executable"),
            human_actor(),
        )

    assert error.value.code == "package_intake_status_invalid"


def test_package_intake_requires_human_actor(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(),
            ActorContext("worker-1", ActorRole.WORKER),
        )

    assert error.value.code == "human_actor_required"


def test_package_intake_rejects_missing_verification_mode(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(verification_mode=""),
            human_actor(),
        )

    assert error.value.code == "package_intake_verification_invalid"


def test_package_intake_is_idempotent(migrated_session: Session) -> None:
    command = intake_command()

    first = register_package_intake(migrated_session, command, human_actor())
    second = register_package_intake(migrated_session, command, human_actor())

    acceptance_rows = tuple(
        migrated_session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == first.id)
            .order_by(PackageAcceptanceCriterion.ac_id)
        )
    )
    events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.idempotency_key == command.idempotency_key).order_by(Event.id)
        )
    )

    assert second.id == first.id
    assert first.profile == "software"
    assert first.status_at_intake == "approved"
    assert first.intake_source == "package_cli"
    assert first.approval_ledger_commit == "a" * 40
    assert first.approval_event_id == "evt-package-intake-1"
    assert first.verification_mode == "caller_attested_cli_verified"
    assert first.verification_limitations == command.verification_limitations
    assert first.enforcement_snapshot["acceptance_criteria"] == ["AC-001"]
    assert [(row.ac_id, row.condition) for row in acceptance_rows] == [
        ("AC-001", "The change is tested.")
    ]
    assert [event.action for event in events] == ["package_revision.intake_registered"]


def test_package_intake_replays_ws32_event_identity_without_new_task6_fields(
    migrated_session: Session,
) -> None:
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
        enforcement_snapshot={
            **command.enforcement_snapshot,
            "acceptance_criteria": ["AC-001"],
        },
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
        expected_version=command.expected_version,
    )
    legacy_identity = package_intake._command_identity(command, actor)
    legacy_identity.pop("intake_purpose")
    legacy_identity.pop("follow_up")
    limitations = legacy_identity["verification_limitations"]
    assert isinstance(limitations, dict)
    limitations.pop("protocol_fixture_only", None)
    migrated_session.add(
        Event(
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            payload={"command": legacy_identity},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.flush()

    replay = register_package_intake(migrated_session, command, actor)

    assert replay.id == revision.id


def test_concurrent_identical_intake_converges(migrated_engine: Engine) -> None:
    start = Barrier(2)
    before_intake_lock = Barrier(2)

    def synchronize_intake_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" in statement and "intake_namespace" in statement:
            before_intake_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_intake_lock)

    def register() -> uuid.UUID:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            revision = register_package_intake(session, intake_command(), human_actor())
            session.commit()
            return revision.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            revision_ids = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_intake_lock)

    with Session(migrated_engine) as session:
        intake_events = tuple(
            session.scalars(select(Event).where(Event.idempotency_key == "package-intake-1"))
        )
        acceptance_rows = tuple(session.scalars(select(PackageAcceptanceCriterion)))

    assert revision_ids[0] == revision_ids[1]
    assert len(intake_events) == 1
    assert len(acceptance_rows) == 1


def test_package_intake_conflict_is_stable(migrated_session: Session) -> None:
    register_package_intake(migrated_session, intake_command(), human_actor())

    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(
                content_hash="sha256:different",
                idempotency_key="package-intake-2",
            ),
            human_actor(),
        )

    assert error.value.code == "package_intake_conflict"


def test_package_intake_rejects_duplicate_acceptance_criteria(
    migrated_session: Session,
) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(
                acceptance_criteria=(
                    acceptance_criterion("AC-001"),
                    acceptance_criterion("AC-001"),
                )
            ),
            human_actor(),
        )

    assert error.value.code == "package_intake_acceptance_criteria_invalid"


def test_package_cli_revision_blocks_direct_unit_registration_without_approved_decomposition(
    migrated_session: Session,
) -> None:
    revision = register_package_intake(migrated_session, intake_command(), human_actor())

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-1",
            title="Implement one",
            outcome="One works",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "decomposition_approval_required"


def test_package_cli_revision_rejects_activation_source_without_approved_decomposition_id(
    migrated_session: Session,
) -> None:
    revision = register_package_intake(migrated_session, intake_command(), human_actor())

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-1",
            title="Implement one",
            outcome="One works",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            activation_source="approved_decomposition",
        )

    assert error.value.code == "decomposition_approval_required"
