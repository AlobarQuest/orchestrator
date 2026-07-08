"""Add WS-6.1 bounded observations.

Revision ID: 0012_ws61_observations
Revises: 0011_ws53_deploy_obs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_ws61_observations"
down_revision = "0011_ws53_deploy_obs"
branch_labels = None
depends_on = None

SOURCE_SYSTEMS = (
    "deployment_observation",
    "watchtower",
    "ops_dashboard",
    "healthchecks",
    "uptime_monitor",
    "github",
    "drift_digest",
)
TRUST_CLASSIFICATIONS = ("orchestrator", "delivery_system", "monitor", "external")
SUBJECT_TYPES = (
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
OBSERVATION_TYPES = (
    "deployment",
    "health",
    "uptime",
    "github_check",
    "github_pr",
    "drift",
    "metric",
    "alert",
    "inventory",
)
STATUSES = ("passed", "failed", "degraded", "healthy", "unhealthy", "unknown", "observed")
SEVERITIES = ("info", "warning", "critical")


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_system", sa.String(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("trust_classification", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_reference", sa.Text(), nullable=False),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("observation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("normalized_fact_hash", sa.String(), nullable=False),
        sa.Column("payload_digest", sa.String(), nullable=True),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "source_system",
            "source_reference",
            "normalized_fact_hash",
            name="uq_observations_source_fact",
        ),
        sa.CheckConstraint(
            f"source_system IN {SOURCE_SYSTEMS!r}",
            name="ck_observations_source_system",
        ),
        sa.CheckConstraint(
            f"trust_classification IN {TRUST_CLASSIFICATIONS!r}",
            name="ck_observations_trust_classification",
        ),
        sa.CheckConstraint(
            f"subject_type IN {SUBJECT_TYPES!r}",
            name="ck_observations_subject_type",
        ),
        sa.CheckConstraint(
            f"observation_type IN {OBSERVATION_TYPES!r}",
            name="ck_observations_type",
        ),
        sa.CheckConstraint(f"status IN {STATUSES!r}", name="ck_observations_status"),
        sa.CheckConstraint(f"severity IN {SEVERITIES!r}", name="ck_observations_severity"),
        sa.CheckConstraint(
            "source_reference <> '' AND subject_reference <> '' AND summary <> '' "
            "AND normalized_fact_hash <> '' AND recorded_by <> '' AND idempotency_key <> ''",
            name="ck_observations_required_text",
        ),
    )
    op.create_index("ix_observations_source", "observations", ["source_system", "source_reference"])
    op.create_index(
        "ix_observations_subject",
        "observations",
        ["subject_type", "subject_reference"],
    )
    op.create_index("ix_observations_type", "observations", ["observation_type"])
    op.create_index("ix_observations_environment", "observations", ["environment"])
    op.create_index("ix_observations_observed_at", "observations", ["observed_at"])
    op.execute(
        "CREATE TRIGGER reject_observations_mutation "
        "BEFORE UPDATE OR DELETE ON observations "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_observations_mutation ON observations")
    op.drop_index("ix_observations_observed_at", table_name="observations")
    op.drop_index("ix_observations_environment", table_name="observations")
    op.drop_index("ix_observations_type", table_name="observations")
    op.drop_index("ix_observations_subject", table_name="observations")
    op.drop_index("ix_observations_source", table_name="observations")
    op.drop_table("observations")
