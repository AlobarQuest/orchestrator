"""wsp27 tracker reconciliation vocab

Revision ID: 0019_wsp27_tracker_recon
Revises: 0018_wsp27_tracker_bindings

Note: named `tracker_recon`, not the fuller `tracker_reconciliation`, because
`alembic_version.version_num` is `varchar(32)` and the longer id (33 chars)
overflows it -- confirmed empirically (`StringDataRightTruncation`) before
this file was renamed to match.
"""

from __future__ import annotations

from alembic import op

revision = "0019_wsp27_tracker_recon"
down_revision = "0018_wsp27_tracker_bindings"
branch_labels = None
depends_on = None

# Frozen copies of orchestrator.persistence.models after this migration.
# Migrations never import model constants (established convention, see 0014).
OBSERVATION_KINDS = ("github_pr", "github_check", "deployment", "tracker")
CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
    "tracker_state_divergence",
)
# Pre-migration copies (for downgrade).
_OLD_OBSERVATION_KINDS = ("github_pr", "github_check", "deployment")
_OLD_CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
)


def _swap(name: str, column: str, new: tuple[str, ...]) -> None:
    op.drop_constraint(name, "reconciliation_conditions", type_="check")
    op.create_check_constraint(name, "reconciliation_conditions", f"{column} IN {new!r}")


def upgrade() -> None:
    _swap("ck_reconciliation_conditions_observation_kind", "observation_kind", OBSERVATION_KINDS)
    _swap("ck_reconciliation_conditions_type", "condition_type", CONDITION_TYPES)


def downgrade() -> None:
    _swap(
        "ck_reconciliation_conditions_observation_kind",
        "observation_kind",
        _OLD_OBSERVATION_KINDS,
    )
    _swap("ck_reconciliation_conditions_type", "condition_type", _OLD_CONDITION_TYPES)
