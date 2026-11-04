"""Retire the tracker adapter's storage: `unit_tracker_bindings` and the tracker vocabulary.

ADR-0040 amendment 1 retired the tracker adapter. Production held 4 cancelled bindings (recorded in
that amendment) and no tracker conditions, so nothing is carried over. The downgrade restores the
structure (the table and the wider CHECKs), not any rows.

Revision ID: 0047_retire_tracker_bindings
Revises: 0046_observation_source_seen
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_retire_tracker_bindings"
down_revision = "0046_observation_source_seen"
branch_labels = None
depends_on = None

# Frozen copies (migrations never import model constants).
OBSERVATION_KINDS = ("github_pr", "github_check", "deployment")
CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
)
_WIDE_OBSERVATION_KINDS = (*OBSERVATION_KINDS, "tracker")
_WIDE_CONDITION_TYPES = (*CONDITION_TYPES, "tracker_state_divergence")
_TRACKER_SYSTEMS = ("todoist",)


def _in_list(column: str, members: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{member}'" for member in members)
    return f"{column} IN ({quoted})"


def _swap_checks(observation_kinds: tuple[str, ...], condition_types: tuple[str, ...]) -> None:
    for name, column, members in (
        ("ck_reconciliation_conditions_observation_kind", "observation_kind", observation_kinds),
        ("ck_reconciliation_conditions_type", "condition_type", condition_types),
    ):
        op.drop_constraint(name, "reconciliation_conditions", type_="check")
        op.create_check_constraint(name, "reconciliation_conditions", _in_list(column, members))


def upgrade() -> None:
    _swap_checks(OBSERVATION_KINDS, CONDITION_TYPES)
    op.drop_table("unit_tracker_bindings")


def downgrade() -> None:
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
        sa.CheckConstraint(
            _in_list("tracker_system", _TRACKER_SYSTEMS),
            name="ck_unit_tracker_bindings_tracker_system",
        ),
        sa.CheckConstraint(
            "external_item_id <> ''",
            name="ck_unit_tracker_bindings_external_item_id",
        ),
    )
    _swap_checks(_WIDE_OBSERVATION_KINDS, _WIDE_CONDITION_TYPES)
