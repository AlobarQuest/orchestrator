"""Teach `landing_reverts` the image steps: `pinned_images`, `restored_at`, two statuses.

`repointed` (production runs the parent's image, the revert not yet open) and `restored` (the
moving tag is back after the revert landed) join the status CHECK, built by joining quoted members,
the same text as the model's.

The downgrade refuses while any row holds `repointed` or `restored`: narrowing the CHECK would fail
on them anyway, and mapping them to another status would misstate where production stands.

Revision ID: 0045_landing_revert_images
Revises: 0044_landing_reverts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_landing_revert_images"
down_revision = "0044_landing_reverts"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "rerun_requested",
    "revert_opened",
    "revert_landed",
    "refused_overlap",
    "revert_failed",
)
NEW_STATUSES = (*OLD_STATUSES, "repointed", "restored")
CONSTRAINT = "ck_landing_reverts_status"


def _check(statuses: tuple[str, ...]) -> str:
    return "status IN ({})".format(", ".join(f"'{status}'" for status in statuses))


def upgrade() -> None:
    op.add_column("landing_reverts", sa.Column("pinned_images", postgresql.JSONB(), nullable=True))
    op.add_column(
        "landing_reverts", sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint(CONSTRAINT, "landing_reverts", type_="check")
    op.create_check_constraint(CONSTRAINT, "landing_reverts", _check(NEW_STATUSES))


def downgrade() -> None:
    held = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM landing_reverts WHERE status IN ('repointed', 'restored')"
            )
        )
        .scalar_one()
    )
    if held:
        raise RuntimeError(
            f"landing_reverts has {held} row(s) in status repointed or restored; "
            "resolve them before downgrading past 0045"
        )
    op.drop_constraint(CONSTRAINT, "landing_reverts", type_="check")
    op.create_check_constraint(CONSTRAINT, "landing_reverts", _check(OLD_STATUSES))
    op.drop_column("landing_reverts", "restored_at")
    op.drop_column("landing_reverts", "pinned_images")
