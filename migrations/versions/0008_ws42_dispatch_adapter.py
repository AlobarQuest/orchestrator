"""Add WS-4.2 dispatch adapter persistence.

Revision ID: 0008_ws42_dispatch_adapter
Revises: 0007_work_unit_authority
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_ws42_dispatch_adapter"
down_revision = "0007_work_unit_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "dispatch_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("runner_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("target_repository", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("workflow_ref", sa.String(), nullable=False),
        sa.Column("github_run_id", sa.String(), nullable=True),
        sa.Column("github_run_url", sa.Text(), nullable=True),
        sa.Column("failure_signature", sa.String(), nullable=True),
        sa.Column("payload", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("work_unit_id", "runner_attempt"),
        sa.CheckConstraint("runner_attempt > 0", name="ck_dispatch_records_positive_attempt"),
        sa.CheckConstraint(
            "status IN ('dispatched', 'skipped', 'blocked', 'failed')",
            name="ck_dispatch_records_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("dispatch_records")
