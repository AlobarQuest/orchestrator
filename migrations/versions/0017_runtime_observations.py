"""Add immutable runtime-observation records.

Revision ID: 0017_runtime_observations
Revises: 0016_production_drill_resources
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_runtime_observations"
down_revision = "0016_production_drill_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("coolify_application_id", sa.String(), nullable=False),
        sa.Column("container_id", sa.String(), nullable=False),
        sa.Column("configured_image_ref", sa.Text(), nullable=False),
        sa.Column("observed_image_digest", sa.String(), nullable=False),
        sa.Column("openapi_sha256", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observer_actor_id", sa.String(), nullable=False),
        sa.Column("observer_credential_key_id", sa.String(), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint(
            "target <> '' AND coolify_application_id <> '' AND container_id <> '' "
            "AND configured_image_ref <> '' AND observed_image_digest <> '' "
            "AND openapi_sha256 <> '' AND observer_actor_id <> '' "
            "AND observer_credential_key_id <> ''",
            name="ck_runtime_observations_required_text",
        ),
    )
    op.execute(
        "CREATE TRIGGER reject_runtime_observations_mutation "
        "BEFORE UPDATE OR DELETE ON runtime_observations "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )
    # Existing historical runs predate this provenance source and cannot be honestly backfilled.
    # New runs are required by the start service to carry an observation; the nullable database
    # column preserves those old immutable rows without inventing an attestation for them.
    op.add_column(
        "production_drill_runs",
        sa.Column(
            "runtime_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runtime_observations.id"),
            nullable=True,
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_production_drill_run_provenance_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.revision_id IS DISTINCT FROM OLD.revision_id
             OR NEW.owner_actor_id IS DISTINCT FROM OLD.owner_actor_id
             OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
             OR NEW.image_ref IS DISTINCT FROM OLD.image_ref
             OR NEW.image_digest IS DISTINCT FROM OLD.image_digest
             OR NEW.openapi_digest IS DISTINCT FROM OLD.openapi_digest
             OR NEW.runtime_observation_id IS DISTINCT FROM OLD.runtime_observation_id THEN
            RAISE EXCEPTION 'production_drill_runs provenance is immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_production_drill_run_provenance_immutable()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.revision_id IS DISTINCT FROM OLD.revision_id
             OR NEW.owner_actor_id IS DISTINCT FROM OLD.owner_actor_id
             OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
             OR NEW.image_ref IS DISTINCT FROM OLD.image_ref
             OR NEW.image_digest IS DISTINCT FROM OLD.image_digest
             OR NEW.openapi_digest IS DISTINCT FROM OLD.openapi_digest THEN
            RAISE EXCEPTION 'production_drill_runs provenance is immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.drop_column("production_drill_runs", "runtime_observation_id")
    op.drop_table("runtime_observations")
