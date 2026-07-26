"""wsp27 tracker bindings

Revision ID: 0018_wsp27_tracker_bindings
Revises: 0017_wsp23_waiver_risk_class
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_wsp27_tracker_bindings"
down_revision = "0017_wsp23_waiver_risk_class"
branch_labels = None
depends_on = None

# Frozen copy of orchestrator.persistence.models.TRACKER_SYSTEMS.
# Migrations never import model constants (established convention, see 0014).
TRACKER_SYSTEMS = ("todoist",)


def upgrade() -> None:
    op.create_table(
        "unit_tracker_bindings",
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            primary_key=True,
        ),
        sa.Column("tracker_system", sa.String(), nullable=False),
        sa.Column("external_item_id", sa.String(), nullable=False),
        sa.Column("external_url", sa.String(), nullable=True),
        sa.Column("projected_state", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Built via join, not `{TRACKER_SYSTEMS!r}`: a single-element tuple's repr carries a
        # trailing comma (`('todoist',)`), which is invalid inside a SQL IN (...) list.
        sa.CheckConstraint(
            "tracker_system IN ({})".format(", ".join(f"'{s}'" for s in TRACKER_SYSTEMS)),
            name="ck_unit_tracker_bindings_tracker_system",
        ),
        sa.CheckConstraint(
            "external_item_id <> ''",
            name="ck_unit_tracker_bindings_external_item_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("unit_tracker_bindings")
