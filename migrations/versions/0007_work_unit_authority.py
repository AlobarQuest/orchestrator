"""Store approved work-unit authority envelopes.

Revision ID: 0007_work_unit_authority
Revises: 0006_approval_event_id_text
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_work_unit_authority"
down_revision = "0006_approval_event_id_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column("work_units", sa.Column("authority", jsonb, nullable=True))
    op.execute(
        """
        UPDATE work_units AS wu
        SET authority = COALESCE(
            (
                SELECT dpu.authority
                FROM events AS event
                JOIN approved_decompositions AS ad
                  ON ad.id = CAST(
                      NULLIF(event.payload -> 'command' ->> 'approved_decomposition_id', '') AS uuid
                  )
                 AND ad.work_package_revision_id = wu.work_package_revision_id
                JOIN decomposition_proposal_units AS dpu
                  ON dpu.proposal_id = ad.proposal_id
                 AND dpu.unit_key = wu.unit_key
                WHERE event.subject_type = 'work_unit'
                  AND event.subject_id = wu.id
                  AND event.action = 'work_unit.registered'
                LIMIT 1
            ),
            wpr.enforcement_snapshot -> 'authority'
        )
        FROM work_package_revisions AS wpr
        WHERE wu.work_package_revision_id = wpr.id
          AND wu.authority IS NULL
        """
    )
    op.alter_column("work_units", "authority", existing_type=jsonb, nullable=False)


def downgrade() -> None:
    op.drop_column("work_units", "authority")
