"""Add WS-3.4 event publication outbox.

Revision ID: 0005_ws34_event_publications
Revises: 0004_ws33_protocol_runtime
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_ws34_event_publications"
down_revision = "0004_ws33_protocol_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_system", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_action", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("mapping_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("factory_event", postgresql.JSONB(), nullable=True),
        sa.Column("export_ref", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_system = 'orchestrator'",
            name="ck_event_publications_source_system",
        ),
        sa.CheckConstraint(
            "source_kind IN ('event', 'evidence', 'adjudication', 'context_snapshot')",
            name="ck_event_publications_source_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'exported', 'published', 'skipped', 'rejected', 'failed')",
            name="ck_event_publications_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_event_publications_attempt_count"),
    )
    op.create_unique_constraint(
        "uq_event_publications_source_mapping",
        "event_publications",
        ["source_kind", "source_id", "mapping_version"],
    )
    op.create_unique_constraint(
        "uq_event_publications_event_id",
        "event_publications",
        ["event_id"],
    )
    op.create_index("ix_event_publications_status", "event_publications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_event_publications_status", table_name="event_publications")
    op.drop_constraint(
        "uq_event_publications_event_id",
        "event_publications",
        type_="unique",
    )
    op.drop_constraint(
        "uq_event_publications_source_mapping",
        "event_publications",
        type_="unique",
    )
    op.drop_table("event_publications")
