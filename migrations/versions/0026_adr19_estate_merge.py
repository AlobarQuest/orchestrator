"""ADR-0019 increment 5b: the orchestrator's record of landing a pull request that has no unit.

One row per (repository, pull request), ever, for the reason its unit-bound sibling has one row
per unit: a landing is not idempotent and its failure is asymmetric, so a repeat has to be
detectable before the call rather than guessable after it.

The permission is written into the row -- which record authorised it, under which pinned policy
version. The standing condition behind that is re-derivable and will move; the instant at which it
authorised an irreversible act cannot be recovered from anything else afterwards.

Revision ID: 0026_adr19_estate_merge
Revises: 0025_wsp37_pr_merge
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_adr19_estate_merge"
down_revision = "0025_wsp37_pr_merge"
branch_labels = None
depends_on = None

# Frozen copy of `PR_MERGE_STATUSES`. Migrations inline their vocabularies rather than importing
# the model constant, so a later change to the tuple cannot rewrite history. Built by joining
# rather than by `repr`, which renders a trailing comma for a one-element tuple and is a syntax
# error in Postgres.
PR_MERGE_STATUSES = ("merged", "already_merged", "refused")


def upgrade() -> None:
    op.create_table(
        "estate_pr_merge",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("merge_commit_sha", sa.String(), nullable=True),
        sa.Column("github_status", sa.Integer(), nullable=True),
        # An identifier belonging to a FOREIGN system, so no foreign key: this database cannot
        # enforce it, and a constraint it cannot enforce is a claim rather than a guarantee.
        sa.Column("change_record_id", sa.Integer(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=True),
        # NOT NULL deliberately: Postgres treats NULLs as distinct in a unique index, so a
        # nullable column here would let the uniqueness below be evaded.
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("repository", "pr_number", name="uq_estate_pr_merge_subject"),
        sa.UniqueConstraint("idempotency_key", name="uq_estate_pr_merge_idempotency"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{status}'" for status in PR_MERGE_STATUSES)),
            name="ck_estate_pr_merge_status",
        ),
        sa.CheckConstraint("pr_number > 0", name="ck_estate_pr_merge_positive_pr_number"),
        sa.CheckConstraint(
            "repository <> '' AND head_sha <> '' AND idempotency_key <> ''",
            name="ck_estate_pr_merge_required_text",
        ),
    )
    # The pace rule asks "has anything landed into this repository since the window opened?", which
    # is a range scan over one repository and would otherwise be a sequential scan for the life of
    # the table. Named rather than implied, because the unique constraint above leads on the same
    # column and it is easy to assume it serves this query -- it does, today, and would stop doing
    # so the moment the uniqueness were re-keyed.
    op.create_index(
        "ix_estate_pr_merge_repository_created_at",
        "estate_pr_merge",
        ["repository", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_estate_pr_merge_repository_created_at", table_name="estate_pr_merge")
    op.drop_table("estate_pr_merge")
