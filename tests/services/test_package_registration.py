import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import (
    AuthorityBudgets,
    AuthorityEnvelope,
    authority_fingerprint,
    normalize_authority,
)
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Approval, Event, WorkPackageRevision
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repository_write": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = str(uuid.UUID(int=1))


def register_test_revision(session: Session) -> WorkPackageRevision:
    return register_revision(
        session,
        package_id="pkg-1",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:one",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=APPROVAL_EVENT_ID,
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def test_revision_registration_is_idempotent_and_normalized(
    migrated_session: Session,
) -> None:
    approval_event_id = str(uuid.uuid4())
    values = {
        "package_id": "pkg-1",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:one",
        "source_path": "intent.md",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW,
        "approval_event_id": approval_event_id,
        "enforcement_snapshot": {"z": 1, "a": {"later": True}},
        "authority": AUTHORITY,
        "registry_version": 1,
        "actor_id": "human-1",
        "actor_role": ActorRole.HUMAN,
    }

    first = register_revision(migrated_session, **values)
    second = register_revision(migrated_session, **values)

    assert second.id == first.id
    assert list(first.enforcement_snapshot) == ["a", "authority", "z"]
    assert first.authority_fingerprint


def test_conflicting_revision_registration_has_stable_error(
    migrated_session: Session,
) -> None:
    register_test_revision(migrated_session)

    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-1",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:different",
            source_path="intent.md",
            source_commit="def456",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority=AUTHORITY,
            registry_version=1,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "revision_conflict"


def test_registration_requires_registered_human_actor(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-1",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:one",
            source_path="intent.md",
            source_commit="abc123",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority=AUTHORITY,
            registry_version=1,
            actor_id="worker-1",
            actor_role=ActorRole.WORKER,
        )

    assert error.value.code == "human_actor_required"


def test_approved_unit_registration_only_creates_draft(migrated_session: Session) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-1",
        title="Implement one",
        outcome="One works",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.state == "draft"
    assert unit.decomposition_approved_by == "human-1"


def test_approved_unit_registration_defaults_to_three_attempts(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-default-attempts",
        title="Implement defaults",
        outcome="Defaults work",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.max_attempts == 3


def test_approved_unit_registration_preserves_explicit_attempt_budget(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-explicit-attempts",
        title="Implement explicit budget",
        outcome="Explicit budget works",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=2,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.max_attempts == 2


def test_approved_unit_registration_idempotency_conflicts_when_raw_authority_differs(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    raw_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-a",
            "allowed_commands": ["make check"],
        },
    }
    conflicting_raw_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-b",
            "allowed_commands": ["make check"],
        },
    }

    # Constraint values are covered by the authority fingerprint, so these two envelopes
    # are no longer normalization-identical. Raw-payload replay identity is still what
    # catches differences the fingerprint cannot see — see the unknown-field test below.
    assert normalize_authority(raw_authority) != normalize_authority(conflicting_raw_authority)

    first = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-raw-authority",
        title="Respect raw authority",
        outcome="Replay identity includes raw authority.",
        required_capability="repository_write",
        authority=normalize_authority(raw_authority),
        authority_payload=raw_authority,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-raw-authority",
    )
    replay = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-raw-authority",
        title="Respect raw authority",
        outcome="Replay identity includes raw authority.",
        required_capability="repository_write",
        authority=normalize_authority(raw_authority),
        authority_payload=raw_authority,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-raw-authority",
    )

    assert replay.id == first.id

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-raw-authority",
            title="Respect raw authority",
            outcome="Replay identity includes raw authority.",
            required_capability="repository_write",
            authority=normalize_authority(conflicting_raw_authority),
            authority_payload=conflicting_raw_authority,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            idempotency_key="unit-raw-authority",
        )

    assert error.value.code == "idempotency_conflict"


def test_approved_unit_registration_conflicts_when_unknown_field_values_differ(
    migrated_session: Session,
) -> None:
    """Raw-payload replay identity guards what the fingerprint cannot see.

    Normalization records unknown fields by *name* only, so two envelopes whose unknown
    field values differ share a fingerprint. The stored raw payload must still make them
    conflict, otherwise an approved fingerprint would cover an envelope nobody approved.
    """
    revision = register_test_revision(migrated_session)
    raw_authority = {
        "capabilities": {"repository_write": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "future_field": "original",
    }
    conflicting_raw_authority = {**raw_authority, "future_field": "tampered"}

    assert authority_fingerprint(normalize_authority(raw_authority)) == authority_fingerprint(
        normalize_authority(conflicting_raw_authority)
    )

    register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-unknown-field",
        title="Respect raw authority",
        outcome="Replay identity includes unknown field values.",
        required_capability="repository_write",
        authority=normalize_authority(raw_authority),
        authority_payload=raw_authority,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-unknown-field",
    )

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-unknown-field",
            title="Respect raw authority",
            outcome="Replay identity includes unknown field values.",
            required_capability="repository_write",
            authority=normalize_authority(conflicting_raw_authority),
            authority_payload=conflicting_raw_authority,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            idempotency_key="unit-unknown-field",
        )

    assert error.value.code == "idempotency_conflict"


def test_authority_approval_idempotency_binds_expected_version(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="approval-version",
        title="Approval version",
        outcome="Approval is exact",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    first = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approved",
        idempotency_key="authority-version",
        expected_version=1,
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="approved",
            idempotency_key="authority-version",
            expected_version=2,
        )

    assert first.subject_revision_or_fingerprint == unit.authority_fingerprint
    assert error.value.code == "idempotency_conflict"


def test_authority_approval_rejects_legacy_invalid_dependency_update_envelope(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="invalid-authority",
        title="Invalid authority",
        outcome="Authority remains unapproved.",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.authority = {
        "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {"allowed_commands": ["uv sync --locked"]},
    }
    migrated_session.flush()

    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="approve legacy envelope",
            idempotency_key="invalid-authority-approval",
            expected_version=unit.version,
        )

    assert error.value.code == "authority_mutation_commands_invalid"
    assert migrated_session.scalar(select(func.count()).select_from(Approval)) == 0
    assert migrated_session.scalar(select(func.count()).select_from(Event)) == 0


def test_action_approval_remains_version_bound_without_authority_effect(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="action-approval",
        title="Action approval",
        outcome="Action approval remains unchanged.",
        required_capability="repository_write",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    approval = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="action",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approve action",
        idempotency_key="action-approval",
        expected_version=unit.version,
    )

    assert approval.subject_revision_or_fingerprint == str(unit.version)
    assert unit.authority_approval_id is None


def test_concurrent_identical_first_registration_converges(
    migrated_engine: Engine,
) -> None:
    start = Barrier(2)
    before_registration_lock = Barrier(2)

    def synchronize_registration_lock(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" in statement:
            before_registration_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    def register() -> uuid.UUID:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            revision = register_test_revision(session)
            session.commit()
            return revision.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            revision_ids = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    assert revision_ids[0] == revision_ids[1]


def test_concurrent_conflicting_first_registration_returns_stable_error(
    migrated_engine: Engine,
) -> None:
    start = Barrier(2)
    before_registration_lock = Barrier(2)

    def synchronize_registration_lock(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" in statement:
            before_registration_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    def register(registration: tuple[int, str]) -> str:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            try:
                register_revision(
                    session,
                    package_id="pkg-1",
                    source_repository="owner/repo",
                    revision=registration[0],
                    content_hash=registration[1],
                    source_path="intent.md",
                    source_commit="abc123",
                    approved_by="human-1",
                    approved_at=NOW,
                    approval_event_id=APPROVAL_EVENT_ID,
                    enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
                    authority=AUTHORITY,
                    registry_version=1,
                    actor_id="human-1",
                    actor_role=ActorRole.HUMAN,
                )
                session.commit()
                return "registered"
            except DomainError as error:
                session.rollback()
                return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(register, registration)
                for registration in ((1, "sha256:one"), (2, "sha256:other"))
            ]
            results = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    assert sorted(results) == ["registered", "revision_conflict"]
