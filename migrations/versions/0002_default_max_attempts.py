"""Set the default work-unit attempt budget to three."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_default_max_attempts"
down_revision: str | None = "0001_ws31_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "work_units",
        "max_attempts",
        existing_type=sa.Integer(),
        server_default=sa.text("3"),
    )


def downgrade() -> None:
    op.alter_column(
        "work_units",
        "max_attempts",
        existing_type=sa.Integer(),
        server_default=sa.text("1"),
    )
