# WS-3.4 Evidence Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire orchestrator protocol evidence and lifecycle facts to validated `factory-event/v1` publication/export records without changing canonical lifecycle ownership.

**Architecture:** Add `orchestrator` as a first-class `factory-event/v1` source in `security-standards`. In `orchestrator`, add an `event_publications` outbox table plus pure mapping and publication services; API/CLI expose queue/export/retry/status, and Evidence Pack displays read-only publication facts. Export is deterministic full-snapshot JSONL; tests use disposable paths only.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Pydantic, Typer, Jinja, `security-standards` `factory_events`/`agent_registry` helpers, pytest.

## Global Constraints

- Approved intent: `ws-3.4-evidence-events` revision 2, hash `8530173a7cd1ec70a40e4a177c7dae3db68170f11d3a9ea88563edf5188a9239`.
- Preserve orchestrator database as canonical lifecycle truth.
- External `factory-event/v1` records are observable audit events only.
- No factory-runner dispatch, `workflow_dispatch`, production deployment, Coolify mutation, Phase-5 verifier logic, tracker-canonical state, automatic merge, or worker-controlled completion.
- No secrets, live DSNs, BWS token material, or production event-store credentials in tracked files.
- Tests must not mutate live `~/.factory/events.jsonl`.
- Public API/CLI term is `event-publications`.
- Export writes deterministic full snapshots, not append-only cursor exports.
- Unknown actor fallback maps only protocol fixtures and explicitly historical replay rows.

---

## File Structure

Security-standards:

- Modify: `schema/factory-event.v1.schema.json` - add `orchestrator` to `source.system`.
- Modify: `tests/test_factory_envelope.py` or `tests/test_factory_cli.py` - assert the new source is accepted and unknown sources are still rejected.

Orchestrator:

- Create: `migrations/versions/0005_ws34_event_publications.py` - outbox table and indexes.
- Modify: `src/orchestrator/persistence/models.py` - `EventPublication` model and status constants.
- Create: `src/orchestrator/services/event_publications.py` - mapper, actor validation, queue/export/retry/list services.
- Modify: `src/orchestrator/api/schemas.py` - event-publication request/response models.
- Modify: `src/orchestrator/api/routes.py` - event-publication endpoints.
- Modify: `src/orchestrator/cli.py` - event-publication commands.
- Modify: `src/orchestrator/web/routes.py` - include publication facts in Evidence Pack context.
- Modify: `src/orchestrator/templates/evidence_pack.html` - read-only publication table.
- Add tests under `tests/services/test_event_publications.py`, `tests/api/test_event_publications_api.py`, `tests/cli/test_event_publications_cli.py`, `tests/web/test_evidence_pack.py`, `tests/architecture/test_ws34_scope_guards.py`, and `tests/persistence/test_migrations.py`.
- Create: `docs/evidence/ws-3.4-evidence-index.md` after implementation verification.

---

### Task 1: security-standards source-system support

**Repo:** `/Users/devon/Projects/security-standards`

**Files:**
- Modify: `schema/factory-event.v1.schema.json`
- Modify: `tests/test_factory_envelope.py`

**Interfaces:**
- Consumes: existing `factory_events.envelope.make_event(...)`.
- Produces: schema accepts `source.system == "orchestrator"`.

- [ ] **Step 1: Write failing schema test**

Add to `tests/test_factory_envelope.py`:

```python
def test_make_event_accepts_orchestrator_source():
    event = envelope.make_event(
        actor="devon",
        action="orchestrator.evidence_recorded",
        result="success",
        source={"system": "orchestrator", "ref": "orchestrator:evidence:abc"},
        timestamp="2026-07-06T18:00:00Z",
        evidence=[{"record": {"source_kind": "evidence"}}],
        event_id="evt-" + "a" * 64,
    )

    assert event["source"] == {
        "system": "orchestrator",
        "ref": "orchestrator:evidence:abc",
    }
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/devon/Projects/security-standards
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_envelope.py::test_make_event_accepts_orchestrator_source
```

Expected: FAIL with schema enum rejection for `orchestrator`.

- [ ] **Step 3: Implement minimal schema change**

In `schema/factory-event.v1.schema.json`, change:

```json
"system": {"enum": ["high-power-audit", "change-manager", "direct"]}
```

to:

```json
"system": {"enum": ["high-power-audit", "change-manager", "orchestrator", "direct"]}
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd /Users/devon/Projects/security-standards
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_envelope.py tests/test_factory_cli.py tests/test_agent_registry.py
make check
```

Expected: focused tests pass; `make check` passes with only existing pyright missing-source warnings.

- [ ] **Step 5: Review**

Review diff and confirm the only security-standards behavior change is the new source-system enum and tests.

---

### Task 2: outbox migration and model

**Repo:** `/Users/devon/Projects/orchestrator`

**Files:**
- Create: `migrations/versions/0005_ws34_event_publications.py`
- Modify: `src/orchestrator/persistence/models.py`
- Modify: `tests/persistence/test_migrations.py`

**Interfaces:**
- Produces: SQLAlchemy model `EventPublication`.
- Produces statuses: `pending`, `exported`, `published`, `skipped`, `rejected`, `failed`.

- [ ] **Step 1: Write failing migration test**

Add to `tests/persistence/test_migrations.py`:

```python
def test_ws34_event_publication_table_exists(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "event_publications" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("event_publications")}
    assert {
        "id",
        "source_system",
        "source_kind",
        "source_id",
        "source_action",
        "event_id",
        "mapping_version",
        "status",
        "skip_reason",
        "factory_event",
        "export_ref",
        "attempt_count",
        "last_error",
        "created_at",
        "updated_at",
        "last_attempted_at",
        "published_at",
    } <= columns
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/devon/Projects/orchestrator
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/persistence/test_migrations.py::test_ws34_event_publication_table_exists -q
```

Expected: FAIL because `event_publications` does not exist.

- [ ] **Step 3: Add migration**

Create `migrations/versions/0005_ws34_event_publications.py` with:

```python
"""WS-3.4 event publication outbox.

Revision ID: 0005_ws34_event_publications
Revises: 0004_ws33_protocol_runtime
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_ws34_event_publications"
down_revision = "0004_ws33_protocol_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_system", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_action", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("mapping_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("factory_event", postgresql.JSONB(), nullable=True),
        sa.Column("export_ref", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_system = 'orchestrator'", name="ck_event_publications_source_system"),
        sa.CheckConstraint(
            "source_kind IN ('event', 'evidence', 'adjudication', 'context_snapshot')",
            name="ck_event_publications_source_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'exported', 'published', 'skipped', 'rejected', 'failed')",
            name="ck_event_publications_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_event_publications_attempt_count"),
    )
    op.create_unique_constraint(
        "uq_event_publications_source_mapping",
        "event_publications",
        ["source_kind", "source_id", "mapping_version"],
    )
    op.create_unique_constraint(
        "uq_event_publications_event_id",
        "event_publications",
        ["event_id"],
    )
    op.create_index(
        "ix_event_publications_status",
        "event_publications",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_publications_status", table_name="event_publications")
    op.drop_constraint("uq_event_publications_event_id", "event_publications", type_="unique")
    op.drop_constraint("uq_event_publications_source_mapping", "event_publications", type_="unique")
    op.drop_table("event_publications")
```

- [ ] **Step 4: Add model**

Add constants and model to `src/orchestrator/persistence/models.py`:

```python
EVENT_PUBLICATION_KINDS = ("event", "evidence", "adjudication", "context_snapshot")
EVENT_PUBLICATION_STATUSES = ("pending", "exported", "published", "skipped", "rejected", "failed")


class EventPublication(UUIDPrimaryKey, Base):
    __tablename__ = "event_publications"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "mapping_version"),
        UniqueConstraint("event_id"),
        CheckConstraint("source_system = 'orchestrator'", name="ck_event_publications_source_system"),
        CheckConstraint(f"source_kind IN {EVENT_PUBLICATION_KINDS!r}", name="ck_event_publications_source_kind"),
        CheckConstraint(f"status IN {EVENT_PUBLICATION_STATUSES!r}", name="ck_event_publications_status"),
        CheckConstraint("attempt_count >= 0", name="ck_event_publications_attempt_count"),
    )

    source_system: Mapped[str] = mapped_column(String, default="orchestrator", server_default="orchestrator")
    source_kind: Mapped[str] = mapped_column(String)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_action: Mapped[str | None] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    mapping_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    factory_event: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    export_ref: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 5: Verify green**

Run:

```bash
cd /Users/devon/Projects/orchestrator
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/persistence/test_migrations.py -q
```

Expected: migrations pass.

---

### Task 3: pure mapper and actor validation

**Files:**
- Create: `src/orchestrator/services/event_publications.py`
- Add: `tests/services/test_event_publications.py`

**Interfaces:**
- `MAPPING_VERSION = "ws34.v1"`
- `source_ref(source_kind: str, source_id: uuid.UUID) -> str`
- `deterministic_factory_event_id(source_kind: str, source_id: uuid.UUID) -> str`
- `map_source_fact(session: Session, source_kind: str, source_id: uuid.UUID) -> MappingResult`

- [ ] **Step 1: Write failing mapper tests**

Add tests covering:

```python
def test_maps_evidence_event_to_valid_factory_event(migrated_session, review_unit):
    # arrange local evidence via existing service/fixture helpers
    # act: map_source_fact(session, "event", evidence.event_id)
    # assert schema == factory-event/v1, source.system == orchestrator,
    # action == orchestrator.evidence_recorded, work_package/input_revision populated
```

```python
def test_deterministic_event_id_is_stable_for_source_fact():
    source_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert deterministic_factory_event_id("evidence", source_id) == deterministic_factory_event_id("evidence", source_id)
```

```python
def test_unknown_current_actor_is_rejected_not_mapped(migrated_session, review_unit):
    # create an Event with actor_id="not-registered" for a non-fixture revision
    result = map_source_fact(migrated_session, "event", event.id)
    assert result.status == "rejected"
    assert "unregistered actor" in result.reason
```

- [ ] **Step 2: Verify red**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_event_publications.py -q
```

Expected: import/function failures.

- [ ] **Step 3: Implement mapper**

Create service dataclasses:

```python
@dataclass(frozen=True)
class MappingResult:
    status: str
    event_id: str
    factory_event: dict[str, Any] | None
    reason: str | None = None
```

Implement:

- joins from source fact to `WorkUnit`, `WorkPackageRevision`, and `WorkPackage`;
- action mapping table from design section 7;
- timestamp formatting as UTC `Z`;
- registered actor lookup using `agent_registry.registry.registered_ids`;
- fallback to `unknown` only when `revision.intake_source == "protocol_fixture"` or event/evidence payload explicitly marks historical replay;
- `factory_events.envelope.make_event(...)` validation.

- [ ] **Step 4: Verify green**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_event_publications.py -q
```

Expected: mapper tests pass.

---

### Task 4: queue, list, retry, and deterministic snapshot export services

**Files:**
- Modify: `src/orchestrator/services/event_publications.py`
- Modify: `tests/services/test_event_publications.py`

**Interfaces:**
- `queue_event_publications(session, source_kind=None, source_id=None) -> tuple[EventPublication, ...]`
- `list_event_publications(session, filters: EventPublicationFilters | None = None) -> tuple[EventPublication, ...]`
- `retry_event_publication(session, publication_id: uuid.UUID) -> EventPublication`
- `export_event_publications(session, output_path: Path) -> tuple[EventPublication, ...]`

- [ ] **Step 1: Write failing queue/export tests**

Cover:

- queue inserts one row per publishable source fact;
- repeated queue does not duplicate rows;
- unmapped actions create `skipped`;
- rejected actors create `rejected`;
- export writes deterministic full snapshot;
- export marks rows `exported`;
- failed export does not mutate lifecycle rows.

- [ ] **Step 2: Verify red**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_event_publications.py -q
```

- [ ] **Step 3: Implement services**

Use SQLAlchemy transactions only for outbox rows. Export ordering:

```python
ORDER BY event_publications.created_at, event_publications.event_id
```

Snapshot export writes all selected `pending`, `failed`, or `exported` rows with non-null `factory_event` and non-terminal validation state. Write to a temp file beside the target, then replace atomically.

- [ ] **Step 4: Verify green**

Run service tests.

---

### Task 5: API surface

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Add: `tests/api/test_event_publications_api.py`

**Interfaces:**
- `GET /api/v1/event-publications`
- `POST /api/v1/event-publications/queue`
- `POST /api/v1/event-publications/export`
- `POST /api/v1/event-publications/{publication_id}/retry`

- [ ] **Step 1: Write failing API tests**

Assert:

- list returns status, source refs, event IDs, errors;
- queue creates rows and is idempotent;
- export writes a JSONL snapshot to a test path;
- retry changes only publication status;
- lifecycle state is unchanged after failed export.

- [ ] **Step 2: Verify red**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/api/test_event_publications_api.py -q
```

- [ ] **Step 3: Implement schemas and routes**

Add Pydantic models:

```python
class EventPublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_system: str
    source_kind: str
    source_id: UUID
    source_action: str | None
    event_id: str
    mapping_version: str
    status: str
    skip_reason: str | None
    export_ref: str | None
    attempt_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    last_attempted_at: datetime | None
    published_at: datetime | None
```

Route bodies:

```python
class EventPublicationQueueCommand(BaseModel):
    source_kind: str | None = None
    source_id: UUID | None = None


class EventPublicationExportCommand(BaseModel):
    output_path: str = Field(min_length=1)
```

- [ ] **Step 4: Verify green**

Run API tests.

---

### Task 6: CLI parity

**Files:**
- Modify: `src/orchestrator/cli.py`
- Add: `tests/cli/test_event_publications_cli.py`

**Interfaces:**
- `event-publications list`
- `event-publications queue`
- `event-publications export`
- `event-publications retry`

- [ ] **Step 1: Write failing CLI tests**

Use existing CLI HTTP transport pattern to assert requests hit:

- `GET /api/v1/event-publications`;
- `POST /api/v1/event-publications/queue`;
- `POST /api/v1/event-publications/export`;
- `POST /api/v1/event-publications/{id}/retry`.

- [ ] **Step 2: Verify red**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/cli/test_event_publications_cli.py -q
```

- [ ] **Step 3: Implement Typer sub-app**

Add:

```python
event_publications_app = typer.Typer(no_args_is_help=True)
app.add_typer(event_publications_app, name="event-publications")
```

Implement commands using existing `_run` and `request`.

- [ ] **Step 4: Verify green**

Run CLI tests.

---

### Task 7: Evidence Pack read-only display

**Files:**
- Modify: `src/orchestrator/web/routes.py`
- Modify: `src/orchestrator/templates/evidence_pack.html`
- Modify: `tests/web/test_evidence_pack.py`

**Interfaces:**
- Evidence Pack context includes `event_publications_by_source`.

- [ ] **Step 1: Write failing web test**

Extend `tests/web/test_evidence_pack.py`:

```python
def test_evidence_pack_shows_read_only_event_publication_status(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    # insert EventPublication for an evidence row
    page = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)
    assert "Event publications" in page.text
    assert "orchestrator:evidence:" in page.text
    assert "<form" not in page.text
```

- [ ] **Step 2: Verify red**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/web/test_evidence_pack.py -q
```

- [ ] **Step 3: Implement read-only context and template**

Load publication rows for evidence, adjudication, context snapshots, and local events tied to the unit. Render a table with status, event ID, source ref, exported/published timestamp, and last error.

- [ ] **Step 4: Verify green**

Run web tests.

---

### Task 8: scope guards and live-store isolation

**Files:**
- Add: `tests/architecture/test_ws34_scope_guards.py`
- Modify: `tests/services/test_event_publications.py`

**Interfaces:**
- Architecture tests search repository for forbidden Phase-4/5 paths.

- [ ] **Step 1: Write guard tests**

Guard tests assert:

- no `workflow_dispatch` integration in orchestrator source;
- no GitHub Actions dispatch calls;
- no Coolify mutation strings;
- no automatic merge commands;
- no publication service uses default `~/.factory/events.jsonl` in tests;
- no route mutates lifecycle from event-publication endpoints.

- [ ] **Step 2: Verify guards**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/architecture/test_ws34_scope_guards.py -q
```

Expected: pass after implementation, or red if a forbidden seam exists.

---

### Task 9: documentation and evidence index

**Files:**
- Create: `docs/evidence/ws-3.4-evidence-index.md`
- Modify: `PROJECT.md` only if a genuinely non-obvious invariant is discovered.

**Steps:**

- [ ] Record package approval hash and design/plan paths.
- [ ] Record focused test evidence per implementation slice.
- [ ] Record full `orchestrator make check`.
- [ ] Record `security-standards make check` and focused tests if touched.
- [ ] Record scope-guard evidence and live-store isolation evidence.
- [ ] Add a Known Non-obvious Invariant only if implementation reveals a behavior not already in docs.

---

### Task 10: full verification and PR preparation

**Commands:**

Run:

```bash
cd /Users/devon/Projects/security-standards
make check
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_envelope.py tests/test_factory_cli.py tests/test_agent_registry.py
```

Run:

```bash
cd /Users/devon/Projects/orchestrator
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check
```

Run:

```bash
cd /Users/devon/Projects/intent-packages
PYTHONPATH=src .venv/bin/python -m intent_packages verify-approval packages/ws-3.4-evidence-events
PYTHONPATH=src .venv/bin/python -m intent_packages validate --all
make check
```

Expected:

- security-standards checks pass with only existing warnings/skips.
- orchestrator checks pass.
- intent-package approval remains verified and repo checks pass.

Then prepare ready-for-review PRs. Do not merge. Devon alone merges.

---

## Self-Review

Spec coverage:

- Canonical lifecycle ownership: Tasks 2, 4, 8.
- `security-standards` source-system update: Task 1.
- Mapping table and schema validation: Task 3.
- Deterministic IDs and idempotency: Tasks 3 and 4.
- Actor strategy: Task 3.
- Failure/retry/partial publication: Task 4.
- API/CLI/report surface: Tasks 5 and 6.
- Evidence Pack read-only display: Task 7.
- Live-store avoidance: Tasks 4 and 8.
- Phase-4/5 seams without implementation: Tasks 8 and 10.

Placeholder scan: no unresolved placeholder text or unspecified task remains.

Type consistency: public term is consistently `event-publications`; model is `EventPublication`; table is `event_publications`; mapping version is `ws34.v1`.
