"""Add `observation_sources`: when each observation producer last posted about each subject.

One row per (`source_system`, `subject_reference`), moved by every accepted observation, a repeat
of a stored one included, so a content-addressed producer stuck on one fact can be told from one
that stopped (Landing Part 3b's served-revision rule reads `revision_watcher`'s). No backfill: a
producer has no row until its next accepted post, which reads as "not heard from", the safe
direction.

Revision ID: 0046_observation_source_seen
Revises: 0045_landing_revert_images
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_observation_source_seen"
down_revision = "0045_landing_revert_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observation_sources",
        sa.Column("source_system", sa.String(), primary_key=True),
        sa.Column("subject_reference", sa.Text(), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("observation_sources")
