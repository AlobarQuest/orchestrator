"""Admit the tool installer to the observation spine.

`AlobarQuest/rtk`'s upstream sync runs end to end and stops one step short of the machine: landing
does not install, so the binary that filters every command here is whatever was last installed by
hand. The installer lane closes that step and files what it found; nothing in the existing
vocabulary let it say so.

`source_system` had no member for this producer. `machine_activation` is the near miss and is
wrong: it names the lane that asserts what a WORKING COPY will execute at its next start, where
this one asserts what a BINARY on the machine is -- two different claims about two different
artifacts, and a row claiming the sweep observed a compiled tool would be false provenance in a
table with no supersession model and no delete route.

`observation_type` had no member either. `activation` is the same near miss for the same reason,
and `inventory` asserts only that something was enumerated, where this asserts something specific
and falsifiable: which revision of a named tool the operator machine is running, and whether that
is the revision the fork's branch holds.

`subject_type` needed nothing: `repo` already exists and is what the landing ledger, the activation
sweep and the pin watcher key on. None of the four can collide, because uniqueness is on
`(source_system, source_reference)` and the source systems differ.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while installer rows exist would silently
invalidate them.
"""

from alembic import op

revision = "0033_tool_installer_obs"
down_revision = "0032_pin_watcher_obs"
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
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("tool_installer",)

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
)
_NEW_TYPES = _OLD_TYPES + ("tool_revision",)


def _members(values: tuple[str, ...]) -> str:
    """Built by an explicit join, never by `repr`.

    A one-element tuple's repr carries a trailing comma -- `('x',)` -- which renders as
    `col IN ('x',)` and is a Postgres syntax error rather than merely ugly. Neither tuple here is
    one element today; the construction is the one that stays correct if either ever becomes so.
    """
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
