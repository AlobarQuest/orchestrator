"""Add WS-3.3 protocol runtime persistence schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_ws33_protocol_runtime"
down_revision: str | None = "0003_ws32_intake_decomposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    op.drop_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
    )
    op.create_check_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
        "intake_source IN ('manual_ws31', 'package_cli', 'protocol_fixture')",
    )

    op.create_unique_constraint("uq_claims_id_attempt", "claims", ["id", "attempt"])

    op.create_table(
        "context_snapshots",
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
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("context_fingerprint", sa.String(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approvals.id"),
            nullable=True,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        _timestamp("created_at", default=True),
        sa.ForeignKeyConstraint(
            ["claim_id", "attempt"],
            ["claims.id", "claims.attempt"],
            name="fk_context_snapshots_claim_attempt",
        ),
        sa.CheckConstraint(
            "classification IN "
            "('accepted', 'same_scope', 'authority_expanding', 'missing_required', 'stale')",
            name="ck_context_snapshots_classification",
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected', 'requires_approval')",
            name="ck_context_snapshots_decision",
        ),
        sa.CheckConstraint("attempt > 0", name="ck_context_snapshots_positive_attempt"),
    )

    op.add_column(
        "claims",
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "claims",
        sa.Column("execution_context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_claims_context_snapshot_id",
        "claims",
        "context_snapshots",
        ["context_snapshot_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_claims_execution_context_snapshot_id",
        "claims",
        "context_snapshots",
        ["execution_context_snapshot_id"],
        ["id"],
    )

    op.add_column(
        "evidence",
        sa.Column("context_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_evidence_context_snapshot_id",
        "evidence",
        "context_snapshots",
        ["context_snapshot_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_evidence_context_snapshot_id", "evidence", type_="foreignkey")
    op.drop_column("evidence", "context_snapshot_id")

    op.drop_constraint(
        "fk_claims_execution_context_snapshot_id",
        "claims",
        type_="foreignkey",
    )
    op.drop_constraint("fk_claims_context_snapshot_id", "claims", type_="foreignkey")
    op.drop_column("claims", "execution_context_snapshot_id")
    op.drop_column("claims", "context_snapshot_id")

    op.drop_table("context_snapshots")
    op.drop_constraint("uq_claims_id_attempt", "claims", type_="unique")

    op.drop_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
    )
    op.create_check_constraint(
        "ck_work_package_revisions_intake_source",
        "work_package_revisions",
        "intake_source IN ('manual_ws31', 'package_cli')",
    )
