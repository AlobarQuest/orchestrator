"""Add work_package_revisions.follow_up — the package's declared follow-up block (WS-P2.8).

Revision ID: 0020_wsp28_follow_up
Revises: 0019_wsp27_tracker_recon

Nullable with no server default, deliberately. NULL means "this revision predates the column and
its declaration is unrecoverable" -- the package YAML is never stored, only the derived intake
payload, and the payload did not carry the block. That is distinguishable from a stored
`{"required": false, ...}`, which is a real answer. No backfill is possible.

Note the revision id is 20 characters: `alembic_version.version_num` is varchar(32), and a longer
id fails at runtime with StringDataRightTruncation rather than at authoring time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_wsp28_follow_up"
down_revision = "0019_wsp27_tracker_recon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_package_revisions",
        sa.Column("follow_up", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_package_revisions", "follow_up")
