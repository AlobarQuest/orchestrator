"""Admit the recovery floor's four scheduled jobs to the observation spine.

ADR-0021 ruled that whether a backup ran, restores, snapshotted, or whether the tamper-evident
chain verified, are FACTS rather than decisions -- so they belong here rather than in
change-manager. Nothing in the existing vocabulary let them say so: `source_system` had no member
for a scheduled job on the operator machine, and `observation_type` had no member for a recovery
artifact or a chain verification. Reusing a near-miss (`healthchecks`, `ops_dashboard`, `health`)
would write false provenance into rows that have no supersession model and no delete route.

`subject_type` needed nothing: `external_run` already existed and fits.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while recovery-floor rows exist would
silently invalidate them.
"""

from alembic import op

revision = "0027_adr21_recovery_floor"
down_revision = "0026_adr19_estate_merge"
branch_labels = None
depends_on = None

_OLD_SOURCE_SYSTEMS = (
    "deployment_observation",
    "watchtower",
    "ops_dashboard",
    "healthchecks",
    "uptime_monitor",
    "github",
    "drift_digest",
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("recovery_floor",)

_OLD_TYPES = (
    "deployment",
    "health",
    "uptime",
    "github_check",
    "github_pr",
    "drift",
    "metric",
    "alert",
    "inventory",
    "landing",
    "landing_audit",
)
_NEW_TYPES = _OLD_TYPES + ("backup", "chain_integrity")


def _members(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace(name: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(name, "observations", type_="check")
    op.create_check_constraint(name, "observations", f"{column} IN ({_members(values)})")


def upgrade() -> None:
    _replace("ck_observations_source_system", "source_system", _NEW_SOURCE_SYSTEMS)
    _replace("ck_observations_type", "observation_type", _NEW_TYPES)


def downgrade() -> None:
    _replace("ck_observations_source_system", "source_system", _OLD_SOURCE_SYSTEMS)
    _replace("ck_observations_type", "observation_type", _OLD_TYPES)
