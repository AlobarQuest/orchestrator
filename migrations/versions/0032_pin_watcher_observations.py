"""Admit the pin watcher to the observation spine.

A caller workflow's `uses:` SHA IS the runner revision a dispatch executes, so a repository pinned
behind the recommendation runs a runner nobody chose. On 2026-09-04 five of six callers were
twenty-three commits behind and nothing reported it. The watcher reports it; nothing in the
existing vocabulary let it say so.

`source_system` had no member for this producer. `github` is the near miss and is wrong: every
member of that tuple names the PRODUCING LANE, not the system a fact was read from, and a row
claiming GitHub observed its own callers would be false provenance in a table with no supersession
model and no delete route. `observation_type` had no member either -- `inventory` asserts only that
something was enumerated, where this asserts something specific and falsifiable about which runner
revision a dispatch would execute.

`subject_type` needed nothing: `repo` already exists and is what the landing ledger and the
activation sweep already key on. The three cannot collide, because uniqueness is on
`(source_system, source_reference)` and the source systems differ.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while pin-watcher rows exist would silently
invalidate them.
"""

from alembic import op

revision = "0032_pin_watcher_obs"
down_revision = "0031_adr30_activation_obs"
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
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("pin_watcher",)

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
)
_NEW_TYPES = _OLD_TYPES + ("caller_pin",)


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
