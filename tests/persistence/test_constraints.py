import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import (
    Claim,
    ContextSnapshot,
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
        approval_event_id=str(uuid.uuid4()),
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


def test_work_unit_package_revision_cannot_change(migrated_session: Session) -> None:
    first_revision = _revision(migrated_session)
    second_package = WorkPackage(package_id="pkg-2", source_repository="owner/repo")
    second_revision = WorkPackageRevision(
        work_package=second_package,
        revision=1,
        content_hash="other-hash",
        source_path="intent.md",
        source_commit="def456",
        approved_by="human-1",
        approved_at="2026-07-05T12:00:00+00:00",
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={},
        authority_fingerprint="authority",
        registry_version=1,
        registered_by="human-1",
    )
    unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=first_revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add_all((second_revision, unit))
    migrated_session.commit()

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(
                "UPDATE work_units SET work_package_revision_id = :revision_id WHERE id = :unit_id"
            ),
            {"revision_id": second_revision.id, "unit_id": unit.id},
        )
        migrated_session.commit()


def test_work_unit_state_and_version_remain_mutable(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        decomposition_approved_by="human-1",
        decomposition_approved_at="2026-07-05T12:00:00+00:00",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add(unit)
    migrated_session.commit()
    previous_updated_at = unit.updated_at

    migrated_session.execute(
        text("UPDATE work_units SET state = 'ready', version = version + 1 WHERE id = :id"),
        {"id": unit.id},
    )
    migrated_session.commit()
    migrated_session.refresh(unit)

    assert unit.state == "ready"
    assert unit.version == 2
    assert unit.updated_at > previous_updated_at


def test_evidence_supersession_must_match_revision_unit_and_ac(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session)
    first_unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="python",
        authority_fingerprint="authority",
    )
    second_unit = WorkUnit(
        unit_key="unit-2",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="draft",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add_all((first_unit, second_unit))
    migrated_session.flush()
    original = Evidence(
        work_package_revision_id=revision.id,
        work_unit_id=first_unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="test",
        stable_ref="artifact://one",
        source_revision="abc123",
        recorded_by="worker-1",
        event_id=uuid.uuid4(),
        idempotency_key="evidence-1",
    )
    migrated_session.add(original)
    migrated_session.flush()
    migrated_session.add(
        Evidence(
            work_package_revision_id=revision.id,
            work_unit_id=second_unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://two",
            source_revision="abc123",
            recorded_by="worker-1",
            event_id=uuid.uuid4(),
            idempotency_key="evidence-2",
            supersedes_evidence_id=original.id,
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


@pytest.mark.parametrize(
    ("state", "attempt_count", "max_attempts"),
    [
        ("unknown", 0, 1),
        ("draft", -1, 1),
        ("draft", 2, 1),
        ("draft", 0, -1),
    ],
)
def test_work_unit_enum_and_attempt_boundaries(
    migrated_session: Session, state: str, attempt_count: int, max_attempts: int
) -> None:
    revision = _revision(migrated_session)
    migrated_session.add(
        WorkUnit(
            unit_key="unit-1",
            work_package_revision_id=revision.id,
            title="Title",
            outcome="Outcome",
            state=state,
            required_capability="python",
            authority_fingerprint="authority",
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_work_package_revision_uniqueness_is_database_enforced(
    migrated_session: Session,
) -> None:
    revision = _revision(migrated_session)
    migrated_session.add(
        WorkPackageRevision(
            work_package_id=revision.work_package_id,
            revision=revision.revision,
            content_hash="different-hash",
            source_path="intent.md",
            source_commit="def456",
            approved_by="human-1",
            approved_at="2026-07-05T12:00:00+00:00",
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority_fingerprint="authority",
            registry_version=1,
            registered_by="human-1",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_work_unit_revision_foreign_key_is_database_enforced(
    migrated_session: Session,
) -> None:
    migrated_session.add(
        WorkUnit(
            unit_key="unit-1",
            work_package_revision_id=uuid.uuid4(),
            title="Title",
            outcome="Outcome",
            state="draft",
            required_capability="python",
            authority_fingerprint="authority",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_dependency_cannot_reference_its_own_work_unit(migrated_session: Session) -> None:
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
            kind="work_unit",
            required_state_or_condition="completed",
            depends_on_work_unit_id=unit.id,
            status="pending",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_context_snapshot_claim_attempt_must_match_claim(migrated_session: Session) -> None:
    revision = _revision(migrated_session)
    ready_unit = WorkUnit(
        unit_key="unit-1",
        work_package_revision_id=revision.id,
        title="Title",
        outcome="Outcome",
        state="ready",
        decomposition_approved_by="human-1",
        decomposition_approved_at="2026-07-05T12:00:00+00:00",
        required_capability="python",
        authority_fingerprint="authority",
    )
    migrated_session.add(ready_unit)
    migrated_session.flush()

    claim = Claim(
        work_unit_id=ready_unit.id,
        attempt=1,
        claimed_by="worker-1",
        lease_token_hash="hash",
        idempotency_key="claim",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    migrated_session.add(claim)
    migrated_session.flush()

    migrated_session.add(
        ContextSnapshot(
            work_package_revision_id=ready_unit.work_package_revision_id,
            work_unit_id=ready_unit.id,
            claim_id=claim.id,
            attempt=2,
            actor_id="worker-1",
            actor_role="worker",
            context={},
            context_fingerprint="fp",
            classification="accepted",
            decision="accepted",
            event_id=uuid.uuid4(),
            idempotency_key="ctx",
        )
    )

    with pytest.raises(IntegrityError):
        migrated_session.commit()
