"""Admit the machine-activation sweep to the observation spine.

ADR-0030 ruled that a change becomes live on the operator machine when the code is pulled into a
working copy and the next process start picks it up -- a second deployment model the spine could
not describe. `source_system` had no member for that lane, and `observation_type` had none for
what a sweep of it asserts. Reusing a near-miss (`drift_digest`, `drift`, `inventory`) would write
false provenance into rows that have no supersession model and no delete route.

`subject_type` needed nothing: `repo` already exists, and the landing ledger already keys it by
`owner/name`. The two producers cannot collide -- uniqueness is on
`(source_system, source_reference)`.

The tuples below are a FROZEN COPY rather than an import of the model constants. A migration
describes the database at one point in history; importing the live constant would make every past
migration silently re-describe itself the next time a member is added.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while activation rows exist would silently
invalidate them.

ORDER MATTERS AT RELEASE TIME. This must run BEFORE the image swap, which is the estate's standing
rule and has a specific direction here: the new image validates the wider tuple in its own
process, so a new container against the old constraint passes its own check and is refused by the
database, arriving as an `observation_conflict` rather than as a clean `observation_invalid`.
"""

from alembic import op

revision = "0029_adr30_activation"
down_revision = "0028_adr26_change_record"
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
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("machine_activation",)

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
)
_NEW_TYPES = _OLD_TYPES + ("activation",)


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
