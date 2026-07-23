"""Add the attempt a PR binding was written for (WS-P2.16 U3).

Revision ID: 0015_wsp216_binding_attempt
Revises: 0014_wsp21_recovery_controls

`unit_pr_binding.binding_attempt` records which claim attempt wrote the binding, so the
EXECUTING -> SUBMITTED guard can require a binding written for the CURRENT attempt and reject a
stale one carried over from a previous attempt (the divergence alarm would otherwise arm on the
wrong head). It is deliberately NULLABLE:

* NOT NULL would make the SYSTEM operator-repair write (which may legitimately omit the attempt)
  raise IntegrityError -- the only path that can unblock a stuck unit would 500.
* Nullable + a guard that requires ``binding_attempt == attempt_count`` fails closed: a binding
  written without a real attempt can never satisfy the guard, which is correct -- an operator
  repairing a unit must supply the true attempt of the PR that genuinely exists.

Nullable ``add_column`` is safe on a non-empty table, so this does not depend on
``unit_pr_binding`` being empty in production. It is orthogonal to
``ck_unit_pr_binding_armed_head_has_attempt`` (which constrains the verification_read_* pair).
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_wsp216_binding_attempt"
down_revision = "0014_wsp21_recovery_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("unit_pr_binding", sa.Column("binding_attempt", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("unit_pr_binding", "binding_attempt")
