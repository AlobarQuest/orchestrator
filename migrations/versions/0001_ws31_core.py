"""Create the WS-3.1 persistent core schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_ws31_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = (
    "work_package_revisions",
    "evidence",
    "adjudications",
    "events",
)


def _uuid_primary_key() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _timestamp(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default else None,
    )


def _frozen_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "work_packages",
        metadata,
        _uuid_primary_key(),
        sa.Column("package_id", sa.String(), nullable=False, unique=True),
        sa.Column("source_repository", sa.String(), nullable=False),
        _timestamp("created_at", default=True),
    )
    sa.Table(
        "work_package_revisions",
        metadata,
        _uuid_primary_key(),
        sa.Column(
            "work_package_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_packages.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("source_commit", sa.String(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=False),
        _timestamp("approved_at"),
        sa.Column("approval_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enforcement_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("authority_fingerprint", sa.String(), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("registered_by", sa.String(), nullable=False),
        _timestamp("registered_at", default=True),
        sa.UniqueConstraint("work_package_id", "revision"),
        sa.UniqueConstraint("work_package_id", "content_hash"),
        sa.CheckConstraint("revision > 0", name="ck_work_package_revisions_positive_revision"),
        sa.CheckConstraint(
            "content_hash <> '' AND source_path <> '' AND source_commit <> '' "
            "AND approved_by <> '' AND registered_by <> ''",
            name="ck_work_package_revisions_required_text",
        ),
    )
    sa.Table(
        "work_units",
        metadata,
        _uuid_primary_key(),
        sa.Column("unit_key", sa.String(), nullable=False),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("decomposition_approved_by", sa.String(), nullable=True),
        _timestamp("decomposition_approved_at", nullable=True),
        sa.Column("required_capability", sa.String(), nullable=False),
        sa.Column("authority_fingerprint", sa.String(), nullable=False),
        sa.Column("authority_approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        sa.UniqueConstraint("work_package_revision_id", "unit_key"),
        sa.CheckConstraint(
            "state IN ('draft', 'ready', 'claimed', 'executing', 'blocked', "
            "'awaiting_approval', 'submitted', 'verifying', 'awaiting_review', "
            "'revision_required', 'completed', 'failed', 'cancelled')",
            name="ck_work_units_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 0 AND attempt_count <= max_attempts",
            name="ck_work_units_attempts",
        ),
        sa.CheckConstraint(
            "state = 'draft' OR "
            "(decomposition_approved_by IS NOT NULL AND decomposition_approved_at IS NOT NULL)",
            name="ck_work_units_approved_beyond_draft",
        ),
    )
    sa.Table(
        "dependencies",
        metadata,
        _uuid_primary_key(),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("required_state_or_condition", sa.String(), nullable=False),
        sa.Column(
            "depends_on_work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=True,
        ),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("resolved_by", sa.String(), nullable=True),
        _timestamp("resolved_at", nullable=True),
        sa.Column("resolution_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('work_unit', 'external_system', 'pull_request', 'decision')",
            name="ck_dependencies_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'satisfied', 'failed')",
            name="ck_dependencies_status",
        ),
        sa.CheckConstraint(
            "(depends_on_work_unit_id IS NOT NULL) <> (external_ref IS NOT NULL)",
            name="ck_dependencies_exactly_one_reference",
        ),
        sa.CheckConstraint(
            "depends_on_work_unit_id IS NULL OR depends_on_work_unit_id <> work_unit_id",
            name="ck_dependencies_not_self_referential",
        ),
    )
    sa.Table(
        "claims",
        metadata,
        _uuid_primary_key(),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=False),
        sa.Column("lease_token_hash", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        _timestamp("acquired_at", default=True),
        _timestamp("renewed_at", nullable=True),
        _timestamp("lease_expires_at"),
        _timestamp("released_at", nullable=True),
        sa.Column("terminal_reason", sa.String(), nullable=True),
        sa.UniqueConstraint("work_unit_id", "attempt"),
        sa.UniqueConstraint("work_unit_id", "idempotency_key"),
        sa.CheckConstraint("attempt > 0", name="ck_claims_positive_attempt"),
    )
    sa.Table(
        "approvals",
        metadata,
        _uuid_primary_key(),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_revision_or_fingerprint", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _timestamp("created_at", default=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.CheckConstraint(
            "subject_type IN ('work_unit', 'authority', 'retry', 'action')",
            name="ck_approvals_subject_type",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_approvals_decision",
        ),
    )
    sa.Table(
        "evidence",
        metadata,
        _uuid_primary_key(),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("ac_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(), nullable=False),
        sa.Column("stable_ref", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("source_revision", sa.String(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=False),
        _timestamp("recorded_at", default=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("supersedes_evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "id",
            "work_package_revision_id",
            "work_unit_id",
            "ac_id",
            name="uq_evidence_supersession_target",
        ),
        sa.ForeignKeyConstraint(
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
        sa.CheckConstraint(
            "stable_ref IS NOT NULL OR payload IS NOT NULL",
            name="ck_evidence_reference_or_payload",
        ),
        sa.CheckConstraint("attempt > 0", name="ck_evidence_positive_attempt"),
    )
    sa.Table(
        "adjudications",
        metadata,
        _uuid_primary_key(),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("ac_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(), nullable=False),
        _timestamp("decided_at", default=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "failed_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            nullable=True,
        ),
        sa.Column("risk", sa.Text(), nullable=True),
        sa.Column("follow_up", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        _timestamp("expires_at", nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "supersedes_adjudication_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("adjudications.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'waived', 'not_applicable')",
            name="ck_adjudications_outcome",
        ),
        sa.CheckConstraint(
            "outcome <> 'waived' OR "
            "(failed_evidence_id IS NOT NULL AND length(trim(rationale)) > 0 "
            "AND length(trim(risk)) > 0 AND length(trim(follow_up)) > 0)",
            name="ck_adjudications_waiver_fields",
        ),
    )
    sa.Table(
        "events",
        metadata,
        _uuid_primary_key(),
        _timestamp("occurred_at", default=True),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", sa.String(), nullable=True),
        sa.Column("to_state", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
    )
    return metadata


def upgrade() -> None:
    _frozen_metadata().create_all(op.get_bind())
    op.execute(
        """
        CREATE FUNCTION reject_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION enforce_work_unit_revision_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.work_package_revision_id <> OLD.work_package_revision_id THEN
            RAISE EXCEPTION 'work_units.work_package_revision_id is immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER enforce_work_unit_revision_immutable "
        "BEFORE UPDATE ON work_units FOR EACH ROW "
        "EXECUTE FUNCTION enforce_work_unit_revision_immutable()"
    )
    op.execute(
        """
        CREATE FUNCTION set_work_unit_updated_at()
        RETURNS trigger AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER set_work_unit_updated_at "
        "BEFORE UPDATE ON work_units FOR EACH ROW "
        "EXECUTE FUNCTION set_work_unit_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER set_work_unit_updated_at ON work_units")
    op.execute("DROP FUNCTION set_work_unit_updated_at()")
    op.execute("DROP TRIGGER enforce_work_unit_revision_immutable ON work_units")
    op.execute("DROP FUNCTION enforce_work_unit_revision_immutable()")
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER reject_{table}_mutation ON {table}")
    op.execute("DROP FUNCTION reject_append_only_mutation()")
    _frozen_metadata().drop_all(op.get_bind())
