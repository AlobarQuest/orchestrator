"""Add WS-5.3 deployment observations.

Revision ID: 0011_ws53_deploy_obs
Revises: 0010_ws52_release_artifacts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_ws53_deploy_obs"
down_revision = "0010_ws52_release_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "deployment_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "release_artifact_binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_artifact_bindings.id"),
            nullable=False,
        ),
        sa.Column(
            "implementation_work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column(
            "work_package_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_package_revisions.id"),
            nullable=False,
        ),
        sa.Column("package_revision_hash", sa.String(), nullable=False),
        sa.Column(
            "post_deploy_work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("observed_artifact_digest", sa.String(), nullable=False),
        sa.Column("deployment_ref", sa.Text(), nullable=False),
        sa.Column("deployment_url", sa.Text(), nullable=False),
        sa.Column("deployer", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("probe_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("route_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("auth_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "dispatch_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("post_deploy_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("evidence_ids", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "release_artifact_binding_id",
            "environment",
            name="uq_deployment_observation_binding_environment",
        ),
        sa.CheckConstraint(
            "package_revision_hash <> '' AND environment <> '' AND base_url <> '' "
            "AND observed_artifact_digest <> '' AND deployment_ref <> '' "
            "AND deployment_url <> '' AND deployer <> ''",
            name="ck_deployment_observations_required_text",
        ),
    )


def downgrade() -> None:
    op.drop_table("deployment_observations")
