import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


PROPOSAL_STATES = ("proposed", "approved", "rejected", "revision_required")
INTAKE_SOURCES = ("manual_ws31", "package_cli", "protocol_fixture")
VERIFICATION_MODES = ("caller_attested_cli_verified",)
CONTEXT_CLASSIFICATIONS = (
    "accepted",
    "same_scope",
    "authority_expanding",
    "missing_required",
    "stale",
)
CONTEXT_DECISIONS = ("accepted", "rejected", "requires_approval")
EVENT_PUBLICATION_KINDS = ("event", "evidence", "adjudication", "context_snapshot")
EVENT_PUBLICATION_STATUSES = (
    "pending",
    "exported",
    "published",
    "skipped",
    "rejected",
    "failed",
)


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
            f"intake_source IN {INTAKE_SOURCES!r}",
            name="ck_work_package_revisions_intake_source",
        ),
        CheckConstraint(
            f"verification_mode IS NULL OR verification_mode IN {VERIFICATION_MODES!r}",
            name="ck_work_package_revisions_verification_mode",
        ),
        CheckConstraint(
            "("
            "intake_source <> 'package_cli' OR "
            "COALESCE(verification_mode = 'caller_attested_cli_verified', FALSE)"
            ")",
            name="ck_work_package_revisions_package_cli_verification_mode",
        ),
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
    approval_event_id: Mapped[str] = mapped_column(String)
    enforcement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    authority_fingerprint: Mapped[str] = mapped_column(String)
    registry_version: Mapped[int] = mapped_column(Integer)
    registered_by: Mapped[str] = mapped_column(String)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    profile: Mapped[str | None] = mapped_column(String)
    status_at_intake: Mapped[str | None] = mapped_column(String)
    intake_source: Mapped[str] = mapped_column(String, server_default="manual_ws31")
    approval_ledger_commit: Mapped[str | None] = mapped_column(String)
    verification_mode: Mapped[str | None] = mapped_column(String)
    verification_limitations: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
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
    authority: Mapped[dict[str, Any]] = mapped_column(JSONB)
    authority_fingerprint: Mapped[str] = mapped_column(String)
    authority_approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
        UniqueConstraint("id", "attempt", name="uq_claims_id_attempt"),
        UniqueConstraint("work_unit_id", "attempt"),
        UniqueConstraint("work_unit_id", "idempotency_key"),
        CheckConstraint("attempt > 0", name="ck_claims_positive_attempt"),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    attempt: Mapped[int] = mapped_column(Integer)
    claimed_by: Mapped[str] = mapped_column(String)
    lease_token_hash: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_snapshots.id")
    )
    execution_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_snapshots.id")
    )
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


class ContextSnapshot(UUIDPrimaryKey, Base):
    __tablename__ = "context_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "attempt"],
            ["claims.id", "claims.attempt"],
            name="fk_context_snapshots_claim_attempt",
        ),
        CheckConstraint(
            f"classification IN {CONTEXT_CLASSIFICATIONS!r}",
            name="ck_context_snapshots_classification",
        ),
        CheckConstraint(
            f"decision IN {CONTEXT_DECISIONS!r}",
            name="ck_context_snapshots_decision",
        ),
        CheckConstraint("attempt > 0", name="ck_context_snapshots_positive_attempt"),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String)
    actor_role: Mapped[str] = mapped_column(String)
    context: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB)
    context_fingerprint: Mapped[str] = mapped_column(String)
    classification: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Evidence(UUIDPrimaryKey, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "work_package_revision_id",
            "work_unit_id",
            "ac_id",
            name="uq_evidence_supersession_target",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_evidence_id",
                "work_package_revision_id",
                "work_unit_id",
                "ac_id",
            ],
            [
                "evidence.id",
                "evidence.work_package_revision_id",
                "evidence.work_unit_id",
                "evidence.ac_id",
            ],
            name="fk_evidence_supersession_scope",
        ),
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
    supersedes_evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_snapshots.id")
    )


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


class EventPublication(UUIDPrimaryKey, Base):
    __tablename__ = "event_publications"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "mapping_version"),
        UniqueConstraint("event_id"),
        CheckConstraint(
            "source_system = 'orchestrator'",
            name="ck_event_publications_source_system",
        ),
        CheckConstraint(
            f"source_kind IN {EVENT_PUBLICATION_KINDS!r}",
            name="ck_event_publications_source_kind",
        ),
        CheckConstraint(
            f"status IN {EVENT_PUBLICATION_STATUSES!r}",
            name="ck_event_publications_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_event_publications_attempt_count"),
    )

    source_system: Mapped[str] = mapped_column(
        String,
        default="orchestrator",
        server_default="orchestrator",
    )
    source_kind: Mapped[str] = mapped_column(String)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_action: Mapped[str | None] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    mapping_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    factory_event: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    export_ref: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PackageAcceptanceCriterion(UUIDPrimaryKey, Base):
    __tablename__ = "package_acceptance_criteria"
    __table_args__ = (
        UniqueConstraint("work_package_revision_id", "ac_id"),
        CheckConstraint(
            "ac_id <> '' AND condition <> '' AND evidence_type <> '' "
            "AND evidence <> '' AND approver <> ''",
            name="ck_package_acceptance_criteria_required_text",
        ),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    ac_id: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String)
    evidence: Mapped[str] = mapped_column(Text)
    approver: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DecompositionProposal(UUIDPrimaryKey, Base):
    __tablename__ = "decomposition_proposals"
    __table_args__ = (
        UniqueConstraint("work_package_revision_id", "proposal_number"),
        UniqueConstraint("id", "work_package_revision_id"),
        CheckConstraint(
            f"state IN {PROPOSAL_STATES!r}",
            name="ck_decomposition_proposals_state",
        ),
        CheckConstraint("rationale <> ''", name="ck_decomposition_proposals_rationale"),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    proposal_number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String)
    proposed_actor_role: Mapped[str] = mapped_column(String)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_by: Mapped[str | None] = mapped_column(String)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_work_unit_ids: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DecompositionProposalUnit(UUIDPrimaryKey, Base):
    __tablename__ = "decomposition_proposal_units"
    __table_args__ = (
        UniqueConstraint("proposal_id", "unit_key"),
        CheckConstraint(
            "max_attempts >= 0",
            name="ck_decomposition_proposal_units_attempts",
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decomposition_proposals.id"))
    unit_key: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(Text)
    required_capability: Mapped[str] = mapped_column(String)
    authority: Mapped[dict[str, Any]] = mapped_column(JSONB)
    authority_fingerprint: Mapped[str] = mapped_column(String)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")


class DecompositionProposalDependency(UUIDPrimaryKey, Base):
    __tablename__ = "decomposition_proposal_dependencies"
    __table_args__ = (
        CheckConstraint(
            "(target_unit_key IS NOT NULL) <> (external_ref IS NOT NULL)",
            name="ck_decomposition_proposal_dependencies_reference",
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decomposition_proposals.id"))
    source_unit_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    target_unit_key: Mapped[str | None] = mapped_column(String)
    external_ref: Mapped[str | None] = mapped_column(String)
    required_state_or_condition: Mapped[str] = mapped_column(String)


class DecompositionProposalAcMapping(UUIDPrimaryKey, Base):
    __tablename__ = "decomposition_proposal_ac_mappings"
    __table_args__ = (
        UniqueConstraint("proposal_id", "package_acceptance_criterion_id", "unit_key"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decomposition_proposals.id"))
    package_acceptance_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("package_acceptance_criteria.id")
    )
    unit_key: Mapped[str] = mapped_column(String)


class DecompositionProposalRetainedAc(UUIDPrimaryKey, Base):
    __tablename__ = "decomposition_proposal_retained_acs"
    __table_args__ = (
        UniqueConstraint("proposal_id", "package_acceptance_criterion_id"),
        CheckConstraint(
            "rationale <> ''",
            name="ck_decomposition_proposal_retained_acs_rationale",
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decomposition_proposals.id"))
    package_acceptance_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("package_acceptance_criteria.id")
    )
    rationale: Mapped[str] = mapped_column(Text)


class ApprovedDecomposition(UUIDPrimaryKey, Base):
    __tablename__ = "approved_decompositions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["proposal_id", "work_package_revision_id"],
            ["decomposition_proposals.id", "decomposition_proposals.work_package_revision_id"],
            name="fk_approved_decompositions_proposal_revision",
        ),
        Index(
            "uq_approved_decompositions_active_revision",
            "work_package_revision_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    approved_by: Mapped[str] = mapped_column(String)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String)
    supersession_reason: Mapped[str | None] = mapped_column(Text)
