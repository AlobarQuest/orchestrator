"""Add WS-5.2 release artifact bindings.

Revision ID: 0010_ws52_release_artifacts
Revises: 0009_ws44_infra_lane_linkage
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_ws52_release_artifacts"
down_revision = "0009_ws44_infra_lane_linkage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "release_artifact_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_unit_id",
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
        sa.Column("source_repository", sa.String(), nullable=False),
        sa.Column("implementation_pr_number", sa.Integer(), nullable=True),
        sa.Column("source_commit", sa.String(), nullable=False),
        sa.Column("merge_commit", sa.String(), nullable=False),
        sa.Column("artifact_registry", sa.String(), nullable=False),
        sa.Column("artifact_repository", sa.String(), nullable=False),
        sa.Column("artifact_name", sa.String(), nullable=False),
        sa.Column("artifact_digest", sa.String(), nullable=False),
        sa.Column("artifact_tag", sa.String(), nullable=True),
        sa.Column("workflow_run_id", sa.String(), nullable=True),
        sa.Column("workflow_run_attempt", sa.Integer(), nullable=True),
        sa.Column("workflow_path", sa.Text(), nullable=True),
        sa.Column("workflow_ref", sa.Text(), nullable=True),
        sa.Column("workflow_run_url", sa.Text(), nullable=True),
        sa.Column("builder_id", sa.String(), nullable=True),
        sa.Column("builder_class", sa.String(), nullable=True),
        sa.Column("provenance_ref", sa.Text(), nullable=True),
        sa.Column("provenance_digest", sa.String(), nullable=True),
        sa.Column("sbom_ref", sa.Text(), nullable=True),
        sa.Column("sbom_digest", sa.String(), nullable=True),
        sa.Column("summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id")),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence.id")),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "work_package_revision_id",
            "work_unit_id",
            "source_repository",
            "merge_commit",
            "source_commit",
            "artifact_registry",
            "artifact_repository",
            "artifact_name",
            name="uq_release_artifact_source_tuple",
        ),
        sa.CheckConstraint(
            "implementation_pr_number IS NULL OR implementation_pr_number > 0",
            name="ck_release_artifact_positive_pr",
        ),
        sa.CheckConstraint(
            "workflow_run_attempt IS NULL OR workflow_run_attempt > 0",
            name="ck_release_artifact_positive_workflow_attempt",
        ),
        sa.CheckConstraint(
            "package_revision_hash <> '' AND source_repository <> '' "
            "AND source_commit <> '' AND merge_commit <> '' "
            "AND artifact_registry <> '' AND artifact_repository <> '' "
            "AND artifact_name <> '' AND artifact_digest <> ''",
            name="ck_release_artifact_required_text",
        ),
    )


def downgrade() -> None:
    op.drop_table("release_artifact_bindings")
