"""Add `staged_package_intakes`: intake payloads a machine staged for a person to confirm.

ADR-0006 amendment 1. The CLI stages an intake as SYSTEM and a live human session confirms it
from `/review`. The table only holds the payload and its state; registering still goes through
`register_package_intake`, so nothing about what a registered revision is changes.

The downgrade drops the table. A staged row that was never confirmed did nothing, and a confirmed
one's revision and intake event are kept, so what is lost is only the link from the revision back
to its staged row.

Revision ID: 0040_staged_package_intakes
Revises: 0039_live_unit_key_unique
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_staged_package_intakes"
down_revision = "0039_live_unit_key_unique"
branch_labels = None
depends_on = None

# Pinned here rather than imported: a migration describes the schema at its own revision, and the
# model's tuple may grow later. Joined, not repr'd, as the model does.
_STATES = ("staged", "registered", "withdrawn")


def upgrade() -> None:
    op.create_table(
        "staged_package_intakes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("staged_by", sa.String(), nullable=False),
        sa.Column(
            "staged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column(
            "registered_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_staged_package_intakes_idempotency"),
        sa.CheckConstraint(
            "state IN ({})".format(", ".join(f"'{state}'" for state in _STATES)),
            name="ck_staged_package_intakes_state",
        ),
        sa.CheckConstraint(
            "(state = 'registered') = (registered_revision_id IS NOT NULL)",
            name="ck_staged_package_intakes_registered_revision",
        ),
        sa.CheckConstraint(
            "idempotency_key <> '' AND staged_by <> ''",
            name="ck_staged_package_intakes_required_text",
        ),
    )


def downgrade() -> None:
    op.drop_table("staged_package_intakes")
