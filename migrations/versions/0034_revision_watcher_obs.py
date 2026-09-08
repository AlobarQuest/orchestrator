"""Admit the revision watcher to the observation spine.

The lane shipped filing NOTHING. Its first live pass measured six applications correctly and
reported five rows `unfiled`, because `source_system` and `observation_type` are closed
vocabularies backed by CHECK constraints and neither had a member for this producer. That is this
estate's most-repeated lesson met again -- wherever two vocabularies must agree, assume they do not
until you have grepped both sides -- and it survived a 5197-test gate because every test that
composes the payload also mocks the client that would have refused it. `test_record.py` now pins
the payload's members against these tuples, which is the durable half of the fix.

`source_system` had no member. `deployment_observation` is the near miss and is wrong: that names
rows describing a deployment EVENT, recorded once by whoever performed it, where this lane asserts a
standing CONDITION it re-measures hourly and that no deployment need have caused. `github` is wrong
for the reason the pin watcher already records -- it names the system a fact came from, and every
member here names the PRODUCER.

`observation_type` had none either. `deployment` asserts that a deployment happened; this asserts
nothing about how the state was arrived at, which is the cause-independence that makes the lane
worth having -- a failed rollout, an image that never built, a rotated secret and a manual swap
nobody performed are indistinguishable here and deliberately so. `tool_revision` is the same shape
one artifact over: a binary on the operator machine rather than an application in production.

`subject_type` needed nothing. `service` already exists and is exactly what a deployed application
is; adding a member for it would have been a fourth spelling of a noun the vocabulary already has.
Four of the six subjects are services of one repository, so `repo` -- which the ledger, the sweep
and the pin watcher key on -- would have collided four ways.

Widening a CHECK is backward-compatible: every row that satisfied the old constraint satisfies the
new one, so the upgrade cannot fail on existing data. The downgrade CAN fail, and deliberately does
rather than deleting rows -- narrowing the constraint while watcher rows exist would silently
invalidate them.
"""

from alembic import op

revision = "0034_revision_watcher_obs"
down_revision = "0033_tool_installer_obs"
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
)
_NEW_SOURCE_SYSTEMS = _OLD_SOURCE_SYSTEMS + ("revision_watcher",)

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
)
_NEW_TYPES = _OLD_TYPES + ("production_revision",)


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
