"""Add WS-4.4 infra-lane linkage persistence.

Revision ID: 0009_ws44_infra_lane_linkage
Revises: 0008_ws42_dispatch_adapter
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_ws44_infra_lane_linkage"
down_revision = "0008_ws42_dispatch_adapter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "infra_lane_links",
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
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("change_manager_ref", sa.String(), nullable=False),
        sa.Column("change_manager_url", sa.Text(), nullable=True),
        sa.Column("infraops_ref", sa.String(), nullable=True),
        sa.Column("approval_ref", sa.Text(), nullable=True),
        sa.Column("rollback_ref", sa.Text(), nullable=True),
        sa.Column("verify_ref", sa.Text(), nullable=True),
        sa.Column("final_evidence_ref", sa.Text(), nullable=True),
        sa.Column("payload", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint("attempt > 0", name="ck_infra_lane_links_positive_attempt"),
        sa.CheckConstraint(
            "status IN ("
            "'requested', 'approved', 'executing', 'verification_pending', "
            "'completed', 'failed', 'cancelled'"
            ")",
            name="ck_infra_lane_links_status",
        ),
        sa.CheckConstraint(
            "change_manager_ref <> ''",
            name="ck_infra_lane_links_change_manager_ref_required",
        ),
    )


def downgrade() -> None:
    op.drop_table("infra_lane_links")
