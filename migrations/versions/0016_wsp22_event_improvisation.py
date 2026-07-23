"""Add events.improvisation — a first-class marker for human operator overrides (WS-P2.2).

Revision ID: 0016_wsp22_event_improvisation
Revises: 0015_wsp216_binding_attempt

The SLO report needs to count how often a human acted outside the declared contract, truthfully
rather than by scraping. The write path (``_transition_event``) knows the actor's role, source, and
target, so it stamps this boolean at the moment of the transition. NOT NULL with a ``false`` default
is safe on the append-only ``events`` table and leaves every existing row and every other event
emit site untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_wsp22_event_improvisation"
down_revision = "0015_wsp216_binding_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "improvisation",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "improvisation")
