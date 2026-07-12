"""Add production drill run authorization records.

Revision ID: 0015_production_drill_runs
Revises: 0014_wsp21_recovery_controls
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_production_drill_runs"
down_revision = "0014_wsp21_recovery_controls"
branch_labels = None
depends_on = None

RUN_STATUSES = ("open", "asserting", "closed", "failed")


def upgrade() -> None:
    op.create_table(
        "production_drill_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("owner_actor_id", sa.String(), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("image_ref", sa.String(), nullable=False),
        sa.Column("image_digest", sa.String(), nullable=False),
        sa.Column("openapi_digest", sa.String(), nullable=False),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(f"status IN {RUN_STATUSES!r}", name="ck_production_drill_runs_status"),
    )
    op.create_index(
        "ix_production_drill_runs_revision_id", "production_drill_runs", ["revision_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_production_drill_runs_revision_id", table_name="production_drill_runs")
    op.drop_table("production_drill_runs")
