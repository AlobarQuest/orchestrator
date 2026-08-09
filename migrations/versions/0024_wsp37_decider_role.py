"""WS-P3.7: record which KIND of actor decided an adjudication.

Nullable, and NOT back-filled. Every row written before this column existed carries NULL, and
NULL means *unknown* -- never *not human*. A boundary is only sound if the population on its
clean side is actually clean (ADR-0014), and the historical population is not: the role survived
only inside an event payload, and inferring it from `decided_by`'s spelling would be a heuristic
keyed on identity strings.

Revision ID: 0024_wsp37_decider_role
Revises: 0023_wsp36_landing_audit
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_wsp37_decider_role"
down_revision = "0023_wsp36_landing_audit"
branch_labels = None
depends_on = None

# Frozen copy of `ActorRole`'s members. Migrations inline their vocabularies rather than
# importing the model constant, so a later change to the enum cannot rewrite history.
ACTOR_ROLES = ("system", "worker", "verifier", "human", "observer")


def upgrade() -> None:
    op.add_column("adjudications", sa.Column("decided_by_role", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_adjudications_decided_by_role",
        "adjudications",
        "decided_by_role IS NULL OR decided_by_role IN ("
        + ", ".join(f"'{role}'" for role in ACTOR_ROLES)
        + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_adjudications_decided_by_role", "adjudications", type_="check")
    op.drop_column("adjudications", "decided_by_role")
