import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class WorkPackage(UUIDPrimaryKey, Base):
    __tablename__ = "work_packages"

    package_id: Mapped[str] = mapped_column(String, unique=True)
    source_repository: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkPackageRevision(UUIDPrimaryKey, Base):
    __tablename__ = "work_package_revisions"
    __table_args__ = (
        UniqueConstraint("work_package_id", "revision"),
        UniqueConstraint("work_package_id", "content_hash"),
        CheckConstraint("revision > 0", name="ck_work_package_revisions_positive_revision"),
        CheckConstraint(
            "content_hash <> '' AND source_path <> '' AND source_commit <> '' "
            "AND approved_by <> '' AND registered_by <> ''",
            name="ck_work_package_revisions_required_text",
        ),
    )

    work_package_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_packages.id"))
    revision: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String)
    source_path: Mapped[str] = mapped_column(String)
    source_commit: Mapped[str] = mapped_column(String)
    approved_by: Mapped[str] = mapped_column(String)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approval_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    enforcement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    authority_fingerprint: Mapped[str] = mapped_column(String)
    registry_version: Mapped[int] = mapped_column(Integer)
    registered_by: Mapped[str] = mapped_column(String)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    work_package: Mapped[WorkPackage] = relationship()


WORK_UNIT_STATES = (
    "draft",
    "ready",
    "claimed",
    "executing",
    "blocked",
    "awaiting_approval",
    "submitted",
    "verifying",
    "awaiting_review",
    "revision_required",
    "completed",
    "failed",
    "cancelled",
)


class WorkUnit(UUIDPrimaryKey, Base):
    __tablename__ = "work_units"
    __table_args__ = (
        UniqueConstraint("work_package_revision_id", "unit_key"),
        CheckConstraint(f"state IN {WORK_UNIT_STATES!r}", name="ck_work_units_state"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 0 AND attempt_count <= max_attempts",
            name="ck_work_units_attempts",
        ),
        CheckConstraint(
            "state = 'draft' OR "
            "(decomposition_approved_by IS NOT NULL AND decomposition_approved_at IS NOT NULL)",
            name="ck_work_units_approved_beyond_draft",
        ),
    )

    unit_key: Mapped[str] = mapped_column(String)
    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    title: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String)
    decomposition_approved_by: Mapped[str | None] = mapped_column(String)
    decomposition_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_capability: Mapped[str] = mapped_column(String)
    authority_fingerprint: Mapped[str] = mapped_column(String)
    authority_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Dependency(UUIDPrimaryKey, Base):
    __tablename__ = "dependencies"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('work_unit', 'external_system', 'pull_request', 'decision')",
            name="ck_dependencies_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'satisfied', 'failed')", name="ck_dependencies_status"
        ),
        CheckConstraint(
            "(depends_on_work_unit_id IS NOT NULL) <> (external_ref IS NOT NULL)",
            name="ck_dependencies_exactly_one_reference",
        ),
        CheckConstraint(
            "depends_on_work_unit_id IS NULL OR depends_on_work_unit_id <> work_unit_id",
            name="ck_dependencies_not_self_referential",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    kind: Mapped[str] = mapped_column(String)
    required_state_or_condition: Mapped[str] = mapped_column(String)
    depends_on_work_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_units.id"))
    external_ref: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    resolved_by: Mapped[str | None] = mapped_column(String)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Claim(UUIDPrimaryKey, Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("work_unit_id", "attempt"),
        UniqueConstraint("work_unit_id", "idempotency_key"),
        CheckConstraint("attempt > 0", name="ck_claims_positive_attempt"),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    attempt: Mapped[int] = mapped_column(Integer)
    claimed_by: Mapped[str] = mapped_column(String)
    lease_token_hash: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String)


class Approval(UUIDPrimaryKey, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('work_unit', 'authority', 'retry', 'action')",
            name="ck_approvals_subject_type",
        ),
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approvals_decision"),
    )

    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    subject_revision_or_fingerprint: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    approved_by: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)


class Evidence(UUIDPrimaryKey, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "stable_ref IS NOT NULL OR payload IS NOT NULL",
            name="ck_evidence_reference_or_payload",
        ),
        CheckConstraint("attempt > 0", name="ck_evidence_positive_attempt"),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    ac_id: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer)
    evidence_type: Mapped[str] = mapped_column(String)
    stable_ref: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_revision: Mapped[str] = mapped_column(String)
    recorded_by: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    supersedes_evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"))


class Adjudication(UUIDPrimaryKey, Base):
    __tablename__ = "adjudications"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'waived', 'not_applicable')",
            name="ck_adjudications_outcome",
        ),
        CheckConstraint(
            "outcome <> 'waived' OR "
            "(failed_evidence_id IS NOT NULL AND length(trim(rationale)) > 0 "
            "AND length(trim(risk)) > 0 AND length(trim(follow_up)) > 0)",
            name="ck_adjudications_waiver_fields",
        ),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    ac_id: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"))
    decided_by: Mapped[str] = mapped_column(String)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rationale: Mapped[str] = mapped_column(Text)
    failed_evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"))
    risk: Mapped[str | None] = mapped_column(Text)
    follow_up: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    supersedes_adjudication_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("adjudications.id")
    )


class Event(UUIDPrimaryKey, Base):
    __tablename__ = "events"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_id: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    from_state: Mapped[str | None] = mapped_column(String)
    to_state: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
