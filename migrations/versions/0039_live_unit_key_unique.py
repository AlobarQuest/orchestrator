"""Hold a unit key unique only among a revision's units that are not cancelled (ADR-0052).

Superseding an unworked decomposition approval cancels its units, and the re-approval may reuse
their keys: the factory derives keys deterministically. The plain UNIQUE(work_package_revision_id,
unit_key) from 0001 would refuse that, so it becomes a partial unique index that skips cancelled
units. Every unit that isn't cancelled still has a key no other live unit of its revision holds.

The downgrade restores the plain constraint, and refuses if any revision now has a cancelled unit
sharing a key with another unit, since the old constraint can't hold those rows.

Revision ID: 0039_live_unit_key_unique
Revises: 0038_t3_drop_infra_lane_links
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_live_unit_key_unique"
down_revision = "0038_t3_drop_infra_lane_links"
branch_labels = None
depends_on = None

_OLD = "work_units_work_package_revision_id_unit_key_key"
_NEW = "uq_work_units_live_unit_key"


def upgrade() -> None:
    op.drop_constraint(_OLD, "work_units", type_="unique")
    op.create_index(
        _NEW,
        "work_units",
        ["work_package_revision_id", "unit_key"],
        unique=True,
        postgresql_where=sa.text("state <> 'cancelled'"),
    )


def downgrade() -> None:
    shared = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM ("
            " SELECT 1 FROM work_units GROUP BY work_package_revision_id, unit_key"
            " HAVING count(*) > 1) AS shared_keys"
        )
    )
    if shared:
        raise RuntimeError(
            f"{shared} revision/unit_key pairs are held by more than one unit; the plain unique "
            "constraint cannot be restored over them"
        )
    op.drop_index(_NEW, table_name="work_units")
    op.create_unique_constraint(_OLD, "work_units", ["work_package_revision_id", "unit_key"])
