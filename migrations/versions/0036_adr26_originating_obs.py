"""ADR-0026 amendment 1: the observation that caused a package revision.

G1+G2 clause C3. The change record already on this table says which DECISION caused the work;
this says what FACT the decision was about. Both are carried rather than one being derived from
the other, because the chain threads two services and only this one holds the fact.

IT CARRIES A FOREIGN KEY WHERE `change_record_id` DELIBERATELY DOES NOT. That column refuses one
because its referenced rows belong to a foreign system, so the database cannot enforce it and a
constraint that cannot be enforced is a claim rather than a guarantee. This database owns
`observations`, so the constraint is available and means what it says -- the same shape
`reconciliation_conditions.observation_id` already uses against the same table.

It costs nothing to add: every existing row is NULL, so there is nothing to validate.

Revision ID: 0036_adr26_originating_obs
Revises: 0035_bump_proposer_obs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_adr26_originating_obs"
down_revision = "0035_bump_proposer_obs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_package_revisions",
        sa.Column(
            "originating_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("observations.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("work_package_revisions", "originating_observation_id")
