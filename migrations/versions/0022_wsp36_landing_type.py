"""Add the `landing` observation type.

The vocabulary had no member meaning "a commit reached the default branch". WS-P3.6's
landing ledger needs one; until this migration it recorded direct pushes as `inventory`,
a type with no producer and no established meaning, as an honest placeholder.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint
satisfies the new one, so the upgrade cannot fail on existing data. The downgrade CAN
fail, and deliberately does rather than deleting rows -- if any observation has been
recorded as `landing`, narrowing the constraint would silently invalidate it.
"""

from alembic import op

revision = "0022_wsp36_landing_type"
down_revision = "0021_wsp212_enrichment"
branch_labels = None
depends_on = None

_OLD = (
    "deployment",
    "health",
    "uptime",
    "github_check",
    "github_pr",
    "drift",
    "metric",
    "alert",
    "inventory",
)
_NEW = _OLD + ("landing",)


def _members(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint("ck_observations_type", "observations", type_="check")
    op.create_check_constraint(
        "ck_observations_type", "observations", f"observation_type IN ({_members(_NEW)})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_observations_type", "observations", type_="check")
    op.create_check_constraint(
        "ck_observations_type", "observations", f"observation_type IN ({_members(_OLD)})"
    )
