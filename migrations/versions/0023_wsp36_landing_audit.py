"""Add the `landing_audit` observation type.

WS-P3.6 Increment 3's two detectors file one row per repository per pass. It is a separate type
from `landing` because it is a JUDGMENT about records rather than a record: the same subject
(`repo`), a different claim.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies
the new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and
deliberately does rather than deleting rows -- narrowing the constraint while audit rows exist
would silently invalidate them.
"""

from alembic import op

revision = "0023_wsp36_landing_audit"
down_revision = "0022_wsp36_landing_type"
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
    "landing",
)
_NEW = _OLD + ("landing_audit",)


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
