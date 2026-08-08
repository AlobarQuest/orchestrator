import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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

from orchestrator.kernel.states import WAIVER_RISK_CLASSES


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
DISPATCH_RECORD_STATUSES = ("dispatched", "skipped", "blocked", "failed")
INFRA_LANE_LINK_STATUSES = (
    "requested",
    "approved",
    "executing",
    "verification_pending",
    "completed",
    "failed",
    "cancelled",
)
OBSERVATION_SOURCE_SYSTEMS = (
    "deployment_observation",
    "watchtower",
    "ops_dashboard",
    "healthchecks",
    "uptime_monitor",
    "github",
    "drift_digest",
)
OBSERVATION_TRUST_CLASSIFICATIONS = ("orchestrator", "delivery_system", "monitor", "external")
OBSERVATION_SUBJECT_TYPES = (
    "service",
    "repo",
    "deployment",
    "release_binding",
    "deployment_observation",
    "work_unit",
    "package_revision",
    "endpoint",
    "monitor",
    "external_run",
)
OBSERVATION_TYPES = (
    "deployment",
    "health",
    "uptime",
    "github_check",
    "github_pr",
    "drift",
    "metric",
    "alert",
    "inventory",
    # A commit reached a repository's default branch, however it got there. The ROUTE it took
    # -- auto-merged, merged by a person, pushed at the branch -- is `permitted_by` on the
    # facts, not a second observation type. Deliberately not `github_pr`, which already means
    # "a fact about a pull request bound to a work unit" in the reconciliation lane
    # (`reconciliation_runner/facts.py`, `services/reconciliation_detection.py`); the two
    # never collide today only because their subject_reference namespaces are disjoint, which
    # is a coincidence to rely on rather than a design.
    "landing",
    # One repository's answer from one pass of the landing audit (WS-P3.6 Increment 3): were the
    # landings the rule permitted actually within it, and is anything eligible and green sitting
    # unlanded. A separate type from `landing` because it is a JUDGMENT about records rather than
    # a record, and a row is written every pass whether or not anything was found -- so its
    # ABSENCE is the signal that the audit stopped running.
    "landing_audit",
)
OBSERVATION_STATUSES = (
    "passed",
    "failed",
    "degraded",
    "healthy",
    "unhealthy",
    "unknown",
    "observed",
)
OBSERVATION_SEVERITIES = ("info", "warning", "critical")
KNOWLEDGE_PROMOTION_TARGET_BRAINS = ("code", "infra")
KNOWLEDGE_PROMOTION_TARGET_TYPES = ("lesson", "rule")
KNOWLEDGE_PROMOTION_AUTHORITIES = ("informational", "recommended", "required")
KNOWLEDGE_PROMOTION_ACTIONS = ("submitted_to_brain", "rejected")

# `release_artifacts` writes one evidence row per binding under this constant ac_id, always with
# supersedes_evidence_id IS NULL. A unit may legitimately carry several bindings, so this triple
# genuinely has MANY unsuperseded rows -- it is append-only bookkeeping, never superseded, never
# adjudicated, never passed to current_evidence()/_terminal(). The single-head index carves it out.
EVIDENCE_HEAD_BOOKKEEPING_AC_ID = "release-artifact"


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
    # The package's declared follow-up block (WS-P2.8). NULL means the revision predates the
    # column -- distinguishable from a declaration that says `required: false`, which matters
    # because the first can never be recovered and the second is a real answer.
    follow_up: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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
    # Governed knowledge resolved at authoring time (WS-P2.12). Write-once: what a
    # worker was told is a record of what the unit executed under, so a path that
    # rewrote it would mean the stored document is no longer the approved one.
    # NULL means "predates enrichment"; an empty document means "enriched, and the
    # brains held nothing" -- the two must not collapse.
    context_enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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
        # Exactly one unsuperseded head per (revision, unit, ac) -- for evidence that
        # participates in supersession. _store_evidence's evidence_already_exists check is the
        # only code preventing a second head, and any writer that bypasses it (evidence recovery
        # must, because _validate_attempt rejects a SYSTEM actor with an expired lease) would
        # fork the supersession chain. Two heads make _terminal raise, so the AC can never be
        # adjudicated and no further evidence can be written -- and this table is append-only, so
        # the row could never be repaired and the unit could never complete.
        #
        # `release_artifacts` is carved out: it writes one row per binding under the constant
        # ac_id 'release-artifact', and a unit may legitimately carry several bindings, so that
        # triple genuinely has MANY unsuperseded rows. Those rows are never superseded, never
        # adjudicated, and never passed to current_evidence()/_terminal(). Including them breaks
        # every multi-binding unit.
        Index(
            "uq_evidence_unsuperseded_head",
            "work_package_revision_id",
            "work_unit_id",
            "ac_id",
            unique=True,
            postgresql_where=text(
                f"supersedes_evidence_id IS NULL AND ac_id <> '{EVIDENCE_HEAD_BOOKKEEPING_AC_ID}'"
            ),
        ),
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
        CheckConstraint(
            "risk IS NULL OR risk IN ("
            + ", ".join(f"'{value}'" for value in WAIVER_RISK_CLASSES)
            + ")",
            name="ck_adjudications_risk_class",
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
    improvisation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class DispatchRecord(UUIDPrimaryKey, Base):
    __tablename__ = "dispatch_records"
    __table_args__ = (
        UniqueConstraint("work_unit_id", "runner_attempt"),
        CheckConstraint("runner_attempt > 0", name="ck_dispatch_records_positive_attempt"),
        CheckConstraint(
            f"status IN {DISPATCH_RECORD_STATUSES!r}",
            name="ck_dispatch_records_status",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    runner_attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    reason_code: Mapped[str | None] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    target_repository: Mapped[str] = mapped_column(String)
    workflow_id: Mapped[str] = mapped_column(String)
    workflow_ref: Mapped[str] = mapped_column(String)
    github_run_id: Mapped[str | None] = mapped_column(String)
    github_run_url: Mapped[str | None] = mapped_column(Text)
    failure_signature: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InfraLaneLink(UUIDPrimaryKey, Base):
    __tablename__ = "infra_lane_links"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint("attempt > 0", name="ck_infra_lane_links_positive_attempt"),
        CheckConstraint(
            f"status IN {INFRA_LANE_LINK_STATUSES!r}",
            name="ck_infra_lane_links_status",
        ),
        CheckConstraint(
            "change_manager_ref <> ''",
            name="ck_infra_lane_links_change_manager_ref_required",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    change_manager_ref: Mapped[str] = mapped_column(String)
    change_manager_url: Mapped[str | None] = mapped_column(Text)
    infraops_ref: Mapped[str | None] = mapped_column(String)
    approval_ref: Mapped[str | None] = mapped_column(Text)
    rollback_ref: Mapped[str | None] = mapped_column(Text)
    verify_ref: Mapped[str | None] = mapped_column(Text)
    final_evidence_ref: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    recorded_by: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class ReleaseArtifactBinding(UUIDPrimaryKey, Base):
    __tablename__ = "release_artifact_bindings"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint(
            "work_package_revision_id",
            "work_unit_id",
            "source_repository",
            "merge_commit",
            "source_commit",
            "artifact_registry",
            "artifact_repository",
            "artifact_name",
            name="uq_release_artifact_source_tuple",
        ),
        CheckConstraint(
            "implementation_pr_number IS NULL OR implementation_pr_number > 0",
            name="ck_release_artifact_positive_pr",
        ),
        CheckConstraint(
            "workflow_run_attempt IS NULL OR workflow_run_attempt > 0",
            name="ck_release_artifact_positive_workflow_attempt",
        ),
        CheckConstraint(
            "package_revision_hash <> '' AND source_repository <> '' "
            "AND source_commit <> '' AND merge_commit <> '' "
            "AND artifact_registry <> '' AND artifact_repository <> '' "
            "AND artifact_name <> '' AND artifact_digest <> ''",
            name="ck_release_artifact_required_text",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    package_revision_hash: Mapped[str] = mapped_column(String)
    source_repository: Mapped[str] = mapped_column(String)
    implementation_pr_number: Mapped[int | None] = mapped_column(Integer)
    source_commit: Mapped[str] = mapped_column(String)
    merge_commit: Mapped[str] = mapped_column(String)
    artifact_registry: Mapped[str] = mapped_column(String)
    artifact_repository: Mapped[str] = mapped_column(String)
    artifact_name: Mapped[str] = mapped_column(String)
    artifact_digest: Mapped[str] = mapped_column(String)
    artifact_tag: Mapped[str | None] = mapped_column(String)
    workflow_run_id: Mapped[str | None] = mapped_column(String)
    workflow_run_attempt: Mapped[int | None] = mapped_column(Integer)
    workflow_path: Mapped[str | None] = mapped_column(Text)
    workflow_ref: Mapped[str | None] = mapped_column(Text)
    workflow_run_url: Mapped[str | None] = mapped_column(Text)
    builder_id: Mapped[str | None] = mapped_column(String)
    builder_class: Mapped[str | None] = mapped_column(String)
    provenance_ref: Mapped[str | None] = mapped_column(Text)
    provenance_digest: Mapped[str | None] = mapped_column(String)
    sbom_ref: Mapped[str | None] = mapped_column(Text)
    sbom_digest: Mapped[str | None] = mapped_column(String)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    recorded_by: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class DeploymentObservation(UUIDPrimaryKey, Base):
    __tablename__ = "deployment_observations"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint(
            "release_artifact_binding_id",
            "environment",
            name="uq_deployment_observation_binding_environment",
        ),
        CheckConstraint(
            "package_revision_hash <> '' AND environment <> '' AND base_url <> '' "
            "AND observed_artifact_digest <> '' AND deployment_ref <> '' "
            "AND deployment_url <> '' AND deployer <> ''",
            name="ck_deployment_observations_required_text",
        ),
    )

    release_artifact_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("release_artifact_bindings.id")
    )
    implementation_work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    work_package_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_package_revisions.id")
    )
    package_revision_hash: Mapped[str] = mapped_column(String)
    post_deploy_work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    environment: Mapped[str] = mapped_column(String)
    base_url: Mapped[str] = mapped_column(Text)
    observed_artifact_digest: Mapped[str] = mapped_column(String)
    deployment_ref: Mapped[str] = mapped_column(Text)
    deployment_url: Mapped[str] = mapped_column(Text)
    deployer: Mapped[str] = mapped_column(String)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    probe_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    route_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    auth_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    dispatch_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    recorded_by: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    post_deploy_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    idempotency_key: Mapped[str] = mapped_column(String)


class Observation(UUIDPrimaryKey, Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint(
            "source_system",
            "source_reference",
            "normalized_fact_hash",
            name="uq_observations_source_fact",
        ),
        CheckConstraint(
            f"source_system IN {OBSERVATION_SOURCE_SYSTEMS!r}",
            name="ck_observations_source_system",
        ),
        CheckConstraint(
            f"trust_classification IN {OBSERVATION_TRUST_CLASSIFICATIONS!r}",
            name="ck_observations_trust_classification",
        ),
        CheckConstraint(
            f"subject_type IN {OBSERVATION_SUBJECT_TYPES!r}",
            name="ck_observations_subject_type",
        ),
        CheckConstraint(
            f"observation_type IN {OBSERVATION_TYPES!r}",
            name="ck_observations_type",
        ),
        CheckConstraint(
            f"status IN {OBSERVATION_STATUSES!r}",
            name="ck_observations_status",
        ),
        CheckConstraint(
            f"severity IN {OBSERVATION_SEVERITIES!r}",
            name="ck_observations_severity",
        ),
        CheckConstraint(
            "source_reference <> '' AND subject_reference <> '' AND summary <> '' "
            "AND normalized_fact_hash <> '' AND recorded_by <> '' AND idempotency_key <> ''",
            name="ck_observations_required_text",
        ),
    )

    source_system: Mapped[str] = mapped_column(String)
    source_reference: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    trust_classification: Mapped[str] = mapped_column(String)
    subject_type: Mapped[str] = mapped_column(String)
    subject_reference: Mapped[str] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(String)
    observation_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    normalized_fact_hash: Mapped[str] = mapped_column(String)
    payload_digest: Mapped[str | None] = mapped_column(String)
    recorded_by: Mapped[str] = mapped_column(String)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


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


class KnowledgePromotionProposal(UUIDPrimaryKey, Base):
    __tablename__ = "knowledge_promotion_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("proposal_hash"),
        UniqueConstraint("correlation_identity"),
        CheckConstraint(
            f"target_brain IN {KNOWLEDGE_PROMOTION_TARGET_BRAINS!r}",
            name="ck_knowledge_promotion_proposals_target_brain",
        ),
        CheckConstraint(
            f"target_type IN {KNOWLEDGE_PROMOTION_TARGET_TYPES!r}",
            name="ck_knowledge_promotion_proposals_target_type",
        ),
        CheckConstraint(
            f"authority IN {KNOWLEDGE_PROMOTION_AUTHORITIES!r}",
            name="ck_knowledge_promotion_proposals_authority",
        ),
        CheckConstraint(
            "correlation_identity <> '' AND correlation_summary <> '' "
            "AND proposed_by <> '' AND idempotency_key <> '' AND proposal_hash <> ''",
            name="ck_knowledge_promotion_proposals_required_text",
        ),
    )

    correlation_identity: Mapped[str] = mapped_column(String)
    source_observation_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    source_observation_hashes: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    release_artifact_binding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deployment_observation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    work_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    package_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    correlation_summary: Mapped[str] = mapped_column(Text)
    target_brain: Mapped[str] = mapped_column(String)
    target_type: Mapped[str] = mapped_column(String)
    authority: Mapped[str] = mapped_column(String)
    applicability: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    proposed_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    proposal_hash: Mapped[str] = mapped_column(String)
    proposed_by: Mapped[str] = mapped_column(String)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class KnowledgePromotionProposalAction(UUIDPrimaryKey, Base):
    __tablename__ = "knowledge_promotion_proposal_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("event_id"),
        UniqueConstraint("proposal_id", "action"),
        CheckConstraint(
            f"action IN {KNOWLEDGE_PROMOTION_ACTIONS!r}",
            name="ck_knowledge_promotion_proposal_actions_action",
        ),
        CheckConstraint(
            "action_by <> '' AND idempotency_key <> ''",
            name="ck_knowledge_promotion_proposal_actions_required_text",
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_promotion_proposals.id"))
    action: Mapped[str] = mapped_column(String)
    brain_record_id: Mapped[str | None] = mapped_column(String)
    brain_status: Mapped[str | None] = mapped_column(String)
    brain_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    action_by: Mapped[str] = mapped_column(String)
    action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


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
    context_enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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


TRACKER_SYSTEMS = ("todoist",)
RECONCILIATION_OBSERVATION_KINDS = ("github_pr", "github_check", "deployment", "tracker")
RECONCILIATION_CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
    "tracker_state_divergence",
)
RECONCILIATION_DECISIONS = ("accepted", "corrected", "dismissed")


class ReconciliationCondition(UUIDPrimaryKey, Base):
    """A recorded divergence between pushed reality and stored lifecycle state.

    Append-only. Recording one never writes `work_units` and never transitions: detection
    surfaces a condition for an operator decision, it never auto-un-completes a completed unit
    and never auto-resolves.
    """

    __tablename__ = "reconciliation_conditions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reconciliation_conditions_idempotency"),
        UniqueConstraint(
            "work_unit_id",
            "observation_kind",
            "normalized_divergence_hash",
            name="uq_reconciliation_conditions_divergence",
        ),
        CheckConstraint(
            f"observation_kind IN {RECONCILIATION_OBSERVATION_KINDS!r}",
            name="ck_reconciliation_conditions_observation_kind",
        ),
        CheckConstraint(
            f"condition_type IN {RECONCILIATION_CONDITION_TYPES!r}",
            name="ck_reconciliation_conditions_type",
        ),
        CheckConstraint(
            "resolution_generation >= 0", name="ck_reconciliation_conditions_generation"
        ),
        CheckConstraint(
            "lineage_hash <> '' AND normalized_divergence_hash <> '' "
            "AND detail <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_conditions_required_text",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    observation_kind: Mapped[str] = mapped_column(String)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("observations.id"))
    deployment_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployment_observations.id")
    )
    condition_type: Mapped[str] = mapped_column(String)
    stored_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    observed_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Generation-free identity of a divergence, so the resolution count for a lineage is a plain
    # equality join. The divergence hash below folds the generation in, so it cannot group.
    lineage_hash: Mapped[str] = mapped_column(String)
    resolution_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    normalized_divergence_hash: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class ReconciliationResolution(UUIDPrimaryKey, Base):
    """An operator's decision on a condition. Append-only; a condition resolves exactly once.

    An OPEN condition is one with no resolution row -- a set difference, mirroring evidence
    supersession. There is no status column to drift.
    """

    __tablename__ = "reconciliation_resolutions"
    __table_args__ = (
        UniqueConstraint("condition_id", name="uq_reconciliation_resolutions_condition"),
        UniqueConstraint("idempotency_key", name="uq_reconciliation_resolutions_idempotency"),
        CheckConstraint(
            f"decision IN {RECONCILIATION_DECISIONS!r}",
            name="ck_reconciliation_resolutions_decision",
        ),
        CheckConstraint(
            "resolved_by <> '' AND rationale <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_resolutions_required_text",
        ),
    )

    condition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_conditions.id"))
    resolved_by: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class UnitPrBinding(Base):
    """A work unit's pull-request head.

    Deliberately NOT append-only. `head_sha` is mutable and worker-written: a rebase or
    force-push before verification is normal and must not alarm.
    `verification_read_head_sha` is the alarm-arming field, and it is write-once WITHIN a
    verification cycle -- keyed by `verification_read_attempt` -- enforced by the service guard
    rather than a trigger, which is exactly why the row must stay UPDATE-able for `head_sha`. A
    later worker push therefore cannot disarm the alarm for the cycle already under way, while
    the next attempt's submit legitimately re-arms it.
    """

    __tablename__ = "unit_pr_binding"
    __table_args__ = (
        CheckConstraint("pr_number > 0", name="ck_unit_pr_binding_positive_pr_number"),
        CheckConstraint("head_sha <> ''", name="ck_unit_pr_binding_head_sha"),
        CheckConstraint(
            "(verification_read_head_sha IS NULL) = (verification_read_attempt IS NULL)",
            name="ck_unit_pr_binding_armed_head_has_attempt",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"), primary_key=True)
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String)
    # The claim attempt this binding was written for. Nullable: a SYSTEM repair may write without
    # one, which fails the submit guard closed. Distinct from `verification_read_attempt`, which
    # keys the write-once armed head; this keys "is there a CURRENT-attempt binding to submit on".
    binding_attempt: Mapped[int | None] = mapped_column(Integer)
    verification_read_head_sha: Mapped[str | None] = mapped_column(String)
    verification_read_attempt: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UnitTrackerBinding(Base):
    """A work unit's projection onto an external tracker item.

    Projection only and one-directional: the orchestrator is always canonical and this row
    records merely THAT a unit is mirrored to some external tracker item. It carries no
    lifecycle authority, and writing it never changes work-unit state. Mutable, one row per
    unit (PK on work_unit_id), mirroring UnitPrBinding.
    """

    __tablename__ = "unit_tracker_bindings"
    __table_args__ = (
        # Built via join, not `{TRACKER_SYSTEMS!r}`: a single-element tuple's repr carries a
        # trailing comma (`('todoist',)`), which is invalid inside a SQL IN (...) list.
        CheckConstraint(
            "tracker_system IN ({})".format(", ".join(f"'{s}'" for s in TRACKER_SYSTEMS)),
            name="ck_unit_tracker_bindings_tracker_system",
        ),
        CheckConstraint(
            "external_item_id <> ''",
            name="ck_unit_tracker_bindings_external_item_id",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"), primary_key=True)
    tracker_system: Mapped[str] = mapped_column(String)
    external_item_id: Mapped[str] = mapped_column(String)
    external_url: Mapped[str | None] = mapped_column(String)
    projected_state: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
