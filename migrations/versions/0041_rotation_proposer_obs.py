"""Admit the rotation proposer to the observation spine (ADR-0054 amendment 1).

The rotation proposer files the fact that a registry credential is due for rotation before it
proposes the work, as the signal->work contract requires of every producer that proposes. It could
not file a row at all: `source_system`, `subject_type` and `observation_type` are closed
vocabularies backed by CHECK constraints, and none had a member for it. The rationale for each
member, and why its near miss is wrong, is beside the tuple in `persistence/models.py`.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while this producer's rows exist would
silently invalidate them.
"""

from alembic import op

revision = "0041_rotation_proposer_obs"
down_revision = "0040_staged_package_intakes"
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
    "recovery_floor",
    "machine_activation",
    "pin_watcher",
    "tool_installer",
    "revision_watcher",
    "bump_proposer",
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("rotation_proposer",)

_OLD_SUBJECT_TYPES = (
    "service",
    "repo",
    "deployment",
    "release_binding",
    "deployment_observation",
    "work_unit",
    "package_revision",
    "endpoint",
    "monitor",
    "external_run",
)
_NEW_SUBJECT_TYPES = _OLD_SUBJECT_TYPES + ("credential",)

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
)
_NEW_TYPES = _OLD_TYPES + ("rotation_due",)


def _members(values: tuple[str, ...]) -> str:
    """Built by an explicit join, never by `repr`: a one-element tuple's repr is a syntax error."""
    return ", ".join(f"'{value}'" for value in values)


def _replace(name: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(name, "observations", type_="check")
    op.create_check_constraint(name, "observations", f"{column} IN ({_members(values)})")


def upgrade() -> None:
    _replace("ck_observations_source_system", "source_system", _NEW_SOURCE_SYSTEMS)
    _replace("ck_observations_subject_type", "subject_type", _NEW_SUBJECT_TYPES)
    _replace("ck_observations_type", "observation_type", _NEW_TYPES)


def downgrade() -> None:
    _replace("ck_observations_source_system", "source_system", _OLD_SOURCE_SYSTEMS)
    _replace("ck_observations_subject_type", "subject_type", _OLD_SUBJECT_TYPES)
    _replace("ck_observations_type", "observation_type", _OLD_TYPES)
