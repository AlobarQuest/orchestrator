"""Index `events` on what its readers filter by.

The budget check at claim time and the SLO report filter events on `action` and a window of
`occurred_at`; a unit's history filters on `subject_id`. The table carried only its primary key
and the unique idempotency key, so each of those reads scanned it whole.

Plain CREATE INDEX, not CONCURRENTLY, for the reason 0014 records: alembic runs inside a
transaction, and a failed CONCURRENTLY build leaves an INVALID index behind. The table is modest,
so the brief lock is the cheaper trade.

Revision ID: 0037_events_indexes
Revises: 0036_adr26_originating_obs
"""

from alembic import op

revision = "0037_events_indexes"
down_revision = "0036_adr26_originating_obs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_events_action_occurred_at", "events", ["action", "occurred_at"])
    op.create_index("ix_events_subject_id", "events", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_events_subject_id", table_name="events")
    op.drop_index("ix_events_action_occurred_at", table_name="events")
