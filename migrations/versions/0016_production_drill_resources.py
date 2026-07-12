"""Add production drill resource ownership.

Revision ID: 0016_production_drill_resources
Revises: 0015_production_drill_runs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_production_drill_resources"
down_revision = "0015_production_drill_runs"
branch_labels = None
depends_on = None

RESOURCE_TYPES = (
    "work_unit",
    "evidence",
    "observation",
    "reconciliation_condition",
    "release_artifact",
    "deployment_observation",
)


def upgrade() -> None:
    op.create_table(
        "production_drill_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_drill_runs.id"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "resource_type", "resource_id", name="uq_production_drill_resources_owner"
        ),
        sa.CheckConstraint(
            f"resource_type IN {RESOURCE_TYPES!r}", name="ck_production_drill_resources_type"
        ),
    )
    op.create_index(
        "ix_production_drill_resources_run_id", "production_drill_resources", ["run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_production_drill_resources_run_id", table_name="production_drill_resources")
    op.drop_table("production_drill_resources")
