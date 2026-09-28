"""Drop `infra_lane_links`, whose ingress Tier 3 item 24a deleted.

The WS-4.4 infra-lane linkage route had no caller in any repository and production held zero
rows on 2026-09-28, so the table is dropped rather than kept. Its two siblings retired by the
same item -- `event_publications` and the `knowledge_promotion_*` pair -- are deliberately NOT
dropped: their production rows are cited by UUID as Phase-6 exit evidence
(~/docs/software-delivery-system/2026-07-09-ws63-governed-promotion-closeout-evidence.md).

The downgrade recreates the table exactly as 0009 created it, empty.

Revision ID: 0038_t3_drop_infra_lane_links
Revises: 0037_events_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_t3_drop_infra_lane_links"
down_revision = "0037_events_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("infra_lane_links")


def downgrade() -> None:
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
