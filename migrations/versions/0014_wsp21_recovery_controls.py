"""Add WS-P2.1 reconciliation conditions/resolutions, PR bindings, evidence head index.

Revision ID: 0014_wsp21_recovery_controls
Revises: 0013_ws62_governed_promotion
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_wsp21_recovery_controls"
down_revision = "0013_ws62_governed_promotion"
branch_labels = None
depends_on = None

OBSERVATION_KINDS = ("github_pr", "github_check", "deployment")
CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
)
DECISIONS = ("accepted", "corrected", "dismissed")

APPEND_ONLY_TABLES = ("reconciliation_conditions", "reconciliation_resolutions")

# `release_artifacts` writes ONE evidence row per release-artifact binding, all under the
# constant ac_id 'release-artifact', all with supersedes_evidence_id IS NULL. A work unit may
# legitimately carry several bindings, so that triple genuinely has MANY unsuperseded rows.
# That evidence is pure append-only bookkeeping: it is never superseded, it is not in
# POST_DEPLOY_AC_IDS, and it is never passed to current_evidence()/_terminal(). The
# exactly-one-head invariant therefore applies only to evidence that participates in the
# supersession/adjudication path -- which is the only evidence recovery ever touches -- so the
# index carves the bookkeeping rows out. Widening it to all evidence breaks multi-binding units.
EVIDENCE_HEAD_BOOKKEEPING_AC_ID = "release-artifact"
EVIDENCE_HEAD_PREDICATE = (
    f"supersedes_evidence_id IS NULL AND ac_id <> '{EVIDENCE_HEAD_BOOKKEEPING_AC_ID}'"
)


def _assert_evidence_has_single_heads() -> None:
    """The partial unique index must apply cleanly to the rows already present.

    `evidence` carries the append-only trigger, so a violating row cannot be repaired by an
    UPDATE. Abort loudly and escalate rather than land a migration that cannot succeed. This is
    check-and-ABORT, never check-and-fix.
    """
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT work_package_revision_id, work_unit_id, ac_id, count(*) AS heads "
                f"FROM evidence WHERE {EVIDENCE_HEAD_PREDICATE} "
                "GROUP BY 1, 2, 3 HAVING count(*) > 1"
            )
        )
        .all()
    )
    if offenders:
        raise RuntimeError(
            "evidence already has multiple unsuperseded heads for "
            f"{[(str(r[0]), str(r[1]), r[2]) for r in offenders]}; the append-only trigger "
            "forbids repair by UPDATE - resolve manually before applying "
            "0014_wsp21_recovery_controls"
        )


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "reconciliation_conditions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            nullable=False,
        ),
        sa.Column("observation_kind", sa.String(), nullable=False),
        sa.Column(
            "observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("observations.id"),
            nullable=True,
        ),
        sa.Column(
            "deployment_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployment_observations.id"),
            nullable=True,
        ),
        sa.Column("condition_type", sa.String(), nullable=False),
        sa.Column("stored_state", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("observed_state", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Generation-FREE identity of a divergence, so the resolution count for a lineage is a
        # plain equality join. sha256(kind, condition_type, canonical(key_facts)).
        sa.Column("lineage_hash", sa.String(), nullable=False),
        sa.Column("resolution_generation", sa.Integer(), nullable=False, server_default="0"),
        # sha256(lineage_hash, resolution_generation). Folding the generation in is what lets a
        # RESOLVED divergence be raised again: without it a recurring check_result_flip hits the
        # UNIQUE below, is silently swallowed, and never re-emits reconciliation.required.
        sa.Column("normalized_divergence_hash", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_reconciliation_conditions_idempotency"),
        sa.UniqueConstraint(
            "work_unit_id",
            "observation_kind",
            "normalized_divergence_hash",
            name="uq_reconciliation_conditions_divergence",
        ),
        sa.CheckConstraint(
            f"observation_kind IN {OBSERVATION_KINDS!r}",
            name="ck_reconciliation_conditions_observation_kind",
        ),
        sa.CheckConstraint(
            f"condition_type IN {CONDITION_TYPES!r}",
            name="ck_reconciliation_conditions_type",
        ),
        sa.CheckConstraint(
            "resolution_generation >= 0", name="ck_reconciliation_conditions_generation"
        ),
        sa.CheckConstraint(
            "lineage_hash <> '' AND normalized_divergence_hash <> '' "
            "AND detail <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_conditions_required_text",
        ),
    )
    op.create_index(
        "ix_reconciliation_conditions_unit", "reconciliation_conditions", ["work_unit_id"]
    )
    op.create_index(
        "ix_reconciliation_conditions_lineage",
        "reconciliation_conditions",
        ["work_unit_id", "lineage_hash"],
    )

    op.create_table(
        "reconciliation_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "condition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reconciliation_conditions.id"),
            nullable=False,
        ),
        sa.Column("resolved_by", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        # Resolvable exactly once. A recurrence is a NEW condition, not a re-resolution.
        sa.UniqueConstraint("condition_id", name="uq_reconciliation_resolutions_condition"),
        sa.UniqueConstraint("idempotency_key", name="uq_reconciliation_resolutions_idempotency"),
        sa.CheckConstraint(
            f"decision IN {DECISIONS!r}", name="ck_reconciliation_resolutions_decision"
        ),
        sa.CheckConstraint(
            "resolved_by <> '' AND rationale <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_resolutions_required_text",
        ),
    )

    # NOT append-only. head_sha is mutable: a worker rebase or force-push before verification is
    # normal and must not alarm. verification_read_head_sha is the alarm-arming field and is
    # write-once, enforced by the service guard -- which is why this row must stay UPDATE-able.
    op.create_table(
        "unit_pr_binding",
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            primary_key=True,
        ),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(), nullable=False),
        sa.Column("verification_read_head_sha", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("pr_number > 0", name="ck_unit_pr_binding_positive_pr_number"),
        sa.CheckConstraint("head_sha <> ''", name="ck_unit_pr_binding_head_sha"),
    )

    # reject_append_only_mutation() already exists (0001_ws31_core) -- do not recreate it.
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )

    # Structurally forecloses a SECOND supersession head for one (revision, unit, ac). Two heads
    # make _terminal raise, so the AC can never be adjudicated and no further evidence can be
    # written -- and evidence is append-only, so the row could never be repaired and the unit
    # could never complete. Plain CREATE INDEX, not CONCURRENTLY: alembic runs inside a
    # transaction, and a failed CONCURRENTLY leaves an INVALID index behind.
    _assert_evidence_has_single_heads()
    op.create_index(
        "uq_evidence_unsuperseded_head",
        "evidence",
        ["work_package_revision_id", "work_unit_id", "ac_id"],
        unique=True,
        postgresql_where=sa.text(EVIDENCE_HEAD_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_evidence_unsuperseded_head", table_name="evidence")
    # DROP TABLE drops that table's triggers with it.
    op.drop_table("unit_pr_binding")
    op.drop_table("reconciliation_resolutions")
    op.drop_index("ix_reconciliation_conditions_lineage", table_name="reconciliation_conditions")
    op.drop_index("ix_reconciliation_conditions_unit", table_name="reconciliation_conditions")
    op.drop_table("reconciliation_conditions")
