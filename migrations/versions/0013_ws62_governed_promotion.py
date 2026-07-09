"""Add WS-6.2 governed knowledge promotion proposals.

Revision ID: 0013_ws62_governed_promotion
Revises: 0012_ws61_observations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_ws62_governed_promotion"
down_revision = "0012_ws61_observations"
branch_labels = None
depends_on = None

TARGET_BRAINS = ("code", "infra")
TARGET_TYPES = ("lesson", "rule")
AUTHORITIES = ("informational", "recommended", "required")
ACTIONS = ("submitted_to_brain", "rejected")


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "knowledge_promotion_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("correlation_identity", sa.String(), nullable=False),
        sa.Column(
            "source_observation_ids",
            jsonb,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_observation_hashes",
            jsonb,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("release_artifact_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deployment_observation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_unit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("package_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_summary", sa.Text(), nullable=False),
        sa.Column("target_brain", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("authority", sa.String(), nullable=False),
        sa.Column("applicability", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposed_payload", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provenance", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposal_hash", sa.String(), nullable=False),
        sa.Column("proposed_by", sa.String(), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("proposal_hash"),
        sa.UniqueConstraint("correlation_identity"),
        sa.CheckConstraint(
            f"target_brain IN {TARGET_BRAINS!r}",
            name="ck_knowledge_promotion_proposals_target_brain",
        ),
        sa.CheckConstraint(
            f"target_type IN {TARGET_TYPES!r}",
            name="ck_knowledge_promotion_proposals_target_type",
        ),
        sa.CheckConstraint(
            f"authority IN {AUTHORITIES!r}",
            name="ck_knowledge_promotion_proposals_authority",
        ),
        sa.CheckConstraint(
            "correlation_identity <> '' AND correlation_summary <> '' "
            "AND proposed_by <> '' AND idempotency_key <> '' AND proposal_hash <> ''",
            name="ck_knowledge_promotion_proposals_required_text",
        ),
    )
    op.create_index(
        "ix_knowledge_promotion_proposals_target",
        "knowledge_promotion_proposals",
        ["target_brain", "target_type"],
    )
    op.create_table(
        "knowledge_promotion_proposal_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_promotion_proposals.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("brain_record_id", sa.String(), nullable=True),
        sa.Column("brain_status", sa.String(), nullable=True),
        sa.Column("brain_response", jsonb, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("action_by", sa.String(), nullable=False),
        sa.Column("action_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("proposal_id", "action"),
        sa.CheckConstraint(
            f"action IN {ACTIONS!r}",
            name="ck_knowledge_promotion_proposal_actions_action",
        ),
        sa.CheckConstraint(
            "action_by <> '' AND idempotency_key <> ''",
            name="ck_knowledge_promotion_proposal_actions_required_text",
        ),
    )
    op.create_index(
        "ix_knowledge_promotion_proposal_actions_proposal",
        "knowledge_promotion_proposal_actions",
        ["proposal_id", "action_at"],
    )
    op.execute(
        "CREATE TRIGGER reject_knowledge_promotion_proposals_mutation "
        "BEFORE UPDATE OR DELETE ON knowledge_promotion_proposals "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )
    op.execute(
        "CREATE TRIGGER reject_knowledge_promotion_proposal_actions_mutation "
        "BEFORE UPDATE OR DELETE ON knowledge_promotion_proposal_actions "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER reject_knowledge_promotion_proposal_actions_mutation "
        "ON knowledge_promotion_proposal_actions"
    )
    op.execute(
        "DROP TRIGGER reject_knowledge_promotion_proposals_mutation "
        "ON knowledge_promotion_proposals"
    )
    op.drop_index(
        "ix_knowledge_promotion_proposal_actions_proposal",
        table_name="knowledge_promotion_proposal_actions",
    )
    op.drop_table("knowledge_promotion_proposal_actions")
    op.drop_index(
        "ix_knowledge_promotion_proposals_target",
        table_name="knowledge_promotion_proposals",
    )
    op.drop_table("knowledge_promotion_proposals")
