import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.services.packages import register_approved_unit, register_revision

AUTHORITY = AuthorityEnvelope(
    capabilities={"repository_write": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = uuid.UUID(int=1)


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
    approval_event_id = uuid.uuid4()
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
            approval_event_id=uuid.uuid4(),
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
            approval_event_id=uuid.uuid4(),
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
