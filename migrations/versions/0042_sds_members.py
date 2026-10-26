"""Add the SDS membership tables: members, their service grants, their apps, and proposals.

A member is a repository the SDS maintains. Grants record each service's standing per member, apps
name what a member serves, and proposals hold a machine-staged membership change until a person
confirms it.

The downgrade drops all four tables; the membership records they hold are lost.

Revision ID: 0042_sds_members
Revises: 0041_rotation_proposer_obs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_sds_members"
down_revision = "0041_rotation_proposer_obs"
branch_labels = None
depends_on = None

# Pinned here rather than imported: a migration describes the schema at its own revision.
_SERVICES = (
    "dependency_updates",
    "security_patches",
    "app_changes",
    "backups",
    "monitoring",
    "rollouts",
    "credential_rotation",
)
_MEMBER_STATES = ("onboarding", "member", "retired")
_GRANT_STATES = ("not_assessed", "remediating", "granted", "not_applicable", "withdrawn")
_PROPOSAL_STATES = ("staged", "confirmed", "withdrawn")


def _in_list(column: str, vocabulary: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{member}'" for member in vocabulary))


def _now_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "sds_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("working_copy", sa.String(), nullable=True),
        sa.Column("machine_local", sa.Boolean(), server_default="false", nullable=False),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.Column("state_changed_by", sa.String(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=False),
        sa.CheckConstraint(_in_list("state", _MEMBER_STATES), name="ck_sds_members_state"),
        sa.CheckConstraint(
            "NOT machine_local OR working_copy IS NOT NULL",
            name="ck_sds_members_machine_local_working_copy",
        ),
        sa.CheckConstraint("owner <> ''", name="ck_sds_members_owner"),
        sa.CheckConstraint("state_reason <> ''", name="ck_sds_members_state_reason"),
    )
    op.create_index(
        "uq_sds_members_repository_lower",
        "sds_members",
        [sa.text("lower(repository)")],
        unique=True,
    )
    op.create_table(
        "sds_member_grants",
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sds_members.id"),
            primary_key=True,
        ),
        sa.Column("service", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("seeded", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _now_column("set_at"),
        sa.CheckConstraint(_in_list("service", _SERVICES), name="ck_sds_member_grants_service"),
        sa.CheckConstraint(_in_list("state", _GRANT_STATES), name="ck_sds_member_grants_state"),
        sa.CheckConstraint("reason <> ''", name="ck_sds_member_grants_reason"),
    )
    op.create_table(
        "sds_member_apps",
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sds_members.id"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("health_url", sa.String(), nullable=False),
        sa.Column("branch", sa.String(), server_default="main", nullable=False),
        sa.UniqueConstraint("name", name="uq_sds_member_apps_name"),
    )
    op.create_table(
        "sds_member_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("staged_by", sa.String(), nullable=False),
        _now_column("staged_at"),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_sds_member_proposals_idempotency"),
        sa.CheckConstraint(
            _in_list("state", _PROPOSAL_STATES), name="ck_sds_member_proposals_state"
        ),
        sa.CheckConstraint(
            "(state = 'staged') = (decided_by IS NULL AND decided_at IS NULL)",
            name="ck_sds_member_proposals_decision",
        ),
    )


def downgrade() -> None:
    op.drop_table("sds_member_proposals")
    op.drop_table("sds_member_apps")
    op.drop_table("sds_member_grants")
    op.drop_index("uq_sds_members_repository_lower", table_name="sds_members")
    op.drop_table("sds_members")
