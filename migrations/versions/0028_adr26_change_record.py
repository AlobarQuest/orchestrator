"""ADR-0026: the change record that caused a package revision.

The join between change-manager, where a human decides, and the orchestrator, which does the
work. Without it the traceability chain can say what a revision caused and not what caused it.

An integer belonging to a FOREIGN system, so deliberately no foreign key -- this database
cannot enforce it, and a constraint that cannot be enforced is a claim rather than a
guarantee. That is the shape `estate_pr_merges.change_record_id` already uses, for the same
reason, against the same service.

Revision ID: 0028_adr26_change_record
Revises: 0027_adr21_recovery_floor
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_adr26_change_record"
down_revision = "0027_adr21_recovery_floor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_package_revisions",
        sa.Column("change_record_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_package_revisions", "change_record_id")
