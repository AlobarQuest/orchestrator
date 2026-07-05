import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    Dependency,
    Evidence,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)


def _revision(session: Session) -> WorkPackageRevision:
    package = WorkPackage(package_id="pkg-1", source_repository="owner/repo")
    revision = WorkPackageRevision(
        work_package=package,
        revision=1,
        content_hash="hash",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at="2026-07-05T12:00:00+00:00",
        approval_event_id=uuid.uuid4(),
        enforcement_snapshot={},
        authority_fingerprint="authority",
        registry_version=1,
        registered_by="human-1",
    )
    session.add(revision)
    session.flush()
    return revision


def test_work_unit_beyond_draft_requires_decomposition_approval(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session)
    migrated_session.add(
        WorkUnit(
            unit_key="unit-1",
            work_package_revision_id=revision.id,
            title="Title",
            outcome="Outcome",
            state="ready",
            required_capability="python",
            authority_fingerprint="authority",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_dependency_requires_exactly_one_reference(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add(unit)
    migrated_session.flush()
    migrated_session.add(
        Dependency(
            work_unit_id=unit.id,
            kind="external_system",
            required_state_or_condition="healthy",
            status="pending",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_evidence_requires_reference_or_payload(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add(unit)
    migrated_session.flush()
    migrated_session.add(
        Evidence(
            work_package_revision_id=revision.id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            source_revision="abc123",
            recorded_by="worker-1",
            event_id=uuid.uuid4(),
            idempotency_key="evidence-1",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()
