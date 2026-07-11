### Task 1: Migration `0014` + ORM models + config knob

**Files:**
- Create — `migrations/versions/0014_wsp21_recovery_controls.py`
- Modify — `src/orchestrator/persistence/models.py` (append new models after `Observation` (~line 732); `Index` is already in the `sqlalchemy` import at lines 5-17; add `Index(...)` to `Evidence.__table_args__` at lines 342-372)
- Modify — `src/orchestrator/config.py` (add one field to `Settings`, lines 7-32)
- Test — `tests/persistence/test_migrations.py` (append; existing file, mold at lines 730-763)
- Test — `tests/persistence/test_append_only.py` (append)
- Test — Create `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task). Existing: `reject_append_only_mutation()` PL/pgSQL function (created in `0001_ws31_core.py`, do **not** recreate); `migrated_engine` / `migrated_session` fixtures (`tests/persistence/conftest.py:18-33`); `alembic_config()` (`tests/persistence/conftest.py:12`).
- Produces:
  - Alembic revision `"0014_wsp21_recovery_controls"`, `down_revision = "0013_ws62_governed_promotion"`.
  - Tables `reconciliation_conditions`, `reconciliation_resolutions`, `unit_pr_binding`.
  - Partial unique index `uq_evidence_unsuperseded_head` on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL`.
  - ORM: `class ReconciliationCondition(UUIDPrimaryKey, Base)` (`__tablename__ = "reconciliation_conditions"`), `class ReconciliationResolution(UUIDPrimaryKey, Base)`, `class UnitPrBinding(Base)` (PK is `work_unit_id`, **not** `UUIDPrimaryKey`).
  - Module constants `RECONCILIATION_OBSERVATION_KINDS: tuple[str, ...]`, `RECONCILIATION_CONDITION_TYPES: tuple[str, ...]`, `RECONCILIATION_DECISIONS: tuple[str, ...]` in `persistence/models.py` — Task 5 imports these.
  - `Settings.reconcile_split_brain_stall_seconds: int` (env `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS`, default `900`).

> **Canonical contract — the config knob is added HERE and NOWHERE ELSE.** Task 8 (the AC-003 split-brain detect-pass) *consumes* `Settings.reconcile_split_brain_stall_seconds` via `get_settings()`. Task 8 must **not** re-add the field. Default is `900`.

> **Design note carried into implementation (from the adversarial review — honor it):** `WHERE supersedes_evidence_id IS NULL` enforces exactly **one chain root** per `(revision, unit, ac)`. That is what makes a *second independent head* impossible for the recovery path, which is the §2.1 wedge. It does **not** by itself prevent a *branched* chain (two rows both superseding the same parent) — that case remains foreclosed by §2.1's `pg_advisory_xact_lock` + re-read-under-lock, which the evidence-recovery task implements. Do not weaken either half.

- [ ] **Step 1.1 — Pre-flight data check (must run before the migration lands; check-and-ABORT, never check-and-fix).** The `evidence` append-only trigger makes an `UPDATE` remediation impossible, so a violating row can only be resolved by a human. Run against the target database:

```bash
psql "$ORCHESTRATOR_DATABASE_URL" -v ON_ERROR_STOP=1 -c "
SELECT work_package_revision_id, work_unit_id, ac_id, count(*) AS roots
FROM evidence
WHERE supersedes_evidence_id IS NULL
GROUP BY 1, 2, 3
HAVING count(*) > 1;"
```

Expected on a clean database: `(0 rows)`. If any row comes back, **stop** — do not run the migration, do not `ALTER TABLE evidence DISABLE TRIGGER`; escalate the offending `(revision, unit, ac)` triples to Devon.

- [ ] **Step 1.2 — Audit the other `supersedes_evidence_id IS NULL` writers.** Three code paths insert evidence rows with a NULL supersedes link: `services/evidence.py:_store_evidence` (guarded by the `evidence_already_exists` check at `evidence.py:368-378`), `services/release_artifacts.py`, `services/deployment_observations.py`. Confirm by inspection that neither of the latter two can write a *second* NULL-supersedes row for the same `(work_package_revision_id, work_unit_id, ac_id)` — `deployment_observations.py` mints a **fresh** post-deploy `WorkUnit` and five evidence rows on distinct `POST_DEPLOY_AC_IDS`, and `release_artifacts.py` writes one evidence row per binding. Record the finding in the PR description. If either could, the index breaks them and the plan must stop here.

- [ ] **Step 1.3 — Write the failing schema test.** Append to `tests/persistence/test_migrations.py`:

```python
def test_wsp21_recovery_tables_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "reconciliation_conditions" in inspector.get_table_names()
    assert "reconciliation_resolutions" in inspector.get_table_names()
    assert "unit_pr_binding" in inspector.get_table_names()

    condition_columns = {
        column["name"] for column in inspector.get_columns("reconciliation_conditions")
    }
    assert {
        "id",
        "work_unit_id",
        "observation_kind",
        "observation_id",
        "deployment_observation_id",
        "condition_type",
        "stored_state",
        "observed_state",
        "lineage_hash",
        "resolution_generation",
        "normalized_divergence_hash",
        "detail",
        "detected_at",
        "event_id",
        "idempotency_key",
    } <= condition_columns

    resolution_columns = {
        column["name"] for column in inspector.get_columns("reconciliation_resolutions")
    }
    assert {
        "id",
        "condition_id",
        "resolved_by",
        "decision",
        "rationale",
        "resolved_at",
        "event_id",
        "idempotency_key",
    } <= resolution_columns

    binding_columns = {column["name"] for column in inspector.get_columns("unit_pr_binding")}
    assert {
        "work_unit_id",
        "pr_number",
        "head_sha",
        "verification_read_head_sha",
        "updated_at",
    } <= binding_columns

    condition_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("reconciliation_conditions")
    }
    assert ("idempotency_key",) in condition_uniques
    assert (
        "work_unit_id",
        "observation_kind",
        "normalized_divergence_hash",
    ) in condition_uniques

    resolution_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("reconciliation_resolutions")
    }
    assert ("condition_id",) in resolution_uniques
    assert ("idempotency_key",) in resolution_uniques

    with migrated_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgname IN ("
                    "'reject_reconciliation_conditions_mutation', "
                    "'reject_reconciliation_resolutions_mutation', "
                    "'reject_unit_pr_binding_mutation')"
                )
            )
        )
    # unit_pr_binding is deliberately NOT append-only: head_sha is mutable (§1.6).
    assert triggers == {
        "reject_reconciliation_conditions_mutation",
        "reject_reconciliation_resolutions_mutation",
    }


def test_wsp21_evidence_head_index_exists(migrated_engine) -> None:
    indexes = {index["name"]: index for index in inspect(migrated_engine).get_indexes("evidence")}
    head_index = indexes["uq_evidence_unsuperseded_head"]

    assert head_index["unique"] is True
    assert head_index["column_names"] == [
        "work_package_revision_id",
        "work_unit_id",
        "ac_id",
    ]
    assert "supersedes_evidence_id IS NULL" in head_index["dialect_options"]["postgresql_where"]
```

- [ ] **Step 1.4 — Run it, confirm the expected failure.**

```bash
uv run pytest tests/persistence/test_migrations.py -k wsp21 -q
```

Expected: `AssertionError: assert 'reconciliation_conditions' in [...]` (the table list has no WS-P2.1 tables), and `KeyError: 'uq_evidence_unsuperseded_head'`.

- [ ] **Step 1.5 — Write the migration.** Create `migrations/versions/0014_wsp21_recovery_controls.py`:

```python
"""Add WS-P2.1 reconciliation conditions, resolutions, PR bindings, evidence head index.

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


def _assert_evidence_has_single_heads() -> None:
    """§10: the partial unique index must apply cleanly to existing rows.

    `evidence` carries the append-only trigger, so a violating row cannot be repaired by
    UPDATE. Abort loudly rather than land a migration that cannot succeed.
    """
    offenders = op.get_bind().execute(
        sa.text(
            "SELECT work_package_revision_id, work_unit_id, ac_id, count(*) AS roots "
            "FROM evidence WHERE supersedes_evidence_id IS NULL "
            "GROUP BY 1, 2, 3 HAVING count(*) > 1"
        )
    ).all()
    if offenders:
        raise RuntimeError(
            "evidence already has multiple unsuperseded heads for "
            f"{[(str(row[0]), str(row[1]), row[2]) for row in offenders]}; "
            "the append-only trigger forbids repair by UPDATE — resolve manually before "
            "applying 0014_wsp21_recovery_controls"
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
        # lineage_hash = sha256(kind, condition_type, canonical(key_facts)) — generation-free,
        # so the resolution count for a lineage is a plain equality join (Task 5).
        sa.Column("lineage_hash", sa.String(), nullable=False),
        sa.Column("resolution_generation", sa.Integer(), nullable=False, server_default="0"),
        # normalized_divergence_hash = sha256(lineage_hash, resolution_generation)
        sa.Column("normalized_divergence_hash", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
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
            "resolution_generation >= 0",
            name="ck_reconciliation_conditions_generation",
        ),
        sa.CheckConstraint(
            "lineage_hash <> '' AND normalized_divergence_hash <> '' "
            "AND detail <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_conditions_required_text",
        ),
    )
    op.create_index(
        "ix_reconciliation_conditions_unit",
        "reconciliation_conditions",
        ["work_unit_id"],
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
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        # §1.2: a condition is resolvable exactly once; a recurrence is a NEW condition.
        sa.UniqueConstraint("condition_id", name="uq_reconciliation_resolutions_condition"),
        sa.UniqueConstraint("idempotency_key", name="uq_reconciliation_resolutions_idempotency"),
        sa.CheckConstraint(
            f"decision IN {DECISIONS!r}",
            name="ck_reconciliation_resolutions_decision",
        ),
        sa.CheckConstraint(
            "resolved_by <> '' AND rationale <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_resolutions_required_text",
        ),
    )

    # §1.6: NOT append-only. head_sha is mutable (worker rebase/force-push is normal);
    # verification_read_head_sha is write-once, enforced by the service guard (Task 4).
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

    # The function already exists (0001_ws31_core.py) — do not recreate it.
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_{table}_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )

    # §2.1: structurally forecloses a second supersession head for one (revision, unit, ac).
    # Plain CREATE INDEX, not CONCURRENTLY: alembic runs migrations inside a transaction and
    # CONCURRENTLY cannot; it would also risk leaving an INVALID index behind on failure.
    _assert_evidence_has_single_heads()
    op.create_index(
        "uq_evidence_unsuperseded_head",
        "evidence",
        ["work_package_revision_id", "work_unit_id", "ac_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_evidence_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_evidence_unsuperseded_head", table_name="evidence")
    # DROP TABLE drops that table's triggers with it.
    op.drop_table("unit_pr_binding")
    op.drop_table("reconciliation_resolutions")
    op.drop_index("ix_reconciliation_conditions_lineage", table_name="reconciliation_conditions")
    op.drop_index("ix_reconciliation_conditions_unit", table_name="reconciliation_conditions")
    op.drop_table("reconciliation_conditions")
```

- [ ] **Step 1.6 — Write the ORM models.** In `src/orchestrator/persistence/models.py`, add these constants next to the existing `OBSERVATION_*` constants block (~line 60):

```python
RECONCILIATION_OBSERVATION_KINDS = ("github_pr", "github_check", "deployment")
RECONCILIATION_CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
)
RECONCILIATION_DECISIONS = ("accepted", "corrected", "dismissed")
```

and append the models after `Observation` (~line 732):

```python
class ReconciliationCondition(UUIDPrimaryKey, Base):
    __tablename__ = "reconciliation_conditions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reconciliation_conditions_idempotency"),
        UniqueConstraint(
            "work_unit_id",
            "observation_kind",
            "normalized_divergence_hash",
            name="uq_reconciliation_conditions_divergence",
        ),
        CheckConstraint(
            f"observation_kind IN {RECONCILIATION_OBSERVATION_KINDS!r}",
            name="ck_reconciliation_conditions_observation_kind",
        ),
        CheckConstraint(
            f"condition_type IN {RECONCILIATION_CONDITION_TYPES!r}",
            name="ck_reconciliation_conditions_type",
        ),
        CheckConstraint(
            "resolution_generation >= 0",
            name="ck_reconciliation_conditions_generation",
        ),
        CheckConstraint(
            "lineage_hash <> '' AND normalized_divergence_hash <> '' "
            "AND detail <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_conditions_required_text",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"))
    observation_kind: Mapped[str] = mapped_column(String)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("observations.id"))
    deployment_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployment_observations.id")
    )
    condition_type: Mapped[str] = mapped_column(String)
    stored_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    observed_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    lineage_hash: Mapped[str] = mapped_column(String)
    resolution_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    normalized_divergence_hash: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class ReconciliationResolution(UUIDPrimaryKey, Base):
    __tablename__ = "reconciliation_resolutions"
    __table_args__ = (
        UniqueConstraint("condition_id", name="uq_reconciliation_resolutions_condition"),
        UniqueConstraint("idempotency_key", name="uq_reconciliation_resolutions_idempotency"),
        CheckConstraint(
            f"decision IN {RECONCILIATION_DECISIONS!r}",
            name="ck_reconciliation_resolutions_decision",
        ),
        CheckConstraint(
            "resolved_by <> '' AND rationale <> '' AND idempotency_key <> ''",
            name="ck_reconciliation_resolutions_required_text",
        ),
    )

    condition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_conditions.id")
    )
    resolved_by: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    idempotency_key: Mapped[str] = mapped_column(String)


class UnitPrBinding(Base):
    """§1.6. Deliberately NOT append-only: `head_sha` is mutable and worker-written.

    `verification_read_head_sha` is the alarm-arming field and is write-once — enforced by
    the service guard in `services/pr_bindings.py`, never by a trigger, because the row must
    stay UPDATE-able for `head_sha`.
    """

    __tablename__ = "unit_pr_binding"
    __table_args__ = (
        CheckConstraint("pr_number > 0", name="ck_unit_pr_binding_positive_pr_number"),
        CheckConstraint("head_sha <> ''", name="ck_unit_pr_binding_head_sha"),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_units.id"), primary_key=True
    )
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String)
    verification_read_head_sha: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

and add the partial index to `Evidence.__table_args__` (mirroring `uq_approved_decompositions_active_revision` at the tail of the file):

```python
        Index(
            "uq_evidence_unsuperseded_head",
            "work_package_revision_id",
            "work_unit_id",
            "ac_id",
            unique=True,
            postgresql_where=text("supersedes_evidence_id IS NULL"),
        ),
```

- [ ] **Step 1.7 — Run the schema tests, confirm pass.**

```bash
uv run pytest tests/persistence/test_migrations.py -k wsp21 -q
```

Expected: `2 passed`.

- [ ] **Step 1.8 — Write the append-only trigger tests.** Append to `tests/persistence/test_append_only.py` (add `from tests.services.test_dependencies import register_unit` to its imports if absent):

```python
def _seed_condition(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (condition_id, resolution_id) after inserting one of each."""
    unit = register_unit(session, "reconcile-append-only")
    event_id = uuid.uuid4()
    condition_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO events (id, actor_id, action, subject_type, subject_id, "
            "payload, correlation_id, idempotency_key) VALUES "
            "(:id, 'system', 'reconciliation.required', 'reconciliation_condition', "
            ":subject_id, '{}', :correlation_id, :key)"
        ),
        {
            "id": event_id,
            "subject_id": condition_id,
            "correlation_id": uuid.uuid4(),
            "key": "reconcile:append-only:condition",
        },
    )
    session.execute(
        text(
            "INSERT INTO reconciliation_conditions (id, work_unit_id, observation_kind, "
            "condition_type, lineage_hash, resolution_generation, "
            "normalized_divergence_hash, detail, event_id, idempotency_key) VALUES "
            "(:id, :unit_id, 'github_pr', 'external_merge_alarm', 'sha256:lineage', 0, "
            "'sha256:divergence', 'pr merged outside the session', :event_id, :key)"
        ),
        {
            "id": condition_id,
            "unit_id": unit.id,
            "event_id": event_id,
            "key": "reconcile:append-only:condition",
        },
    )
    resolution_event_id = uuid.uuid4()
    resolution_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO events (id, actor_id, action, subject_type, subject_id, "
            "payload, correlation_id, idempotency_key) VALUES "
            "(:id, 'devon', 'reconciliation.resolved', 'reconciliation_resolution', "
            ":subject_id, '{}', :correlation_id, :key)"
        ),
        {
            "id": resolution_event_id,
            "subject_id": resolution_id,
            "correlation_id": uuid.uuid4(),
            "key": "reconcile:append-only:resolution",
        },
    )
    session.execute(
        text(
            "INSERT INTO reconciliation_resolutions (id, condition_id, resolved_by, "
            "decision, rationale, event_id, idempotency_key) VALUES "
            "(:id, :condition_id, 'devon', 'accepted', 'acknowledged', :event_id, :key)"
        ),
        {
            "id": resolution_id,
            "condition_id": condition_id,
            "event_id": resolution_event_id,
            "key": "reconcile:append-only:resolution",
        },
    )
    session.commit()
    return condition_id, resolution_id


@pytest.mark.parametrize(
    "table",
    ["reconciliation_conditions", "reconciliation_resolutions"],
)
def test_wsp21_reconciliation_tables_reject_update_and_delete(
    migrated_session: Session, table: str
) -> None:
    condition_id, resolution_id = _seed_condition(migrated_session)
    row_id = condition_id if table == "reconciliation_conditions" else resolution_id

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(f"UPDATE {table} SET idempotency_key = 'mutated' WHERE id = :id"),
            {"id": row_id},
        )
        migrated_session.commit()
    migrated_session.rollback()

    with pytest.raises(IntegrityError):
        migrated_session.execute(
            text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id}
        )
        migrated_session.commit()
    migrated_session.rollback()


def test_wsp21_unit_pr_binding_head_sha_is_mutable(migrated_session: Session) -> None:
    """§1.6: worker rebases/force-pushes are normal; the table must NOT be append-only."""
    unit = register_unit(migrated_session, "pr-binding-mutable")
    migrated_session.execute(
        text(
            "INSERT INTO unit_pr_binding (work_unit_id, pr_number, head_sha) "
            "VALUES (:unit_id, 41, 'a' * 40)"
        ),
        {"unit_id": unit.id},
    )
    migrated_session.commit()

    migrated_session.execute(
        text("UPDATE unit_pr_binding SET head_sha = :sha WHERE work_unit_id = :unit_id"),
        {"sha": "b" * 40, "unit_id": unit.id},
    )
    migrated_session.commit()

    assert (
        migrated_session.scalar(
            text("SELECT head_sha FROM unit_pr_binding WHERE work_unit_id = :unit_id"),
            {"unit_id": unit.id},
        )
        == "b" * 40
    )
```

- [ ] **Step 1.9 — Run them, confirm pass.**

```bash
uv run pytest tests/persistence/test_append_only.py -k "wsp21" -q
```

Expected: `3 passed`. (These pass immediately — the migration from Step 1.5 already created the triggers; they are the regression pins, not a red-first step, because Steps 1.3/1.4 already drove the schema.)

- [ ] **Step 1.10 — Write the failing partial-index tests.** Append to `tests/services/test_evidence.py` (it already imports `Evidence`, `IntegrityError`, `append_evidence`, `supersede_evidence`, `current_evidence`):

```python
def test_second_unsuperseded_evidence_head_is_structurally_rejected(
    migrated_session: Session, ready_unit
) -> None:
    """§2.1: two supersession heads permanently wedge a unit; the partial unique index
    makes the second head impossible even when _store_evidence's guard is bypassed."""
    grant = active_claim(migrated_session, ready_unit)
    first = append_evidence(
        migrated_session, **evidence_kwargs(ready_unit, grant), idempotency_key="ev-head-1"
    )
    assert isinstance(first, Evidence)

    migrated_session.add(
        Evidence(
            work_package_revision_id=ready_unit.work_package_revision_id,
            work_unit_id=ready_unit.id,
            ac_id="ac-1",
            attempt=grant.attempt,
            evidence_type="test",
            stable_ref="artifact://second-head",
            payload=None,
            source_revision="abc123",
            recorded_by="system",
            event_id=uuid.uuid4(),
            idempotency_key="ev-head-2",
            supersedes_evidence_id=None,
        )
    )
    with pytest.raises(IntegrityError) as error:
        migrated_session.flush()
    assert "uq_evidence_unsuperseded_head" in str(error.value)
    migrated_session.rollback()


def test_superseding_evidence_row_is_allowed(migrated_session: Session, ready_unit) -> None:
    grant = active_claim(migrated_session, ready_unit)
    first = append_evidence(
        migrated_session, **evidence_kwargs(ready_unit, grant), idempotency_key="ev-super-1"
    )
    assert isinstance(first, Evidence)

    second = supersede_evidence(
        migrated_session, **evidence_kwargs(ready_unit, grant), idempotency_key="ev-super-2"
    )
    assert isinstance(second, Evidence)
    assert second.supersedes_evidence_id == first.id

    head = current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    )
    assert head is not None
    assert head.id == second.id
```

- [ ] **Step 1.11 — Run them, confirm pass.**

```bash
uv run pytest tests/services/test_evidence.py -k "head" -q
```

Expected: `2 passed`. If `test_second_unsuperseded_evidence_head_is_structurally_rejected` fails with "no IntegrityError raised", the `Index(...)` in `Evidence.__table_args__` is missing or the migration index name is misspelled.

- [ ] **Step 1.12 — Add the config knob (test first).** Create `tests/test_config.py`:

```python
from orchestrator.config import Settings


def test_split_brain_stall_seconds_defaults_and_is_env_overridable(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", raising=False)
    assert Settings.model_validate({}).reconcile_split_brain_stall_seconds == 900

    monkeypatch.setenv("ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS", "5")
    assert Settings.model_validate({}).reconcile_split_brain_stall_seconds == 5
```

> Construct `Settings` directly, never `get_settings()` — it is `@lru_cache`d (`config.py:34-36`) and would hand back a stale object across tests.

- [ ] **Step 1.13 — Run it, confirm the expected failure.**

```bash
uv run pytest tests/test_config.py -q
```

Expected: `AttributeError: 'Settings' object has no attribute 'reconcile_split_brain_stall_seconds'`.

- [ ] **Step 1.14 — Add the field.** In `src/orchestrator/config.py`, inside `class Settings`, after `brain_proposal_timeout_seconds: float = 10.0`:

```python
    # §1.4/AC-003: how long a post-deploy verification unit may sit in SUBMITTED before the
    # detect-pass calls it `deploy_split_brain`. Production must exceed a normal
    # verification's worst case; drill 4 sets it low via the env var so it needs no sleep.
    # CANONICAL: declared here ONLY. Task 8 consumes it via get_settings(); it must not re-add it.
    reconcile_split_brain_stall_seconds: int = 900
```

- [ ] **Step 1.15 — Run it, confirm pass.**

```bash
uv run pytest tests/test_config.py -q
```

Expected: `1 passed`.

- [ ] **Step 1.16 — Full gate + commit.**

```bash
uv run pytest tests/persistence tests/services/test_evidence.py tests/test_config.py -q && make check
git add migrations/versions/0014_wsp21_recovery_controls.py src/orchestrator/persistence/models.py src/orchestrator/config.py tests/persistence/test_migrations.py tests/persistence/test_append_only.py tests/services/test_evidence.py tests/test_config.py
git commit -m "$(cat <<'EOF'
WS-P2.1: add reconciliation condition/resolution tables, unit_pr_binding, evidence head index

Migration 0014 adds the two append-only reconciliation tables (design §1.2), the mutable
unit_pr_binding (§1.6), and the partial unique index on evidence
(work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL (§2.1),
which structurally forecloses a second supersession head — the wedge that would otherwise
make a unit permanently uncompletable. The migration aborts if existing evidence rows already
violate the index, because the append-only trigger forbids repair by UPDATE.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```

---

### Task 2: Shared primitive — circuit-breaker predicate split

**Files:**
- Modify — `src/orchestrator/services/dispatch.py` (replace `_opens_circuit`, lines 384-397; update the single call site, line 189)
- Test — `tests/services/test_dispatch.py` (append; the existing circuit test at line 255 is the unchanged-behavior pin)

**Interfaces:**
- Consumes: `DispatchSettings.failure_signature_threshold: int` (`dispatch.py:36`); `DispatchRecord` (`persistence/models.py:448`).
- Produces (both public — the AC-005 dead-letter view imports them):
  - `signature_failure_count(session: Session, unit_id: uuid.UUID, signature: str) -> int`
  - `circuit_open(count: int, threshold: int) -> bool`
  - Contract: **dispatch is prospective** — it counts the failure it is about to write, so it calls `circuit_open(signature_failure_count(...) + 1, threshold)`. The **at-rest** view counts what is already there: `circuit_open(signature_failure_count(...), threshold)`. The `+ 1` lives **only** at the dispatch call site; `circuit_open` is exactly `count >= threshold`.

- [ ] **Step 2.1 — Write the failing predicate tests.** Append to `tests/services/test_dispatch.py`, extending its import block (lines 14-22) with `circuit_open`, `failure_signature`, `signature_failure_count`, and `from orchestrator.persistence.models import DispatchRecord, Event`:

```python
def test_circuit_open_is_a_pure_at_rest_predicate() -> None:
    assert circuit_open(2, 3) is False
    assert circuit_open(3, 3) is True
    assert circuit_open(4, 3) is True


def test_prospective_and_at_rest_predicates_are_off_by_one(
    migrated_session: Session,
) -> None:
    """dispatch.py:189 counts the failure it is ABOUT to write; the dead-letter view counts
    the failures already on disk. Two rows at rest with threshold 3: the view must report the
    breaker CLOSED, while a third dispatch attempt must see it OPEN."""
    unit = ready_unit(migrated_session, key="offbyone")
    signature = failure_signature("workflow_dispatch", "workflow_not_found", "404")
    for attempt in (1, 2):
        migrated_session.add(
            DispatchRecord(
                work_unit_id=unit.id,
                work_package_revision_id=unit.work_package_revision_id,
                runner_attempt=attempt,
                status="failed",
                reason_code="workflow_not_found",
                idempotency_key=f"dispatch-offbyone-{attempt}",
                target_repository=PILOT_REPOSITORY,
                workflow_id="factory-runner-pilot.yml",
                workflow_ref="main",
                failure_signature=signature,
                payload={},
            )
        )
    migrated_session.commit()

    count = signature_failure_count(migrated_session, unit.id, signature)
    assert count == 2
    # at rest (the dead-letter view): not yet open
    assert circuit_open(count, 3) is False
    # prospective (dispatch): the next failure opens it
    assert circuit_open(count + 1, 3) is True


def test_signature_failure_count_only_counts_that_signature_on_that_unit(
    migrated_session: Session,
) -> None:
    unit = ready_unit(migrated_session, key="scoped")
    other = ready_unit(migrated_session, key="scoped-other")
    signature = failure_signature("workflow_dispatch", "workflow_not_found", "404")
    other_signature = failure_signature("workflow_dispatch", "forbidden", "403")
    rows = (
        (unit, 1, "failed", signature),
        (unit, 2, "blocked", signature),
        (unit, 3, "failed", other_signature),
        (unit, 4, "dispatched", signature),  # not a failure status
        (other, 1, "failed", signature),  # different unit
    )
    for index, (target, attempt, status, sig) in enumerate(rows):
        migrated_session.add(
            DispatchRecord(
                work_unit_id=target.id,
                work_package_revision_id=target.work_package_revision_id,
                runner_attempt=attempt,
                status=status,
                idempotency_key=f"dispatch-scoped-{index}",
                target_repository=PILOT_REPOSITORY,
                workflow_id="factory-runner-pilot.yml",
                workflow_ref="main",
                failure_signature=sig,
                payload={},
            )
        )
    migrated_session.commit()

    # only ("failed", "blocked") on this unit with this signature: attempts 1 and 2
    assert signature_failure_count(migrated_session, unit.id, signature) == 2
```

- [ ] **Step 2.2 — Run them, confirm the expected failure.**

```bash
uv run pytest tests/services/test_dispatch.py -k "circuit_open or off_by_one or signature_failure_count" -q
```

Expected: `ImportError: cannot import name 'circuit_open' from 'orchestrator.services.dispatch'`.

- [ ] **Step 2.3 — Split the predicate.** In `src/orchestrator/services/dispatch.py`, replace `_opens_circuit` (lines 384-397) with:

```python
def signature_failure_count(session: Session, unit_id: uuid.UUID, signature: str) -> int:
    """Failures ALREADY on disk for this (unit, failure signature). At rest — no `+ 1`."""
    return session.scalar(
        select(func.count())
        .select_from(DispatchRecord)
        .where(
            DispatchRecord.work_unit_id == unit_id,
            DispatchRecord.failure_signature == signature,
            DispatchRecord.status.in_(("failed", "blocked")),
        )
    )


def circuit_open(count: int, threshold: int) -> bool:
    """One predicate, two call sites with different tenses.

    Dispatch is PROSPECTIVE: it is about to write the failure it is judging, so it passes
    `count + 1`. The dead-letter view (AC-005) reads what is already there, so it passes
    `count`. Putting the `+ 1` inside this function would show the view a breaker that is
    open one failure early; putting it at both call sites would double-count.
    """
    return count >= threshold
```

Change the import at `dispatch.py:10` to `from sqlalchemy import func, select`.

- [ ] **Step 2.4 — Update the single call site.** In `src/orchestrator/services/dispatch.py`, replace line 189:

```python
        status = (
            "blocked"
            if circuit_open(
                signature_failure_count(session, unit.id, signature) + 1,
                settings.failure_signature_threshold,
            )
            else "failed"
        )
```

Leave it exactly where it is — inside the `except GitHubDispatchError:` branch. Hoisting it would add a query to the success path and count pre-failure state.

- [ ] **Step 2.5 — Run the new tests and the whole existing dispatch suite; pre-existing behavior must be byte-identical.**

```bash
uv run pytest tests/services/test_dispatch.py -q
```

Expected: all pass, including the untouched `test_dispatch_circuit_breaker_blocks_repeated_failure_signature` (line 255), which still asserts `third.reason_code == "failure_signature_circuit_open"` — i.e. with `failure_signature_threshold=3`, the *third* dispatch is the one that blocks. If that test now blocks on the second dispatch, the `+ 1` was duplicated inside `circuit_open`.

- [ ] **Step 2.6 — Commit.**

```bash
make check && git add src/orchestrator/services/dispatch.py tests/services/test_dispatch.py
git commit -m "$(cat <<'EOF'
WS-P2.1: split the prospective circuit-breaker predicate into count + at-rest predicate

_opens_circuit was prospective (len(failures) + 1 >= threshold) — it counted the failure it
was about to write. Reusing it at rest for the AC-005 dead-letter view would show a breaker
open one failure early. Split into signature_failure_count() + circuit_open(count, threshold);
dispatch passes count + 1, the view will pass count. Dispatch behavior is unchanged — the
existing circuit-breaker test is untouched and still green.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```

---

### Task 3: Shared primitive — claim release

**Files:**
- Modify — `src/orchestrator/services/claims.py` (extract lines 251-252 out of `_perform_reclaim`; add the primitive above it)
- Test — `tests/services/test_claims.py` (append)
- Test — `tests/services/test_reclaim.py` (**unchanged** — it is the behavior-identical pin)

**Interfaces:**
- Consumes: `Claim` (`persistence/models.py:254-280`); `TransactionClock().now(session)` (`clock.py:12-14`).
- Produces:
  - `release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None`
  - Contract: this is now the **only** writer of `Claim.released_at` and `Claim.terminal_reason` in the codebase. The recover-evidence task (in `services/evidence.py`) calls it instead of writing those columns itself. **It takes `released_at` as a parameter** and takes no `Session` — `_perform_reclaim` already holds `now` (`claims.py:249`) and reuses it for the `FAILED` transition's `occurred_at`; passing it in keeps `_perform_reclaim`'s behavior identical by construction rather than by accident of `transaction_timestamp()` being stable within a transaction.

- [ ] **Step 3.1 — Write the failing primitive tests.** Append to `tests/services/test_claims.py`, adding `from orchestrator.clock import TransactionClock`, `from orchestrator.persistence.models import Claim`, and `release_claim` to the `orchestrator.services.claims` import:

```python
def test_release_claim_is_the_single_writer_of_released_at_and_terminal_reason(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-1")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    assert claim.released_at is None
    assert claim.terminal_reason is None

    now = TransactionClock().now(migrated_session)
    release_claim(claim, terminal_reason="lease_expired", released_at=now)
    migrated_session.commit()

    migrated_session.refresh(claim)
    assert claim.released_at == now
    assert claim.terminal_reason == "lease_expired"


def test_release_claim_rejects_a_blank_terminal_reason(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-2")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None

    with pytest.raises(DomainError) as error:
        release_claim(
            claim,
            terminal_reason="",
            released_at=TransactionClock().now(migrated_session),
        )
    assert error.value.code == "terminal_reason_required"


def test_release_claim_refuses_to_release_an_already_released_claim(
    migrated_session: Session, ready_unit
) -> None:
    """A double release would silently rewrite the terminal reason — which is exactly what
    having one writer is meant to prevent."""
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-release-3")
    assert isinstance(grant, LeaseGrant)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    now = TransactionClock().now(migrated_session)
    release_claim(claim, terminal_reason="lease_expired", released_at=now)

    with pytest.raises(DomainError) as error:
        release_claim(claim, terminal_reason="recovered_from_expired_lease", released_at=now)
    assert error.value.code == "claim_already_released"
    assert claim.terminal_reason == "lease_expired"
```

- [ ] **Step 3.2 — Run them, confirm the expected failure.**

```bash
uv run pytest tests/services/test_claims.py -k release_claim -q
```

Expected: `ImportError: cannot import name 'release_claim' from 'orchestrator.services.claims'`.

- [ ] **Step 3.3 — Add the primitive.** In `src/orchestrator/services/claims.py`, above `_perform_reclaim`:

```python
def release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None:
    """The single writer of `Claim.released_at` / `Claim.terminal_reason`.

    Factored out of `_perform_reclaim` (design §2.2) so evidence recovery can release an
    expired-but-unreleased claim without becoming a second writer — which is what makes the
    reclaim/recovery interaction disjoint by construction rather than by convention.

    `released_at` is a parameter, not a fresh `TransactionClock().now(session)` call, so the
    caller's transaction timestamp is reused verbatim.
    """
    if not terminal_reason:
        raise DomainError(
            "terminal_reason_required",
            "releasing a claim requires an attributable terminal reason",
            None,
        )
    if claim.released_at is not None:
        raise DomainError(
            "claim_already_released",
            "claim has already been released",
            None,
        )
    claim.released_at = released_at
    claim.terminal_reason = terminal_reason
```

- [ ] **Step 3.4 — Rewire `_perform_reclaim`.** In `src/orchestrator/services/claims.py`, replace lines 251-252 with:

```python
    release_claim(claim, terminal_reason="lease_expired", released_at=now)
```

`_validate_expired_active_claim(unit, claim, now)` on line 249 already raises `lease_not_expired` when `claim.released_at is not None`, so `release_claim`'s `claim_already_released` guard is unreachable from this path — it exists for the recovery caller. Nothing else in `_perform_reclaim` changes.

- [ ] **Step 3.5 — Run the new tests plus the full reclaim suite unchanged.**

```bash
uv run pytest tests/services/test_claims.py tests/services/test_reclaim.py tests/services/test_claim_concurrency.py -q
```

Expected: all pass. `tests/services/test_reclaim.py` is deliberately **not edited** — it is the proof that `_perform_reclaim` behaves identically.

- [ ] **Step 3.6 — Prove there is still exactly one writer.**

```bash
rg -n 'released_at\s*=|terminal_reason\s*=' src/orchestrator/ --type py
```

Expected: exactly the two assignment lines inside `release_claim`, plus keyword-argument uses (`terminal_reason="lease_expired"`) and the `mapped_column` declarations in `persistence/models.py`. No other assignment to either attribute.

- [ ] **Step 3.7 — Commit.**

```bash
make check && git add src/orchestrator/services/claims.py tests/services/test_claims.py
git commit -m "$(cat <<'EOF'
WS-P2.1: factor the claim-release write out of _perform_reclaim into release_claim()

claims.py:251-252 was the only writer of released_at/terminal_reason. Evidence recovery
(§2.2) must release an expired-but-unreleased claim; doing that inline would make a second
writer and reintroduce the double-release/orphaned-claim race. release_claim(claim, *,
terminal_reason, released_at) is now the single writer; _perform_reclaim calls it and the
reclaim suite is untouched and green.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```

---

### Task 4: `unit_pr_binding` service

**Files:**
- Create — `src/orchestrator/services/pr_bindings.py`
- Test — Create `tests/services/test_pr_bindings.py`

**Interfaces:**
- Consumes: `UnitPrBinding` (Task 1); `WorkUnit` (`persistence/models.py`); `DomainError` (`errors.py`); `TransactionClock` (`clock.py`); `ActorContext` (`services/lifecycle.py`); `ActorRole` (`kernel/states.py`).
- Produces (Task 5's detection and the AC-001 head-change alarm consume all three):
  - `upsert_pr_binding(session: Session, *, work_unit_id: uuid.UUID, pr_number: int, head_sha: str, actor: ActorContext) -> UnitPrBinding` — `head_sha` is mutable; a rebase/force-push overwrites it and raises nothing.
  - `record_verification_read_head(session: Session, *, work_unit_id: uuid.UUID, head_sha: str, actor: ActorContext) -> UnitPrBinding` — **write-once**. Re-recording the *same* sha is an idempotent no-op; recording a *different* sha raises `DomainError("verification_head_already_read", ...)`.
  - `get_pr_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None`
  - Error codes produced: `"pr_binding_not_found"`, `"verification_head_already_read"`, `"role_forbidden"`, `"work_unit_not_found"`.

- [ ] **Step 4.1 — Write the failing tests.** Create `tests/services/test_pr_bindings.py`:

```python
import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitPrBinding
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import (
    get_pr_binding,
    record_verification_read_head,
    upsert_pr_binding,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def test_upsert_creates_then_updates_head_sha(migrated_session: Session) -> None:
    """§1.6: pre-verification rebases and force-pushes are normal and must not alarm."""
    unit = register_unit(migrated_session, "pr-binding")

    created = upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=42, head_sha=HEAD_A, actor=SYSTEM
    )
    assert isinstance(created, UnitPrBinding)
    assert created.pr_number == 42
    assert created.head_sha == HEAD_A
    assert created.verification_read_head_sha is None

    rebased = upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=42, head_sha=HEAD_B, actor=SYSTEM
    )
    assert rebased.head_sha == HEAD_B
    assert rebased.verification_read_head_sha is None
    assert get_pr_binding(migrated_session, unit.id).head_sha == HEAD_B


def test_verification_read_head_is_write_once(migrated_session: Session) -> None:
    """The alarm-arming field. A later worker push must not be able to disarm it."""
    unit = register_unit(migrated_session, "pr-binding-armed")
    upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A, actor=SYSTEM
    )

    armed = record_verification_read_head(
        migrated_session, work_unit_id=unit.id, head_sha=HEAD_A, actor=SYSTEM
    )
    assert armed.verification_read_head_sha == HEAD_A

    with pytest.raises(DomainError) as error:
        record_verification_read_head(
            migrated_session, work_unit_id=unit.id, head_sha=HEAD_B, actor=SYSTEM
        )
    assert error.value.code == "verification_head_already_read"

    migrated_session.rollback()
    assert get_pr_binding(migrated_session, unit.id).verification_read_head_sha == HEAD_A


def test_a_later_head_sha_push_does_not_disarm_the_verification_read_head(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-binding-push-after-verify")
    upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A, actor=SYSTEM
    )
    record_verification_read_head(
        migrated_session, work_unit_id=unit.id, head_sha=HEAD_A, actor=SYSTEM
    )

    upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_B, actor=SYSTEM
    )

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding.head_sha == HEAD_B
    assert binding.verification_read_head_sha == HEAD_A  # still armed at the verified head


def test_re_recording_the_same_verification_head_is_an_idempotent_no_op(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-binding-replay")
    upsert_pr_binding(
        migrated_session, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A, actor=SYSTEM
    )
    first = record_verification_read_head(
        migrated_session, work_unit_id=unit.id, head_sha=HEAD_A, actor=SYSTEM
    )
    replay = record_verification_read_head(
        migrated_session, work_unit_id=unit.id, head_sha=HEAD_A, actor=SYSTEM
    )
    assert replay.verification_read_head_sha == first.verification_read_head_sha == HEAD_A


def test_recording_a_verification_head_without_a_binding_is_rejected(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-binding-missing")

    with pytest.raises(DomainError) as error:
        record_verification_read_head(
            migrated_session, work_unit_id=unit.id, head_sha=HEAD_A, actor=SYSTEM
        )
    assert error.value.code == "pr_binding_not_found"


def test_non_system_actors_cannot_write_a_binding(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-binding-role")

    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A, actor=WORKER
        )
    assert error.value.code == "role_forbidden"


def test_binding_for_an_unknown_unit_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session,
            work_unit_id=uuid.uuid4(),
            pr_number=7,
            head_sha=HEAD_A,
            actor=SYSTEM,
        )
    assert error.value.code == "work_unit_not_found"


def test_get_pr_binding_returns_none_when_absent(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-binding-absent")
    assert get_pr_binding(migrated_session, unit.id) is None
```

- [ ] **Step 4.2 — Run them, confirm the expected failure.**

```bash
uv run pytest tests/services/test_pr_bindings.py -q
```

Expected: `ModuleNotFoundError: No module named 'orchestrator.services.pr_bindings'`.

- [ ] **Step 4.3 — Implement the service.** Create `src/orchestrator/services/pr_bindings.py`:

```python
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitPrBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext


def get_pr_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None:
    return session.get(UnitPrBinding, work_unit_id)


def upsert_pr_binding(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    pr_number: int,
    head_sha: str,
    actor: ActorContext,
) -> UnitPrBinding:
    """Records a unit's PR head. `head_sha` is MUTABLE by design (§1.6).

    A worker rebasing or force-pushing before verification is normal and must not alarm.
    The alarm is armed by `verification_read_head_sha`, which this function never touches —
    which is precisely why a later push cannot disarm it.
    """
    _authorize(actor)
    _require_unit(session, work_unit_id)
    binding = _locked_binding(session, work_unit_id)
    now = TransactionClock().now(session)
    if binding is None:
        binding = UnitPrBinding(
            work_unit_id=work_unit_id,
            pr_number=pr_number,
            head_sha=head_sha,
            verification_read_head_sha=None,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.pr_number = pr_number
        binding.head_sha = head_sha
        binding.updated_at = now
    session.flush()
    return binding


def record_verification_read_head(
    session: Session,
    *,
    work_unit_id: uuid.UUID,
    head_sha: str,
    actor: ActorContext,
) -> UnitPrBinding:
    """WRITE-ONCE (§1.6). Set when verification reads the head; never updated.

    The row is taken FOR UPDATE first, so two concurrent verifications cannot both observe
    NULL and both write. Re-recording the identical sha replays; a different sha is refused.
    """
    _authorize(actor)
    binding = _locked_binding(session, work_unit_id)
    if binding is None:
        raise DomainError(
            "pr_binding_not_found",
            "work unit has no PR binding to arm",
            "record the PR binding before verification reads its head",
        )
    existing = binding.verification_read_head_sha
    if existing is not None:
        if existing == head_sha:
            return binding
        raise DomainError(
            "verification_head_already_read",
            "verification has already read a head for this work unit",
            None,
        )
    binding.verification_read_head_sha = head_sha
    binding.updated_at = TransactionClock().now(session)
    session.flush()
    return binding


def _locked_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None:
    return session.scalar(
        select(UnitPrBinding)
        .where(UnitPrBinding.work_unit_id == work_unit_id)
        .with_for_update()
    )


def _require_unit(session: Session, work_unit_id: uuid.UUID) -> WorkUnit:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def _authorize(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may write a PR binding",
            None,
        )
```

- [ ] **Step 4.4 — Run them, confirm pass.**

```bash
uv run pytest tests/services/test_pr_bindings.py -q
```

Expected: `8 passed`.

- [ ] **Step 4.5 — Commit.**

```bash
make check && git add src/orchestrator/services/pr_bindings.py tests/services/test_pr_bindings.py
git commit -m "$(cat <<'EOF'
WS-P2.1: add the unit_pr_binding service with a write-once verification-read head

head_sha is mutable — a worker rebase or force-push before verification is normal and must
not alarm (design §1.6). verification_read_head_sha is the alarm-arming field and is
write-once: set when verification reads the head, never updated, taken FOR UPDATE so two
concurrent verifications cannot both observe NULL. A later worker push overwrites head_sha
and leaves the armed head intact, so the post-verification external-push alarm cannot be
disarmed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```

---

### Task 5: Reconciliation condition + resolution service

**Files:**
- Create — `src/orchestrator/services/reconciliation.py`
- Test — Create `tests/services/test_reconciliation.py`

**Interfaces:**
- Consumes: `ReconciliationCondition`, `ReconciliationResolution`, `RECONCILIATION_OBSERVATION_KINDS`, `RECONCILIATION_CONDITION_TYPES`, `RECONCILIATION_DECISIONS`, `Event`, `WorkUnit` (Task 1); `DomainError` (`errors.py`); `TransactionClock` (`clock.py`); `ActorContext` (`services/lifecycle.py`); `ActorRole` (`kernel/states.py`).
- Produces (the detect-pass, the on-ingest hook, the Task 5b `/review/reconciliation/conditions/{id}/resolution` HUMAN route, and the AC-008 consistency check all consume these):
  - `@dataclass(frozen=True) class ConditionCommand` — `actor: ActorContext`, `work_unit_id: uuid.UUID`, `observation_kind: str`, `condition_type: str`, `key_facts: dict[str, Any]`, `stored_state: dict[str, Any]`, `observed_state: dict[str, Any]`, `detail: str`, `observation_id: uuid.UUID | None = None`, `deployment_observation_id: uuid.UUID | None = None`
  - **`@dataclass(frozen=True) class ConditionOutcome`** — `condition: ReconciliationCondition`, `suppressed: bool`
  - **`record_reconciliation_condition(session: Session, command: ConditionCommand) -> ConditionOutcome | DomainError`** — **commits its own transaction** (§1.8: one condition per transaction, so one failure cannot roll back a detect-pass).
  - `@dataclass(frozen=True) class ResolutionCommand` — `actor: ActorContext`, `condition_id: uuid.UUID`, `decision: str`, `rationale: str`, `idempotency_key: str`
  - `record_resolution(session: Session, command: ResolutionCommand) -> ReconciliationResolution | DomainError`
  - `open_conditions(session: Session, work_unit_id: uuid.UUID | None = None) -> tuple[ReconciliationCondition, ...]` — set-difference: conditions with **no** resolution row.
  - `lineage_hash(observation_kind: str, condition_type: str, key_facts: dict[str, Any]) -> str` and `divergence_hash(lineage: str, generation: int) -> str` — exposed so the AC-008 consistency check can recompute independently.
  - Error codes produced: `"role_forbidden"`, `"work_unit_not_found"`, `"condition_not_found"`, `"condition_already_resolved"`, `"observation_kind_invalid"`, `"condition_type_invalid"`, `"detail_required"`, `"decision_invalid"`, `"idempotency_conflict"`, `"reconciliation_conflict"`.

> **CANONICAL — the return type is `ConditionOutcome`, not the bare row.** `suppressed=True` means an identical *unresolved* condition already existed and this call was a dedup replay-return (no new row, no new event). Tasks 6-9 count `suppressed_duplicates` (§1.7 — fail-open must be *counted*, not silent) directly off this flag; returning the bare row would make every one of those counters dead code.

> **Hash model (§1.2, sharpened).** `resolution_generation` must be countable, but `normalized_divergence_hash` *embeds* the generation — so it cannot be the grouping key. Task 1 therefore stores a generation-free `lineage_hash = sha256(kind, condition_type, canonical(key_facts))`, and `normalized_divergence_hash = sha256(lineage_hash, generation)`. `key_facts` is canonicalized **once at write time** with `json.dumps(sort_keys=True, separators=(",", ":"))` and never rehashed from the JSONB round-trip (jsonb reorders keys and drops duplicates).

> **Lock key (§1.8, sharpened).** The advisory lock must be taken on **`(work_unit_id, lineage_hash)`**, not on the idempotency key — the idempotency key embeds the generation, so two concurrent detectors that disagree about the generation would take *different* locks and fail to serialize. Lock on the lineage → count resolutions → derive generation → derive hash and key → insert.

> **CANONICAL — advisory-lock namespaces.** Reconciliation owns `0x57503231`. The evidence-head recovery lock (`services/evidence.py`) owns `0x57503232`. They must never share a namespace, or two unrelated writers would serialize against each other. (Existing: evidence `0x57503338`, observations `0x57533631`.)

- [ ] **Step 5.1 — Write the failing tests.** Create `tests/services/test_reconciliation.py`:

```python
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Event,
    ReconciliationCondition,
    ReconciliationResolution,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    ResolutionCommand,
    open_conditions,
    record_reconciliation_condition,
    record_resolution,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HUMAN = ActorContext("devon", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)


def flip_command(unit_id: uuid.UUID) -> ConditionCommand:
    return ConditionCommand(
        actor=SYSTEM,
        work_unit_id=unit_id,
        observation_kind="github_check",
        condition_type="check_result_flip",
        key_facts={"check_name": "Quality", "ac_id": "AC-001"},
        stored_state={"conclusion": "success"},
        observed_state={"conclusion": "failure"},
        detail="Quality flipped from success to failure after verification read it",
    )


def test_recording_a_condition_writes_the_condition_and_an_event(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "reconcile-basic")
    migrated_session.commit()

    outcome = record_reconciliation_condition(migrated_session, flip_command(unit.id))

    assert isinstance(outcome, ConditionOutcome)
    assert outcome.suppressed is False
    condition = outcome.condition
    assert condition.resolution_generation == 0
    assert condition.idempotency_key == (
        f"reconcile:{unit.id}:github_check:{condition.normalized_divergence_hash}"
    )
    event = migrated_session.get(Event, condition.event_id)
    assert event is not None
    assert event.action == "reconciliation.required"
    assert event.subject_type == "reconciliation_condition"
    assert event.subject_id == condition.id


def test_recording_a_condition_never_mutates_the_work_unit(
    migrated_session: Session,
) -> None:
    """§1.5 / failure modes #3-#4: detection must never auto-un-complete a completed unit
    and must never transition anything. `version` is the observable — `_transition` bumps it."""
    unit = register_unit(migrated_session, "reconcile-completed")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    state_before = unit.state
    version_before = unit.version
    updated_before = unit.updated_at

    outcome = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="external_merge_alarm",
            key_facts={"pr_number": 12, "head_sha": "a" * 40},
            stored_state={"state": "completed"},
            observed_state={"merged": True},
            detail="PR merged outside the session on a completed unit",
        ),
    )
    assert isinstance(outcome, ConditionOutcome)

    migrated_session.expire_all()
    unit = migrated_session.get(type(unit), unit.id)
    assert unit.state == state_before
    assert unit.version == version_before
    assert unit.updated_at == updated_before
    # and no lifecycle event was emitted for the unit
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.subject_type == "work_unit", Event.subject_id == unit.id)
        )
        == 1  # only the original registration event
    )


def test_an_unresolved_condition_dedups_on_re_detection(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "reconcile-dedup")
    migrated_session.commit()

    first = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    second = record_reconciliation_condition(migrated_session, flip_command(unit.id))

    assert isinstance(first, ConditionOutcome)
    assert isinstance(second, ConditionOutcome)
    assert first.suppressed is False
    # CANONICAL: Tasks 6-9 count suppressed_duplicates (§1.7) off exactly this flag.
    assert second.suppressed is True
    assert second.condition.id == first.condition.id  # replay-return, not a new row, not a 500
    assert (
        migrated_session.scalar(select(func.count()).select_from(ReconciliationCondition))
        == 1
    )
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.action == "reconciliation.required")
        )
        == 1  # M-B: no duplicate event either
    )


def test_a_condition_recurring_after_resolution_mints_a_new_row_and_a_new_event(
    migrated_session: Session,
) -> None:
    """M-B fix: check_result_flip and deploy_split_brain recur with IDENTICAL facts. A naive
    hash would swallow the re-detection forever and permanently blind the operator."""
    unit = register_unit(migrated_session, "reconcile-recur")
    migrated_session.commit()

    first = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(first, ConditionOutcome)
    assert first.suppressed is False
    assert first.condition.resolution_generation == 0

    resolution = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=first.condition.id,
            decision="corrected",
            rationale="Re-ran the check; it is green.",
            idempotency_key="resolve-recur-1",
        ),
    )
    assert isinstance(resolution, ReconciliationResolution)

    recurrence = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(recurrence, ConditionOutcome)
    # a recurrence AFTER resolution is a NEW condition, not a suppressed duplicate
    assert recurrence.suppressed is False
    assert recurrence.condition.id != first.condition.id
    assert recurrence.condition.resolution_generation == 1
    assert recurrence.condition.lineage_hash == first.condition.lineage_hash
    assert (
        recurrence.condition.normalized_divergence_hash
        != first.condition.normalized_divergence_hash
    )
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.action == "reconciliation.required")
        )
        == 2
    )


def test_a_condition_is_resolvable_exactly_once(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-once")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(outcome, ConditionOutcome)

    first = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=outcome.condition.id,
            decision="accepted",
            rationale="Acknowledged.",
            idempotency_key="resolve-once-1",
        ),
    )
    assert isinstance(first, ReconciliationResolution)

    second = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=outcome.condition.id,
            decision="dismissed",
            rationale="Changed my mind.",
            idempotency_key="resolve-once-2",
        ),
    )
    assert isinstance(second, DomainError)
    assert second.code == "condition_already_resolved"
    assert (
        migrated_session.scalar(select(func.count()).select_from(ReconciliationResolution))
        == 1
    )


def test_resolution_duplicate_delivery_replays_and_never_500s(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "reconcile-replay")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(outcome, ConditionOutcome)
    command = ResolutionCommand(
        actor=HUMAN,
        condition_id=outcome.condition.id,
        decision="accepted",
        rationale="Acknowledged.",
        idempotency_key="resolve-replay-1",
    )

    first = record_resolution(migrated_session, command)
    replay = record_resolution(migrated_session, command)

    assert isinstance(first, ReconciliationResolution)
    assert isinstance(replay, ReconciliationResolution)
    assert replay.id == first.id
    assert (
        migrated_session.scalar(select(func.count()).select_from(ReconciliationResolution))
        == 1
    )


def test_open_conditions_is_the_set_difference(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-open")
    migrated_session.commit()
    resolved = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(resolved, ConditionOutcome)
    record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=HUMAN,
            condition_id=resolved.condition.id,
            decision="accepted",
            rationale="Acknowledged.",
            idempotency_key="resolve-open-1",
        ),
    )
    still_open = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_pr",
            condition_type="pr_state_divergence",
            key_facts={"pr_number": 3, "head_sha": "c" * 40},
            stored_state={"head_sha": "a" * 40},
            observed_state={"head_sha": "c" * 40},
            detail="head changed after verification read it",
        ),
    )
    assert isinstance(still_open, ConditionOutcome)

    open_rows = open_conditions(migrated_session, unit.id)

    assert tuple(row.id for row in open_rows) == (still_open.condition.id,)


def test_only_the_system_actor_may_record_a_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-role")
    migrated_session.commit()

    error = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=WORKER,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={},
            observed_state={},
            detail="worker-submitted condition",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "role_forbidden"


def test_only_a_human_may_resolve_a_condition(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-resolve-role")
    migrated_session.commit()
    outcome = record_reconciliation_condition(migrated_session, flip_command(unit.id))
    assert isinstance(outcome, ConditionOutcome)

    error = record_resolution(
        migrated_session,
        ResolutionCommand(
            actor=SYSTEM,
            condition_id=outcome.condition.id,
            decision="accepted",
            rationale="Auto-resolved.",
            idempotency_key="resolve-role-1",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "role_forbidden"  # invariant #4: never auto-resolve


def test_key_facts_key_order_does_not_change_the_hash(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "reconcile-canonical")
    migrated_session.commit()

    first = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"ac_id": "AC-001", "check_name": "Quality"},
            stored_state={},
            observed_state={},
            detail="flip",
        ),
    )
    reordered = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality", "ac_id": "AC-001"},
            stored_state={},
            observed_state={},
            detail="flip",
        ),
    )

    assert isinstance(first, ConditionOutcome)
    assert isinstance(reordered, ConditionOutcome)
    assert reordered.suppressed is True
    assert reordered.condition.id == first.condition.id


def test_unknown_work_unit_is_rejected(migrated_session: Session) -> None:
    error = record_reconciliation_condition(
        migrated_session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=uuid.uuid4(),
            observation_kind="github_pr",
            condition_type="external_merge_alarm",
            key_facts={"pr_number": 1},
            stored_state={},
            observed_state={},
            detail="ghost unit",
        ),
    )

    assert isinstance(error, DomainError)
    assert error.code == "work_unit_not_found"
```

- [ ] **Step 5.2 — Run them, confirm the expected failure.**

```bash
uv run pytest tests/services/test_reconciliation.py -q
```

Expected: `ModuleNotFoundError: No module named 'orchestrator.services.reconciliation'`.

- [ ] **Step 5.3 — Implement the service.** Create `src/orchestrator/services/reconciliation.py`:

```python
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    RECONCILIATION_CONDITION_TYPES,
    RECONCILIATION_DECISIONS,
    RECONCILIATION_OBSERVATION_KINDS,
    Event,
    ReconciliationCondition,
    ReconciliationResolution,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext

# CANONICAL namespace allocation — an advisory lock taken here must never collide with one
# taken by another writer, or two unrelated ingresses would serialize against each other.
#   reconciliation            0x57503231  (this module)
#   evidence-head recovery    0x57503232  (services/evidence.py)
#   evidence (existing)       0x57503338
#   observations (existing)   0x57533631
IDEMPOTENCY_LOCK_NAMESPACE = 0x57503231


@dataclass(frozen=True)
class ConditionCommand:
    actor: ActorContext
    work_unit_id: uuid.UUID
    observation_kind: str
    condition_type: str
    key_facts: dict[str, Any]
    stored_state: dict[str, Any]
    observed_state: dict[str, Any]
    detail: str
    observation_id: uuid.UUID | None = None
    deployment_observation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ConditionOutcome:
    """`suppressed=True` means an identical UNRESOLVED condition already existed and this
    call was a dedup replay-return: no new row, no new event.

    §1.7: skip-never-raise and dedup-swallow are the two silent-miss modes. The detect-pass
    and the on-ingest hook report `suppressed_duplicates` off this flag, so a miss is
    observable rather than invisible. Returning the bare row would make that counter
    impossible to compute.
    """

    condition: ReconciliationCondition
    suppressed: bool


@dataclass(frozen=True)
class ResolutionCommand:
    actor: ActorContext
    condition_id: uuid.UUID
    decision: str
    rationale: str
    idempotency_key: str


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def lineage_hash(observation_kind: str, condition_type: str, key_facts: dict[str, Any]) -> str:
    """Generation-FREE identity of a divergence. Grouping key for the resolution count.

    Canonicalized once, here, at write time — never recomputed from the stored JSONB, which
    reorders keys and drops duplicates and would therefore hash to something else.
    """
    encoded = _canonical(
        {
            "observation_kind": observation_kind,
            "condition_type": condition_type,
            "key_facts": key_facts,
        }
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def divergence_hash(lineage: str, generation: int) -> str:
    """M-B fix (§1.2): folding the resolution generation in is what lets a RESOLVED
    divergence be re-raised. Without it, a recurring check_result_flip hits the UNIQUE, is
    silently swallowed, and reconciliation.required is never re-emitted — permanently blinding
    the operator to exactly the condition types that recur with identical facts."""
    encoded = _canonical({"lineage_hash": lineage, "resolution_generation": generation})
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_reconciliation_condition(
    session: Session, command: ConditionCommand
) -> ConditionOutcome | DomainError:
    """§1.8: each condition is written in its OWN transaction, so one failure cannot roll back
    an entire detect-pass. Never raises past the caller — a 500 here would let a malformed
    correlation DoS the observation ingest path."""
    try:
        outcome = _record_condition(session, command)
        session.commit()
        return outcome
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        # Constraint-name agnostic on purpose: uq_..._idempotency and uq_..._divergence are
        # equivalent by key derivation, and which one PostgreSQL reports is not contractual.
        session.rollback()
        return DomainError(
            "reconciliation_conflict",
            "condition conflicts with an existing reconciliation condition",
            "re-run detection; an equivalent condition is already recorded",
        )
    except Exception:
        session.rollback()
        raise


def _record_condition(session: Session, command: ConditionCommand) -> ConditionOutcome:
    _authorize_system(command.actor)
    _validate_condition(command)
    unit = session.get(WorkUnit, command.work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)

    lineage = lineage_hash(command.observation_kind, command.condition_type, command.key_facts)
    # Lock on the LINEAGE, not the idempotency key: the key embeds the generation, so two
    # detectors that disagree about the generation would take different locks and not
    # serialize. Lock -> count -> derive generation -> derive key.
    _lock_lineage(session, command.work_unit_id, lineage)

    generation = _resolution_generation(session, command.work_unit_id, lineage)
    divergence = divergence_hash(lineage, generation)
    idempotency_key = (
        f"reconcile:{command.work_unit_id}:{command.observation_kind}:{divergence}"
    )

    existing = session.scalar(
        select(ReconciliationCondition).where(
            ReconciliationCondition.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        _validate_idempotent_replay(session, existing, command, divergence)
        return ConditionOutcome(condition=existing, suppressed=True)

    event_with_key = session.scalar(
        select(Event).where(Event.idempotency_key == idempotency_key)
    )
    if event_with_key is not None:
        raise _idempotency_conflict()

    now = TransactionClock().now(session)
    condition_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="reconciliation.required",
            subject_type="reconciliation_condition",
            subject_id=condition_id,
            # No from_state/to_state and no work_unit write: §1.5, detection only appends.
            from_state=None,
            to_state=None,
            payload={"command": _event_payload(command, lineage, generation, divergence)},
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    row = ReconciliationCondition(
        id=condition_id,
        work_unit_id=command.work_unit_id,
        observation_kind=command.observation_kind,
        observation_id=command.observation_id,
        deployment_observation_id=command.deployment_observation_id,
        condition_type=command.condition_type,
        stored_state=command.stored_state,
        observed_state=command.observed_state,
        lineage_hash=lineage,
        resolution_generation=generation,
        normalized_divergence_hash=divergence,
        detail=command.detail,
        detected_at=now,
        event_id=event_id,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    session.flush()
    return ConditionOutcome(condition=row, suppressed=False)


def record_resolution(
    session: Session, command: ResolutionCommand
) -> ReconciliationResolution | DomainError:
    try:
        row = _record_resolution(session, command)
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "condition_already_resolved",
            "condition already has a resolution",
            "a recurrence mints a new condition; it is not a re-resolution",
        )
    except Exception:
        session.rollback()
        raise


def _record_resolution(
    session: Session, command: ResolutionCommand
) -> ReconciliationResolution:
    _authorize_human(command.actor)
    if command.decision not in RECONCILIATION_DECISIONS:
        raise DomainError(
            "decision_invalid",
            f"decision must be one of {RECONCILIATION_DECISIONS}",
            None,
        )
    _lock_idempotency_key(session, command.idempotency_key)

    existing = session.scalar(
        select(ReconciliationResolution).where(
            ReconciliationResolution.idempotency_key == command.idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.condition_id != command.condition_id
            or existing.decision != command.decision
            or existing.rationale != command.rationale
            or existing.resolved_by != command.actor.actor_id
        ):
            raise _idempotency_conflict()
        return existing

    condition = session.scalar(
        select(ReconciliationCondition)
        .where(ReconciliationCondition.id == command.condition_id)
        .with_for_update()
    )
    if condition is None:
        raise DomainError(
            "condition_not_found", "reconciliation condition does not exist", None
        )

    already = session.scalar(
        select(ReconciliationResolution).where(
            ReconciliationResolution.condition_id == command.condition_id
        )
    )
    if already is not None:
        # UNIQUE(condition_id) enforces this structurally; this is the readable path.
        raise DomainError(
            "condition_already_resolved",
            "condition has already been resolved",
            "a recurrence mints a new condition; it is not a re-resolution",
        )

    now = TransactionClock().now(session)
    resolution_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="reconciliation.resolved",
            subject_type="reconciliation_resolution",
            subject_id=resolution_id,
            from_state=None,
            to_state=None,
            payload={
                "command": {
                    "condition_id": str(command.condition_id),
                    "decision": command.decision,
                    "rationale": command.rationale,
                    "resolved_by": command.actor.actor_id,
                }
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    row = ReconciliationResolution(
        id=resolution_id,
        condition_id=command.condition_id,
        resolved_by=command.actor.actor_id,
        decision=command.decision,
        rationale=command.rationale,
        resolved_at=now,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def open_conditions(
    session: Session, work_unit_id: uuid.UUID | None = None
) -> tuple[ReconciliationCondition, ...]:
    """§1.2: an open condition is one with NO resolution row — a set difference, mirroring
    evidence supersession. There is no `status` column to drift."""
    resolved = select(ReconciliationResolution.condition_id)
    stmt = select(ReconciliationCondition).where(
        ReconciliationCondition.id.not_in(resolved)
    )
    if work_unit_id is not None:
        stmt = stmt.where(ReconciliationCondition.work_unit_id == work_unit_id)
    stmt = stmt.order_by(
        ReconciliationCondition.detected_at, ReconciliationCondition.id
    )
    return tuple(session.scalars(stmt))


def _resolution_generation(
    session: Session, work_unit_id: uuid.UUID, lineage: str
) -> int:
    """How many conditions on this lineage have already been resolved.

    Valid as a generation counter because UNIQUE(condition_id) makes each resolution row
    correspond to exactly one resolved condition on the lineage.
    """
    return session.scalar(
        select(func.count())
        .select_from(ReconciliationResolution)
        .join(
            ReconciliationCondition,
            ReconciliationResolution.condition_id == ReconciliationCondition.id,
        )
        .where(
            ReconciliationCondition.work_unit_id == work_unit_id,
            ReconciliationCondition.lineage_hash == lineage,
        )
    )


def _event_payload(
    command: ConditionCommand, lineage: str, generation: int, divergence: str
) -> dict[str, Any]:
    return {
        "work_unit_id": str(command.work_unit_id),
        "observation_kind": command.observation_kind,
        "observation_id": str(command.observation_id) if command.observation_id else None,
        "deployment_observation_id": (
            str(command.deployment_observation_id)
            if command.deployment_observation_id
            else None
        ),
        "condition_type": command.condition_type,
        "key_facts": command.key_facts,
        "stored_state": command.stored_state,
        "observed_state": command.observed_state,
        "detail": command.detail,
        "lineage_hash": lineage,
        "resolution_generation": generation,
        "normalized_divergence_hash": divergence,
    }


def _validate_idempotent_replay(
    session: Session,
    row: ReconciliationCondition,
    command: ConditionCommand,
    divergence: str,
) -> None:
    event = session.get(Event, row.event_id)
    expected = _event_payload(command, row.lineage_hash, row.resolution_generation, divergence)
    if (
        event is None
        or event.action != "reconciliation.required"
        or event.subject_id != row.id
        or event.payload.get("command") != expected
    ):
        raise _idempotency_conflict()


def _validate_condition(command: ConditionCommand) -> None:
    if command.observation_kind not in RECONCILIATION_OBSERVATION_KINDS:
        raise DomainError(
            "observation_kind_invalid",
            f"observation_kind must be one of {RECONCILIATION_OBSERVATION_KINDS}",
            None,
        )
    if command.condition_type not in RECONCILIATION_CONDITION_TYPES:
        raise DomainError(
            "condition_type_invalid",
            f"condition_type must be one of {RECONCILIATION_CONDITION_TYPES}",
            None,
        )
    if not command.detail:
        raise DomainError("detail_required", "a condition requires a detail", None)


def _authorize_system(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may record a reconciliation condition",
            None,
        )


def _authorize_human(actor: ActorContext) -> None:
    # Invariant #4: detection never auto-resolves. A resolution is an operator decision.
    if actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "role_forbidden",
            "only a human may resolve a reconciliation condition",
            None,
        )


def _lock_lineage(session: Session, work_unit_id: uuid.UUID, lineage: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:lineage_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "lineage_key": f"{work_unit_id}:{lineage}",
        },
    )


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different reconciliation record",
        "use a new idempotency key",
    )
```

- [ ] **Step 5.4 — Run them, confirm pass.**

```bash
uv run pytest tests/services/test_reconciliation.py -q
```

Expected: `11 passed`. If `test_recording_a_condition_never_mutates_the_work_unit` fails on `unit.version`, something in the path called `_transition` — remove it; §1.5 is structural.

- [ ] **Step 5.5 — Prove the service touches no lifecycle machinery.**

```bash
rg -n 'transition|WorkUnitState|authorize_transition|\.state\s*=|\.version\s*=' src/orchestrator/services/reconciliation.py
```

Expected: **no matches**. This is the §1.5 / failure-mode-#3 guard restated as a grep; the AC-011 architecture scan pins it permanently in a later task.

- [ ] **Step 5.6 — Commit.**

```bash
make check && git add src/orchestrator/services/reconciliation.py tests/services/test_reconciliation.py
git commit -m "$(cat <<'EOF'
WS-P2.1: add the reconciliation condition + resolution service

record_reconciliation_condition inserts into the two append-only tables and events ONLY — no
work_unit write, no transition (§1.5); a test drives a COMPLETED unit through it and asserts
state and version unchanged. It returns a ConditionOutcome(condition, suppressed) so the
detect-pass and the on-ingest hook can count suppressed_duplicates (§1.7 — fail-open must be
counted, not silent). The divergence hash folds in resolution_generation (M-B), so an
UNRESOLVED condition dedups (suppressed=True, replay-return) while a condition recurring AFTER
resolution mints a new row and a new reconciliation.required event — without which a recurring
check_result_flip would be swallowed by the UNIQUE forever. A generation-free lineage_hash is
stored so the generation is countable, key_facts is canonicalized once at write time (never
rehashed from JSONB), and the advisory lock is taken on the lineage rather than the
generation-bearing idempotency key so concurrent detectors actually serialize.
record_resolution is HUMAN-only with UNIQUE(condition_id): resolvable exactly once. Its HTTP
surface is Task 5b (/review/reconciliation/conditions/{id}/resolution).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```
