"""WS-P3.7: the orchestrator's own record that it asked GitHub to land a unit's pull request.

One row per unit, ever. A landing is not idempotent and its failure is asymmetric — a lost
response looks exactly like a refusal when you ask GitHub again — so the record is what makes a
repeat detectable before the call rather than guessable after it.

Revision ID: 0025_wsp37_pr_merge
Revises: 0024_wsp37_decider_role
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_wsp37_pr_merge"
down_revision = "0024_wsp37_decider_role"
branch_labels = None
depends_on = None

# Frozen copy of `PR_MERGE_STATUSES`. Migrations inline their vocabularies rather than importing
# the model constant, so a later change to the tuple cannot rewrite history. Built by joining
# rather than by `repr`, which renders a trailing comma for a one-element tuple and is a syntax
# error in Postgres — the same construction the model uses, for the same reason.
PR_MERGE_STATUSES = ("merged", "already_merged", "refused")


def upgrade() -> None:
    op.create_table(
        "unit_pr_merge",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("work_unit_id", sa.UUID(as_uuid=True), sa.ForeignKey("work_units.id")),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("merge_commit_sha", sa.String(), nullable=True),
        sa.Column("github_status", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("work_unit_id", name="uq_unit_pr_merge_work_unit"),
        sa.UniqueConstraint("idempotency_key", name="uq_unit_pr_merge_idempotency"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{status}'" for status in PR_MERGE_STATUSES)),
            name="ck_unit_pr_merge_status",
        ),
        sa.CheckConstraint("pr_number > 0", name="ck_unit_pr_merge_positive_pr_number"),
        sa.CheckConstraint(
            "repository <> '' AND head_sha <> '' AND idempotency_key <> ''",
            name="ck_unit_pr_merge_required_text",
        ),
    )


def downgrade() -> None:
    op.drop_table("unit_pr_merge")
