"""Admit the work carrier's standing-approval check to the observation spine (ADR-0055 a22).

Before it registers a lane credential's revision 1 that a policy approved, the work carrier records
that the standing approval's carry form refused nothing on that revision (amendment 22 item 3),
and the decomposition gate admits the policy approver only beside that row. `source_system` and
`observation_type` are closed vocabularies backed by CHECK constraints with no member for it. The
rationale for each member is beside the tuple in `persistence/models.py`; the subject type,
`package_revision`, is already a member.

Widening a CHECK is backward-compatible. The downgrade can fail, and deliberately does rather than
deleting rows: narrowing the constraint while the carrier's rows exist would invalidate them.
"""

from alembic import op

revision = "0048_standing_approval_obs"
down_revision = "0047_retire_tracker_bindings"
branch_labels = None
depends_on = None

# Frozen copies (migrations never import model constants).
_OLD_SOURCE_SYSTEMS = (
    "deployment_observation",
    "watchtower",
    "ops_dashboard",
    "healthchecks",
    "uptime_monitor",
    "github",
    "drift_digest",
    "recovery_floor",
    "machine_activation",
    "pin_watcher",
    "tool_installer",
    "revision_watcher",
    "bump_proposer",
    "rotation_proposer",
)
_NEW_SOURCE_SYSTEMS = (*_OLD_SOURCE_SYSTEMS, "work_carrier")

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
    "backup",
    "chain_integrity",
    "activation",
    "caller_pin",
    "tool_revision",
    "production_revision",
    "dependency_update",
    "rotation_due",
)
_NEW_TYPES = (*_OLD_TYPES, "standing_approval_check")


def _members(values: tuple[str, ...]) -> str:
    """Built by an explicit join, never by `repr`: a one-element tuple's repr is a syntax error."""
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
