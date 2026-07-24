"""Add adjudications risk-class CHECK — waiver risk becomes a controlled vocabulary (WS-P2.3).

Revision ID: 0017_wsp23_waiver_risk_class
Revises: 0016_wsp22_event_improvisation

Exit criterion #4 ("waivers structurally approved and auditable") wants risk to be an auditable
class, not free prose. The ledger is near-empty, so no backfill is required. Non-waivers keep
risk NULL (allowed); a waiver's non-empty risk (already required by ck_adjudications_waiver_fields)
must now be one of the controlled classes.
"""

from alembic import op

revision = "0017_wsp23_waiver_risk_class"
down_revision = "0016_wsp22_event_improvisation"
branch_labels = None
depends_on = None

_RISK_CLASSES = ("low", "medium", "high", "critical")


def upgrade() -> None:
    values = ", ".join(f"'{value}'" for value in _RISK_CLASSES)
    op.create_check_constraint(
        "ck_adjudications_risk_class",
        "adjudications",
        f"risk IS NULL OR risk IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_adjudications_risk_class", "adjudications", type_="check")
