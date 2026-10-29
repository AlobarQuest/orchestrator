"""Add `landing_reverts`: a landed Dependabot bump that went bad, and what was done about it.

One row per (repository, pull request). The status CHECK is built by joining quoted members, the
same text as the model's.

The downgrade drops the table; the record of which landings were reverted is lost.

Revision ID: 0044_landing_reverts
Revises: 0043_member_holds
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_landing_reverts"
down_revision = "0043_member_holds"
branch_labels = None
depends_on = None

STATUSES = (
    "rerun_requested",
    "revert_opened",
    "revert_landed",
    "refused_overlap",
    "revert_failed",
)


def upgrade() -> None:
    op.create_table(
        "landing_reverts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("merge_commit_sha", sa.String(), nullable=False),
        sa.Column("parent_sha", sa.String(), nullable=False),
        sa.Column("failed_job_names", postgresql.JSONB(), nullable=False),
        sa.Column("failed_run_id", sa.BigInteger(), nullable=False),
        sa.Column("rerun_attempts", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("revert_pr_number", sa.Integer(), nullable=True),
        sa.Column("revert_head_sha", sa.String(), nullable=True),
        sa.Column("revert_merge_sha", sa.String(), nullable=True),
        sa.Column("bumps", postgresql.JSONB(), nullable=False),
        sa.Column("hold_outcome", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repository", "pr_number", name="uq_landing_reverts_subject"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{status}'" for status in STATUSES)),
            name="ck_landing_reverts_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("landing_reverts")
