"""WS-P2.12: carry governed-knowledge material on units and proposal units.

Nullable because every unit created before this workstream has no document, and
that absence is meaningful: a class enriched with nothing writes an empty
document, so NULL must keep meaning "never enriched" rather than collapsing into
it.

Revision ID: 0021_wsp212_enrichment
Revises: 0020_wsp28_follow_up
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_wsp212_enrichment"
down_revision = "0020_wsp28_follow_up"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("work_units", "decomposition_proposal_units"):
        op.add_column(
            table,
            sa.Column(
                "context_enrichment",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in ("decomposition_proposal_units", "work_units"):
        op.drop_column(table, "context_enrichment")
