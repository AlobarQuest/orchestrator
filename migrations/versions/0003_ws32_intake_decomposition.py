"""Add WS-3.2 package intake and decomposition schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ws32_intake_decomposition"
down_revision: str | None = "0002_default_max_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("package_acceptance_criteria",)


def _uuid_primary_key() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _timestamp(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default else None,
    )


def upgrade() -> None:
    op.add_column(
        "work_package_revisions",
        sa.Column("profile", sa.String(), nullable=True),
    )
    op.add_column(
        "work_package_revisions",
        sa.Column("status_at_intake", sa.String(), nullable=True),
    )
    op.add_column(
        "work_package_revisions",
        sa.Column(
            "intake_source",
            sa.String(),
            nullable=False,
            server_default="manual_ws31",
        ),
    )
    op.add_column(
        "work_package_revisions",
        sa.Column("approval_ledger_commit", sa.String(), nullable=True),
    )
    op.add_column(
        "work_package_revisions",
        sa.Column("verification_mode", sa.String(), nullable=True),
    )
    op.add_column(
        "work_package_revisions",
        sa.Column("verification_limitations", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
        "intake_source IN ('manual_ws31', 'package_cli')",
    )
    op.create_check_constraint(
        "ck_work_package_revisions_verification_mode",
        "work_package_revisions",
        "verification_mode IS NULL OR verification_mode IN ('caller_attested_cli_verified')",
    )
    op.create_check_constraint(
        "ck_work_package_revisions_package_cli_verification_mode",
        "work_package_revisions",
        "("
        "intake_source <> 'package_cli' OR "
        "COALESCE(verification_mode = 'caller_attested_cli_verified', FALSE)"
        ")",
    )

    op.create_table(
        "package_acceptance_criteria",
        _uuid_primary_key(),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("ac_id", sa.String(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("approver", sa.String(), nullable=False),
        _timestamp("created_at", default=True),
        sa.UniqueConstraint("work_package_revision_id", "ac_id"),
        sa.CheckConstraint(
            "ac_id <> '' AND condition <> '' AND evidence_type <> '' "
            "AND evidence <> '' AND approver <> ''",
            name="ck_package_acceptance_criteria_required_text",
        ),
    )

    op.create_table(
        "decomposition_proposals",
        _uuid_primary_key(),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("proposal_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(), nullable=False),
        sa.Column("proposed_actor_role", sa.String(), nullable=False),
        _timestamp("proposed_at", default=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        _timestamp("decided_at", nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_work_unit_ids", postgresql.JSONB(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        _timestamp("created_at", default=True),
        sa.UniqueConstraint("work_package_revision_id", "proposal_number"),
        sa.UniqueConstraint("id", "work_package_revision_id"),
        sa.CheckConstraint(
            "state IN ('proposed', 'approved', 'rejected', 'revision_required')",
            name="ck_decomposition_proposals_state",
        ),
        sa.CheckConstraint("rationale <> ''", name="ck_decomposition_proposals_rationale"),
    )

    op.create_table(
        "decomposition_proposal_units",
        _uuid_primary_key(),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decomposition_proposals.id"),
            nullable=False,
        ),
        sa.Column("unit_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("required_capability", sa.String(), nullable=False),
        sa.Column("authority", postgresql.JSONB(), nullable=False),
        sa.Column("authority_fingerprint", sa.String(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.UniqueConstraint("proposal_id", "unit_key"),
        sa.CheckConstraint(
            "max_attempts >= 0",
            name="ck_decomposition_proposal_units_attempts",
        ),
    )
    op.create_table(
        "decomposition_proposal_dependencies",
        _uuid_primary_key(),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decomposition_proposals.id"),
            nullable=False,
        ),
        sa.Column("source_unit_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("target_unit_key", sa.String(), nullable=True),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("required_state_or_condition", sa.String(), nullable=False),
        sa.CheckConstraint(
            "(target_unit_key IS NOT NULL) <> (external_ref IS NOT NULL)",
            name="ck_decomposition_proposal_dependencies_reference",
        ),
    )
    op.create_table(
        "decomposition_proposal_ac_mappings",
        _uuid_primary_key(),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decomposition_proposals.id"),
            nullable=False,
        ),
        sa.Column(
            "package_acceptance_criterion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("package_acceptance_criteria.id"),
            nullable=False,
        ),
        sa.Column("unit_key", sa.String(), nullable=False),
        sa.UniqueConstraint("proposal_id", "package_acceptance_criterion_id", "unit_key"),
    )
    op.create_table(
        "decomposition_proposal_retained_acs",
        _uuid_primary_key(),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decomposition_proposals.id"),
            nullable=False,
        ),
        sa.Column(
            "package_acceptance_criterion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("package_acceptance_criteria.id"),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.UniqueConstraint("proposal_id", "package_acceptance_criterion_id"),
        sa.CheckConstraint(
            "rationale <> ''",
            name="ck_decomposition_proposal_retained_acs_rationale",
        ),
    )
    op.create_table(
        "approved_decompositions",
        _uuid_primary_key(),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("approved_by", sa.String(), nullable=False),
        _timestamp("approved_at", default=True),
        _timestamp("superseded_at", nullable=True),
        sa.Column("superseded_by", sa.String(), nullable=True),
        sa.Column("supersession_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["proposal_id", "work_package_revision_id"],
            ["decomposition_proposals.id", "decomposition_proposals.work_package_revision_id"],
            name="fk_approved_decompositions_proposal_revision",
        ),
    )
    op.create_index(
        "uq_approved_decompositions_active_revision",
        "approved_decompositions",
        ["work_package_revision_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER reject_{table}_mutation ON {table}")
    op.drop_index(
        "uq_approved_decompositions_active_revision",
        table_name="approved_decompositions",
    )
    op.drop_table("approved_decompositions")
    op.drop_table("decomposition_proposal_retained_acs")
    op.drop_table("decomposition_proposal_ac_mappings")
    op.drop_table("decomposition_proposal_dependencies")
    op.drop_table("decomposition_proposal_units")
    op.drop_table("decomposition_proposals")
    op.drop_table("package_acceptance_criteria")
    op.drop_constraint(
        "ck_work_package_revisions_package_cli_verification_mode",
        "work_package_revisions",
    )
    op.drop_constraint(
        "ck_work_package_revisions_verification_mode",
        "work_package_revisions",
    )
    op.drop_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
    )
    op.drop_column("work_package_revisions", "verification_limitations")
    op.drop_column("work_package_revisions", "verification_mode")
    op.drop_column("work_package_revisions", "approval_ledger_commit")
    op.drop_column("work_package_revisions", "intake_source")
    op.drop_column("work_package_revisions", "status_at_intake")
    op.drop_column("work_package_revisions", "profile")
