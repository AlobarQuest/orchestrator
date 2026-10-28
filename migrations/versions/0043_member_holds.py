"""Add `sds_member_holds`: a version ceiling on one dependency of one member.

A hold is keyed `(member_id, ecosystem, directory, dependency)`. The ecosystem is Dependabot's
spelling and carries no vocabulary CHECK, because Dependabot owns that vocabulary.

The downgrade drops the table; the holds it records are lost, and every bump they held is then free
to land.

Revision ID: 0043_member_holds
Revises: 0042_sds_members
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043_member_holds"
down_revision = "0042_sds_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sds_member_holds",
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sds_members.id"),
            primary_key=True,
        ),
        sa.Column("ecosystem", sa.String(), primary_key=True),
        sa.Column("directory", sa.String(), primary_key=True),
        sa.Column("dependency", sa.String(), primary_key=True),
        sa.Column("ceiling", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by", sa.String(), nullable=False),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("ecosystem <> ''", name="ck_sds_member_holds_ecosystem"),
        sa.CheckConstraint("directory LIKE '/%'", name="ck_sds_member_holds_directory"),
        sa.CheckConstraint("dependency <> ''", name="ck_sds_member_holds_dependency"),
        sa.CheckConstraint("ceiling <> ''", name="ck_sds_member_holds_ceiling"),
        sa.CheckConstraint("reason <> ''", name="ck_sds_member_holds_reason"),
    )


def downgrade() -> None:
    op.drop_table("sds_member_holds")
