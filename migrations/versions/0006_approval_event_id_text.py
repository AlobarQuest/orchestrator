"""Store approval event IDs as ledger strings.

Revision ID: 0006_approval_event_id_text
Revises: 0005_ws34_event_publications
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_approval_event_id_text"
down_revision = "0005_ws34_event_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "work_package_revisions",
        "approval_event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="approval_event_id::text",
    )


def downgrade() -> None:
    op.alter_column(
        "work_package_revisions",
        "approval_event_id",
        existing_type=sa.String(),
        type_=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
        postgresql_using="approval_event_id::uuid",
    )
