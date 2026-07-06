import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, PackageAcceptanceCriterion
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import (
    AcceptanceCriterionProjection,
    PackageIntakeCommand,
    register_package_intake,
)
from orchestrator.services.packages import register_approved_unit

AUTHORITY = AuthorityEnvelope(
    capabilities={"repository_write": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = uuid.UUID(int=1)


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


def test_package_intake_rejects_draft_status(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(status_at_intake="draft"),
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
    assert first.verification_mode == "caller_attested_cli_verified"
    assert first.verification_limitations == command.verification_limitations
    assert first.enforcement_snapshot["acceptance_criteria"] == ["AC-001"]
    assert [(row.ac_id, row.condition) for row in acceptance_rows] == [
        ("AC-001", "The change is tested.")
    ]
    assert [event.action for event in events] == ["package_revision.intake_registered"]


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
            session.scalars(
                select(Event).where(Event.idempotency_key == "package-intake-1")
            )
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
            required_capability="repository_write",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "decomposition_approval_required"


def test_package_cli_revision_allows_unit_registration_with_approved_decomposition_activation_source(  # noqa: E501 - required task test name
    migrated_session: Session,
) -> None:
    revision = register_package_intake(migrated_session, intake_command(), human_actor())

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-1",
        title="Implement one",
        outcome="One works",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        activation_source="approved_decomposition",
    )

    assert unit.state == "draft"
