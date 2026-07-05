import uuid
from datetime import UTC, datetime

import pytest
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
