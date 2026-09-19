"""Admit the bump proposer to the observation spine (G1+G2 increment 2).

The signal→work contract requires every machine-proposed change record to name the observation
that caused it, and a producer that proposes must first observe. `bump_proposer` is the one
running producer, so it is the one made to conform -- and it could not file a row at all, because
`source_system` and `observation_type` are closed vocabularies backed by CHECK constraints and
neither had a member for it.

`source_system` had no member. `github` is the near miss and is wrong for the reason the pin
watcher already records: it names the system a fact came from, where every member here names the
PRODUCER. `drift_digest` names the infrastructure drift lane, whose subject is a hosted estate
rather than a repository's dependencies.

`observation_type` had none either. `github_pr` already means "a fact about a pull request bound
to a work unit" in the reconciliation lane, whose rows are subject_type `work_unit`; these are
`repo`, so the two never collide -- but two namespaces staying disjoint is a coincidence rather
than a design, which is the reasoning migration 0022 recorded when `landing` was split out for
the same reason. `drift` is the infrastructure digest's and `inventory` asserts only that
something was enumerated, where this states two specific falsifiable versions.

`subject_type` needed nothing. `repo` already exists and is what the landing ledger, the
activation sweep and the pin watcher key on; the reference is spelled the same way they spell it,
so one repository is one subject across all four producers rather than four.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately
does rather than deleting rows -- narrowing the constraint while this producer's rows exist would
silently invalidate them.
"""

from alembic import op

revision = "0035_bump_proposer_obs"
down_revision = "0034_revision_watcher_obs"
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
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("bump_proposer",)

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
)
_NEW_TYPES = _OLD_TYPES + ("dependency_update",)


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
