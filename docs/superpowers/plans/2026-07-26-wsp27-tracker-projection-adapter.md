# WS-P2.7 Tracker Projection Adapter (Increment 1, outbound-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an out-of-process, outbound-only tracker projection adapter that mirrors canonical orchestrator work-unit state onto Todoist, plus a canonical-side `unit_tracker_bindings` table + SYSTEM-only API so the unit↔item mapping lives in the orchestrator, never in the tracker.

**Architecture:** Two pieces. (1) Orchestrator-side (`src/orchestrator/`, tracker-agnostic): a mutable `unit_tracker_bindings` table (one row/unit, mirroring `UnitPrBinding`), a SYSTEM-only upsert + auth-only list API, a `TRACKER_SYSTEMS` closed vocabulary, migration `0018`. (2) Adapter (`src/tracker_projection_adapter/`, a new sibling package outside `src/orchestrator/`, mirroring `src/reconciliation_runner/`): a `typer` console script that reads canonical state via the public API (SYSTEM bearer), upserts one Todoist task per unit via the Todoist REST API, and writes the binding back. One-directional data flow; the adapter's HTTP client structurally forbids every write except the binding POST. The tracker is never canonical (program exit #9).

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy (`Mapped`/`mapped_column`), Alembic, Pydantic v2, `typer`, `httpx` (raw Todoist REST — no `todoist` package), pytest.

**Design spec:** `docs/superpowers/specs/2026-07-26-wsp27-tracker-projection-adapter-design.md`

## Global Constraints

- **The `todoist` import prefix is banned inside `src/orchestrator/`** (`tests/architecture/test_scope_guards.py::test_application_has_no_external_mutation_integrations`). All tracker I/O lives ONLY in `src/tracker_projection_adapter/`.
- **New orchestrator-side code (under `src/orchestrator/`) must not contain the bare tokens `dispatch`, `deploy` (ws32) or `merges` (ws33) in code OR docstrings/strings.** Use "projection", "tracker binding", "mirror". (The adapter package is outside all scope guards.)
- **Every new `/api/v1` route must be added to the explicit route-inventory set literals** in `tests/architecture/test_scope_guards.py` (POST set and/or GET set) in the same change, or CI fails.
- **Every `/api/v1` mutation body inherits `CommandBase`** (`idempotency_key` + `expected_version`); every `/api/v1` success response has a JSON schema via `response_model=`.
- **Route input parsing must raise `DomainError`, never let the stdlib raise** (a bare `ValueError`/`IntegrityError` → unhandled HTTP 500). Service pre-validates every value a DB CHECK would reject.
- **Request entry points own their transaction and must `session.commit()`.** A test asserting persistence must re-read after `expire_all()`, not assert on the returned instance.
- **The adapter imports nothing from `orchestrator.*`; the orchestrator imports nothing from `tracker_projection_adapter.*`;** adapter third-party deps ⊆ `{httpx, typer}` (plus stdlib). Enforced by a new isolation test.
- **`make check` must be green on a clean tree** (needs Postgres at `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, a migrated DB). Read the collected-test count, not just exit 0. Run `ruff format` (never on `.json`/`.toml`) before every commit.
- **Alembic head at start is `0017_wsp23_waiver_risk_class`.** Confirm with `.venv/bin/alembic heads` before writing migration `0018`; set `down_revision` to the actual head.
- Use `.venv/bin/` tools (repo-local venv before global PATH).

---

## File Structure

**Orchestrator-side (modify/create under `src/orchestrator/`):**
- `persistence/models.py` — add `TRACKER_SYSTEMS` tuple + `UnitTrackerBinding` model.
- `migrations/versions/0018_wsp27_tracker_bindings.py` — create the table.
- `services/tracker_bindings.py` — `upsert_tracker_binding`, `get_tracker_binding`, `list_tracker_bindings`, auth + validation.
- `api/schemas.py` — `TrackerBindingCommand`, `TrackerBindingResponse`.
- `api/routes.py` — POST `/work-units/{unit_id}/tracker-binding`, GET `/tracker-bindings`.
- `tests/architecture/test_scope_guards.py` — add both paths to the inventory literals.

**Adapter (create under `src/tracker_projection_adapter/`):**
- `__init__.py` — module docstring.
- `orchestrator_client.py` — httpx client; single-endpoint write allowlist.
- `projection.py` — pure planning logic + view dataclasses + `TERMINAL_STATES`.
- `tracker.py` — `TrackerProjector` protocol + `TodoistProjector`.
- `cli.py` — `typer` app, `project` command, env-var tokens, `--dry-run`.

**Ops/docs:**
- `pyproject.toml` — add `tracker-projection-adapter` console script.
- `scripts/run-tracker-projection.sh` — BWS→env launcher.
- `.bws-secrets.toml` — add the Todoist token UUID.
- `docs/decisions/0003-tracker-projection-outbound-only.md` — ADR-0003.

**Tests (create):**
- `tests/services/test_tracker_bindings.py`
- `tests/api/test_tracker_bindings_api.py`
- `tests/architecture/test_tracker_projection_adapter_isolation.py`
- `tests/tracker_projection_adapter/test_orchestrator_client.py`
- `tests/tracker_projection_adapter/test_projection.py`
- `tests/tracker_projection_adapter/test_vocabulary_sync.py`
- `tests/tracker_projection_adapter/test_tracker.py`
- `tests/tracker_projection_adapter/test_cli.py`

---

### Task 1: `unit_tracker_bindings` model + vocabulary + migration

**Files:**
- Modify: `src/orchestrator/persistence/models.py`
- Create: `src/orchestrator/migrations/versions/0018_wsp27_tracker_bindings.py`
- Test: `tests/services/test_tracker_bindings.py` (created here, extended in Task 2)

**Interfaces:**
- Produces: `orchestrator.persistence.models.TRACKER_SYSTEMS: tuple[str, ...] = ("todoist",)`; `orchestrator.persistence.models.UnitTrackerBinding` with columns `work_unit_id: uuid.UUID` (PK, FK→work_units.id), `tracker_system: str`, `external_item_id: str`, `external_url: str | None`, `projected_state: str`, `updated_at: datetime`.

- [ ] **Step 1: Confirm the Alembic head**

Run: `.venv/bin/alembic heads`
Expected: prints `0017_wsp23_waiver_risk_class (head)`. If different, use the printed head as `down_revision` below.

- [ ] **Step 2: Write the failing test** (`tests/services/test_tracker_bindings.py`)

Note: reuse the same work-unit creation helper the PR-binding tests use — run `grep -rl upsert_pr_binding tests/` to find it, and copy its unit-creation fixture/helper import. Below, `make_work_unit(session)` stands for that helper (returns a persisted `WorkUnit` with a real `.id`).

```python
import pytest
from sqlalchemy.exc import IntegrityError

from orchestrator.persistence.models import UnitTrackerBinding


def test_tracker_binding_row_persists_and_rejects_bad_tracker_system(session):
    unit = make_work_unit(session)  # replace with the shared helper
    binding = UnitTrackerBinding(
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-123",
        external_url="https://todoist.com/app/task/task-123",
        projected_state="ready",
    )
    session.add(binding)
    session.commit()
    session.expire_all()
    reread = session.get(UnitTrackerBinding, unit.id)
    assert reread.tracker_system == "todoist"
    assert reread.external_item_id == "task-123"

    bad = UnitTrackerBinding(
        work_unit_id=unit.id,
        tracker_system="jira",
        external_item_id="x",
        projected_state="ready",
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/services/test_tracker_bindings.py -v`
Expected: FAIL — `ImportError: cannot import name 'UnitTrackerBinding'` (or table-missing error).

- [ ] **Step 4: Add the vocabulary + model to `models.py`**

Add near the other closed-vocabulary tuples (around `RECONCILIATION_OBSERVATION_KINDS`, models.py:~1063):

```python
TRACKER_SYSTEMS = ("todoist",)
```

Add the model directly after `class UnitPrBinding` (models.py:~1198), mirroring its exact `Mapped[]`/`mapped_column` idiom:

```python
class UnitTrackerBinding(Base):
    """A work unit's projection onto an external tracker item.

    Projection only and one-directional: the orchestrator is always canonical and this row
    records merely THAT a unit is mirrored to some external tracker item. It carries no
    lifecycle authority, and writing it never changes work-unit state. Mutable, one row per
    unit (PK on work_unit_id), mirroring UnitPrBinding.
    """

    __tablename__ = "unit_tracker_bindings"
    __table_args__ = (
        CheckConstraint(
            f"tracker_system IN {TRACKER_SYSTEMS!r}",
            name="ck_unit_tracker_bindings_tracker_system",
        ),
        CheckConstraint(
            "external_item_id <> ''",
            name="ck_unit_tracker_bindings_external_item_id",
        ),
    )

    work_unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_units.id"), primary_key=True)
    tracker_system: Mapped[str] = mapped_column(String)
    external_item_id: Mapped[str] = mapped_column(String)
    external_url: Mapped[str | None] = mapped_column(String)
    projected_state: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 5: Create migration `0018`** (`src/orchestrator/migrations/versions/0018_wsp27_tracker_bindings.py`)

```python
"""wsp27 tracker bindings

Revision ID: 0018_wsp27_tracker_bindings
Revises: 0017_wsp23_waiver_risk_class
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_wsp27_tracker_bindings"
down_revision = "0017_wsp23_waiver_risk_class"
branch_labels = None
depends_on = None

# Frozen copy of orchestrator.persistence.models.TRACKER_SYSTEMS.
# Migrations never import model constants (established convention, see 0014).
TRACKER_SYSTEMS = ("todoist",)


def upgrade() -> None:
    op.create_table(
        "unit_tracker_bindings",
        sa.Column(
            "work_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_units.id"),
            primary_key=True,
        ),
        sa.Column("tracker_system", sa.String(), nullable=False),
        sa.Column("external_item_id", sa.String(), nullable=False),
        sa.Column("external_url", sa.String(), nullable=True),
        sa.Column("projected_state", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"tracker_system IN {TRACKER_SYSTEMS!r}",
            name="ck_unit_tracker_bindings_tracker_system",
        ),
        sa.CheckConstraint(
            "external_item_id <> ''",
            name="ck_unit_tracker_bindings_external_item_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("unit_tracker_bindings")
```

- [ ] **Step 6: Apply the migration to the test DB and run the test**

Run: `.venv/bin/alembic upgrade head && .venv/bin/pytest tests/services/test_tracker_bindings.py -v`
Expected: `alembic` upgrades to `0018_wsp27_tracker_bindings`; test PASSES. (The test DB is created/migrated by the suite fixtures; if the table is missing, confirm the migration file was picked up by `.venv/bin/alembic heads`.)

- [ ] **Step 7: Format + commit**

```bash
.venv/bin/ruff format src/orchestrator/persistence/models.py src/orchestrator/migrations/versions/0018_wsp27_tracker_bindings.py tests/services/test_tracker_bindings.py
git add src/orchestrator/persistence/models.py src/orchestrator/migrations/versions/0018_wsp27_tracker_bindings.py tests/services/test_tracker_bindings.py
git commit -m "feat(wsp27): unit_tracker_bindings model, vocabulary, migration 0018"
```

---

### Task 2: `tracker_bindings` service (upsert / get / list, SYSTEM-only, validation)

**Files:**
- Create: `src/orchestrator/services/tracker_bindings.py`
- Test: `tests/services/test_tracker_bindings.py` (extend)

**Interfaces:**
- Consumes: `UnitTrackerBinding`, `TRACKER_SYSTEMS` (Task 1); `ActorContext` + `ActorRole`; `DomainError`; `TransactionClock`.
- Produces:
  - `upsert_tracker_binding(session, *, actor: ActorContext, work_unit_id: uuid.UUID, tracker_system: str, external_item_id: str, external_url: str | None, projected_state: str) -> UnitTrackerBinding` (commits; SYSTEM-only).
  - `get_tracker_binding(session, work_unit_id: uuid.UUID) -> UnitTrackerBinding | None`.
  - `list_tracker_bindings(session, *, tracker_system: str | None = None) -> list[UnitTrackerBinding]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/services/test_tracker_bindings.py`)

Use the actor/context helpers the PR-binding service tests use — `grep -rn "ActorContext(" tests/services/` to find how a SYSTEM/WORKER `ActorContext` is built. Below, `system_actor()` / `worker_actor()` stand for those.

```python
import uuid

import pytest

from orchestrator.errors import DomainError
from orchestrator.persistence.models import UnitTrackerBinding
from orchestrator.services.tracker_bindings import (
    list_tracker_bindings,
    upsert_tracker_binding,
)


def test_system_upsert_creates_then_updates_one_row_and_persists(session):
    unit = make_work_unit(session)
    upsert_tracker_binding(
        session,
        actor=system_actor(),
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )
    session.expire_all()
    row = session.get(UnitTrackerBinding, unit.id)
    assert row.external_item_id == "task-1"
    assert row.projected_state == "ready"

    upsert_tracker_binding(
        session,
        actor=system_actor(),
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url="https://todoist/app/task/task-1",
        projected_state="completed",
    )
    session.expire_all()
    rows = list_tracker_bindings(session)
    assert len(rows) == 1
    assert rows[0].projected_state == "completed"
    assert rows[0].external_url == "https://todoist/app/task/task-1"


def test_non_system_actor_is_forbidden(session):
    unit = make_work_unit(session)
    with pytest.raises(DomainError) as exc:
        upsert_tracker_binding(
            session,
            actor=worker_actor(),
            work_unit_id=unit.id,
            tracker_system="todoist",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert exc.value.code == "role_forbidden"


def test_unsupported_tracker_system_raises_domain_error_not_integrity(session):
    unit = make_work_unit(session)
    with pytest.raises(DomainError) as exc:
        upsert_tracker_binding(
            session,
            actor=system_actor(),
            work_unit_id=unit.id,
            tracker_system="jira",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert exc.value.code == "tracker_system_unsupported"


def test_empty_item_id_raises_domain_error(session):
    unit = make_work_unit(session)
    with pytest.raises(DomainError) as exc:
        upsert_tracker_binding(
            session,
            actor=system_actor(),
            work_unit_id=unit.id,
            tracker_system="todoist",
            external_item_id="",
            external_url=None,
            projected_state="ready",
        )
    assert exc.value.code == "tracker_item_id_required"


def test_missing_work_unit_raises_not_found(session):
    with pytest.raises(DomainError) as exc:
        upsert_tracker_binding(
            session,
            actor=system_actor(),
            work_unit_id=uuid.uuid4(),
            tracker_system="todoist",
            external_item_id="task-1",
            external_url=None,
            projected_state="ready",
        )
    assert exc.value.code == "work_unit_not_found"


def test_upsert_does_not_change_unit_state(session):
    unit = make_work_unit(session)  # helper leaves it in a known state, e.g. "draft" or "ready"
    before = unit.state
    upsert_tracker_binding(
        session,
        actor=system_actor(),
        work_unit_id=unit.id,
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
    )
    session.expire_all()
    from orchestrator.persistence.models import WorkUnit

    assert session.get(WorkUnit, unit.id).state == before
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/services/test_tracker_bindings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.services.tracker_bindings'`.

- [ ] **Step 3: Implement the service** (`src/orchestrator/services/tracker_bindings.py`)

Copy the `TransactionClock` import line verbatim from `src/orchestrator/services/pr_bindings.py` (do not guess its path).

```python
"""Canonical unit -> external tracker-item bindings (projection only).

Writing a binding records only THAT a unit is mirrored onto an external tracker item. It is
never a lifecycle action: it does not transition the unit and carries no authority. Only the
SYSTEM actor (the projection adapter, or an operator repair) may write.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import TRACKER_SYSTEMS, UnitTrackerBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import TransactionClock  # if re-exported there; else copy the real import


def _authorize_write(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the system actor may write a tracker binding",
            None,
        )


def _validate(tracker_system: str, external_item_id: str) -> None:
    if tracker_system not in TRACKER_SYSTEMS:
        raise DomainError(
            "tracker_system_unsupported",
            f"tracker_system must be one of {TRACKER_SYSTEMS!r}",
            None,
        )
    if not external_item_id:
        raise DomainError(
            "tracker_item_id_required",
            "external_item_id must be non-empty",
            None,
        )


def _require_unit(session: Session, work_unit_id: uuid.UUID) -> WorkUnit:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def upsert_tracker_binding(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    tracker_system: str,
    external_item_id: str,
    external_url: str | None,
    projected_state: str,
) -> UnitTrackerBinding:
    _authorize_write(actor)
    _validate(tracker_system, external_item_id)
    _require_unit(session, work_unit_id)
    now = TransactionClock().now(session)
    binding = session.get(UnitTrackerBinding, work_unit_id)
    if binding is None:
        binding = UnitTrackerBinding(
            work_unit_id=work_unit_id,
            tracker_system=tracker_system,
            external_item_id=external_item_id,
            external_url=external_url,
            projected_state=projected_state,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.tracker_system = tracker_system
        binding.external_item_id = external_item_id
        binding.external_url = external_url
        binding.projected_state = projected_state
        binding.updated_at = now
    session.commit()
    return binding


def get_tracker_binding(session: Session, work_unit_id: uuid.UUID) -> UnitTrackerBinding | None:
    return session.get(UnitTrackerBinding, work_unit_id)


def list_tracker_bindings(
    session: Session, *, tracker_system: str | None = None
) -> list[UnitTrackerBinding]:
    stmt = select(UnitTrackerBinding)
    if tracker_system is not None:
        stmt = stmt.where(UnitTrackerBinding.tracker_system == tracker_system)
    return list(session.scalars(stmt))
```

Note on the `TransactionClock` import: if it is not importable via `pr_bindings`, open `services/pr_bindings.py`, copy its actual `TransactionClock` import line, and use that here.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/services/test_tracker_bindings.py -v`
Expected: all PASS.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/orchestrator/services/tracker_bindings.py tests/services/test_tracker_bindings.py
git add src/orchestrator/services/tracker_bindings.py tests/services/test_tracker_bindings.py
git commit -m "feat(wsp27): tracker-binding service (SYSTEM-only upsert/get/list, fail-closed validation)"
```

---

### Task 3: API routes + schemas + route-inventory

**Files:**
- Modify: `src/orchestrator/api/schemas.py`, `src/orchestrator/api/routes.py`
- Modify: `tests/architecture/test_scope_guards.py` (both inventory literals)
- Test: `tests/api/test_tracker_bindings_api.py`

**Interfaces:**
- Consumes: `upsert_tracker_binding`, `list_tracker_bindings` (Task 2); `CommandBase`, `ActorDep`, `SessionDep`, `_require_zero_expected_version`, `_raise_error`.
- Produces: `POST /api/v1/work-units/{unit_id}/tracker-binding` → `TrackerBindingResponse`; `GET /api/v1/tracker-bindings` → `list[TrackerBindingResponse]`.

- [ ] **Step 1: Write the failing API tests** (`tests/api/test_tracker_bindings_api.py`)

Reuse the existing API test fixtures — `grep -rn "pr-binding" tests/api/` to find how the test client and SYSTEM/WORKER auth headers are built. Below, `client` is the app TestClient, `system_headers` / `worker_headers` are the two-header M2M auth dicts, and `make_work_unit_via_api`/`make_work_unit(session)` create a unit.

```python
def test_system_can_upsert_and_anyone_authed_can_list(client, session, system_headers):
    unit = make_work_unit(session)
    resp = client.post(
        f"/api/v1/work-units/{unit.id}/tracker-binding",
        headers=system_headers,
        json={
            "tracker_system": "todoist",
            "external_item_id": "task-1",
            "external_url": None,
            "projected_state": "ready",
            "idempotency_key": "k1",
            "expected_version": 0,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["external_item_id"] == "task-1"

    listing = client.get("/api/v1/tracker-bindings", headers=system_headers)
    assert listing.status_code == 200
    assert any(r["work_unit_id"] == str(unit.id) for r in listing.json())


def test_unauthenticated_post_is_401(client, session):
    unit = make_work_unit(session)
    resp = client.post(
        f"/api/v1/work-units/{unit.id}/tracker-binding",
        json={
            "tracker_system": "todoist",
            "external_item_id": "task-1",
            "external_url": None,
            "projected_state": "ready",
            "idempotency_key": "k1",
            "expected_version": 0,
        },
    )
    assert resp.status_code == 401


def test_worker_post_is_403(client, session, worker_headers):
    unit = make_work_unit(session)
    resp = client.post(
        f"/api/v1/work-units/{unit.id}/tracker-binding",
        headers=worker_headers,
        json={
            "tracker_system": "todoist",
            "external_item_id": "task-1",
            "external_url": None,
            "projected_state": "ready",
            "idempotency_key": "k1",
            "expected_version": 0,
        },
    )
    assert resp.status_code == 403


def test_nonzero_expected_version_is_409(client, session, system_headers):
    unit = make_work_unit(session)
    resp = client.post(
        f"/api/v1/work-units/{unit.id}/tracker-binding",
        headers=system_headers,
        json={
            "tracker_system": "todoist",
            "external_item_id": "task-1",
            "external_url": None,
            "projected_state": "ready",
            "idempotency_key": "k1",
            "expected_version": 3,
        },
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/api/test_tracker_bindings_api.py -v`
Expected: FAIL — 404 (route not registered) / import errors.

- [ ] **Step 3: Add schemas** (`src/orchestrator/api/schemas.py`, near `PrBindingCommand` ~972)

```python
class TrackerBindingCommand(CommandBase):
    tracker_system: str
    external_item_id: str = Field(min_length=1)
    external_url: str | None = None
    projected_state: str = Field(min_length=1)


class TrackerBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    tracker_system: str
    external_item_id: str
    external_url: str | None
    projected_state: str
    updated_at: datetime
```

- [ ] **Step 4: Add routes** (`src/orchestrator/api/routes.py`)

Add imports (with the other service/schema imports):

```python
from orchestrator.services.tracker_bindings import list_tracker_bindings, upsert_tracker_binding
from orchestrator.api.schemas import TrackerBindingCommand, TrackerBindingResponse  # match how schemas are imported in this file
```

Add the routes (near the `pr-binding` route ~859):

```python
@router.post("/work-units/{unit_id}/tracker-binding", response_model=TrackerBindingResponse)
def tracker_binding(
    unit_id: UUID,
    body: TrackerBindingCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Record the tracker item a work unit is projected onto. Projection only, SYSTEM-written.

    Deliberately written by our own side of the ledger: the tracker is never canonical, so a
    binding never derives from tracker content and never changes the unit's state.
    """
    _require_zero_expected_version(body.expected_version, "tracker binding")
    return _raise_error(
        upsert_tracker_binding(
            session,
            actor=actor,
            work_unit_id=unit_id,
            tracker_system=body.tracker_system,
            external_item_id=body.external_item_id,
            external_url=body.external_url,
            projected_state=body.projected_state,
        )
    )


@router.get("/tracker-bindings", response_model=list[TrackerBindingResponse])
def tracker_bindings_route(
    _actor: ActorDep,
    session: SessionDep,
    tracker_system: str | None = None,
) -> object:
    return list_tracker_bindings(session, tracker_system=tracker_system)
```

- [ ] **Step 5: Run API tests to verify pass**

Run: `.venv/bin/pytest tests/api/test_tracker_bindings_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Update the route-inventory literals**

Run the inventory tests to see the exact failure:
Run: `.venv/bin/pytest tests/architecture/test_scope_guards.py -v`
Expected: FAIL — `test_production_post_route_inventory_is_explicit` and `test_production_get_route_inventory_is_explicit` show the missing paths.

Add `"/api/v1/work-units/{unit_id}/tracker-binding"` to the POST set literal and `"/api/v1/tracker-bindings"` to the GET set literal in `tests/architecture/test_scope_guards.py`. Then:
Run: `.venv/bin/pytest tests/architecture/test_scope_guards.py tests/api/test_lifecycle_api.py -v`
Expected: PASS (inventory + JSON-schema + idempotency invariants all green; no `NON_JSON_SUCCESS_PATHS` entry needed — both routes return JSON).

- [ ] **Step 7: Format + commit**

```bash
.venv/bin/ruff format src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/architecture/test_scope_guards.py tests/api/test_tracker_bindings_api.py
git add src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/architecture/test_scope_guards.py tests/api/test_tracker_bindings_api.py
git commit -m "feat(wsp27): tracker-binding API (SYSTEM upsert + auth-only list) + route inventory"
```

---

### Task 4: Adapter HTTP client + package scaffold + isolation test

**Files:**
- Create: `src/tracker_projection_adapter/__init__.py`, `src/tracker_projection_adapter/orchestrator_client.py`
- Create: `tests/architecture/test_tracker_projection_adapter_isolation.py`, `tests/tracker_projection_adapter/__init__.py`, `tests/tracker_projection_adapter/test_orchestrator_client.py`

**Interfaces:**
- Produces: `OrchestratorClient(*, base_url, credential_key_id, token, transport=None)` with `.status_ledger() -> list[dict]`, `.tracker_bindings() -> list[dict]`, `.upsert_tracker_binding(*, work_unit_id, tracker_system, external_item_id, external_url, projected_state, idempotency_key) -> dict`; module constants `STATUS_LEDGER_ENDPOINT`, `TRACKER_BINDINGS_ENDPOINT`, `ALLOWED_WRITE_PATTERN`; exceptions `ProjectionError`, `ForbiddenEndpointError`.

- [ ] **Step 1: Write the failing tests** (`tests/tracker_projection_adapter/test_orchestrator_client.py`)

```python
import httpx
import pytest

from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
)


def _client(seen):
    def handler(request):
        seen.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/api/v1/status-ledger":
            return httpx.Response(200, json=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}])
        if request.url.path == "/api/v1/tracker-bindings":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/tracker-binding"):
            return httpx.Response(200, json={"work_unit_id": "u1"})
        return httpx.Response(404)

    return OrchestratorClient(
        base_url="https://sds.invalid",
        credential_key_id="orchestrator-system",
        token="fixture-token",
        transport=httpx.MockTransport(handler),
    )


def test_status_ledger_requests_include_inactive():
    seen = []
    rows = _client(seen).status_ledger()
    assert rows[0]["unit_key"] == "K-1"
    assert any("include_inactive=true" in s for s in seen)


def test_upsert_hits_the_allowed_write_path():
    seen = []
    _client(seen).upsert_tracker_binding(
        work_unit_id="123e4567-e89b-12d3-a456-426614174000",
        tracker_system="todoist",
        external_item_id="task-1",
        external_url=None,
        projected_state="ready",
        idempotency_key="k1",
    )
    assert any("POST /api/v1/work-units/123e4567-e89b-12d3-a456-426614174000/tracker-binding" in s for s in seen)


def test_write_to_a_transition_path_is_forbidden():
    client = _client([])
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/123e4567-e89b-12d3-a456-426614174000/commands/ready", {})
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/observations", {})
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_orchestrator_client.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the package + client**

`src/tracker_projection_adapter/__init__.py`:

```python
"""Out-of-process, outbound-only tracker projection adapter.

Mirrors canonical orchestrator work-unit state onto an external tracker. The orchestrator is
always canonical; this package only reads canonical state and writes a unit's tracker-item
binding back. It imports nothing from the orchestrator and calls no lifecycle surface.
"""
```

`src/tracker_projection_adapter/orchestrator_client.py`:

```python
"""The adapter's HTTP client for the orchestrator. The write surface is enforced HERE, in code.

The adapter may READ canonical state and WRITE exactly one thing: a unit's tracker-item
binding. Every other path -- commands, evidence, adjudications, observations, release
artifacts -- is structurally unreachable. The tracker is projection, never canonical.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

STATUS_LEDGER_ENDPOINT = "/api/v1/status-ledger"
TRACKER_BINDINGS_ENDPOINT = "/api/v1/tracker-bindings"
# The ONLY write the adapter may make. Concrete: /api/v1/work-units/<uuid>/tracker-binding.
ALLOWED_WRITE_PATTERN = re.compile(r"^/api/v1/work-units/[0-9a-fA-F-]{36}/tracker-binding$")


class ProjectionError(RuntimeError):
    pass


class ForbiddenEndpointError(ProjectionError):
    """The adapter attempted a write outside its projection-only surface."""


class OrchestratorClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
        )

    def status_ledger(self) -> list[dict[str, Any]]:
        return self._request(
            "GET", STATUS_LEDGER_ENDPOINT, params={"include_inactive": "true"}
        ).json()

    def tracker_bindings(self) -> list[dict[str, Any]]:
        return self._request("GET", TRACKER_BINDINGS_ENDPOINT).json()

    def upsert_tracker_binding(
        self,
        *,
        work_unit_id: str,
        tracker_system: str,
        external_item_id: str,
        external_url: str | None,
        projected_state: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        path = f"/api/v1/work-units/{work_unit_id}/tracker-binding"
        return self.post(
            path,
            {
                "tracker_system": tracker_system,
                "external_item_id": external_item_id,
                "external_url": external_url,
                "projected_state": projected_state,
                "idempotency_key": idempotency_key,
                "expected_version": 0,
            },
        )

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not ALLOWED_WRITE_PATTERN.match(path):
            raise ForbiddenEndpointError(f"the adapter may not write to {path}")
        return self._request("POST", path, json=payload).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method not in {"GET", "POST"}:
            raise ForbiddenEndpointError(f"the adapter may not use {method}")
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ProjectionError(f"orchestrator rejected {method} {path}: {response.status_code}")
        return response
```

- [ ] **Step 4: Create the isolation test** (`tests/architecture/test_tracker_projection_adapter_isolation.py`)

```python
"""The adapter shares no import path with the orchestrator and calls no canonical surface."""

import ast
from pathlib import Path

from tracker_projection_adapter.orchestrator_client import ALLOWED_WRITE_PATTERN

ADAPTER = Path("src/tracker_projection_adapter")
ORCHESTRATOR = Path("src/orchestrator")
ALLOWED_TOP_LEVEL = {
    "httpx",
    "typer",
    "tracker_projection_adapter",
    "json",
    "os",
    "re",
    "dataclasses",
    "datetime",
    "typing",
    "__future__",
}


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
    return names


def test_adapter_imports_nothing_from_the_orchestrator() -> None:
    offenders = {n for n in _imports(ADAPTER) if n.split(".")[0] == "orchestrator"}
    assert offenders == set()


def test_orchestrator_imports_nothing_from_the_adapter() -> None:
    offenders = {
        n for n in _imports(ORCHESTRATOR) if n.split(".")[0] == "tracker_projection_adapter"
    }
    assert offenders == set()


def test_adapter_third_party_deps_are_confined() -> None:
    offenders = {n.split(".")[0] for n in _imports(ADAPTER)} - ALLOWED_TOP_LEVEL
    assert offenders == set()


def test_write_pattern_matches_only_tracker_binding() -> None:
    uid = "123e4567-e89b-12d3-a456-426614174000"
    assert ALLOWED_WRITE_PATTERN.match(f"/api/v1/work-units/{uid}/tracker-binding")
    for forbidden in (
        f"/api/v1/work-units/{uid}/commands/ready",
        f"/api/v1/work-units/{uid}/evidence",
        "/api/v1/observations",
        f"/api/v1/work-units/{uid}/adjudications",
    ):
        assert not ALLOWED_WRITE_PATTERN.match(forbidden)
```

Also create empty `tests/tracker_projection_adapter/__init__.py`.

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_orchestrator_client.py tests/architecture/test_tracker_projection_adapter_isolation.py -v`
Expected: all PASS.

- [ ] **Step 6: Format + commit**

```bash
.venv/bin/ruff format src/tracker_projection_adapter tests/tracker_projection_adapter tests/architecture/test_tracker_projection_adapter_isolation.py
git add src/tracker_projection_adapter tests/tracker_projection_adapter tests/architecture/test_tracker_projection_adapter_isolation.py
git commit -m "feat(wsp27): adapter orchestrator client + package + isolation guard"
```

---

### Task 5: Projection planning logic + vocabulary-sync guard

**Files:**
- Create: `src/tracker_projection_adapter/projection.py`
- Test: `tests/tracker_projection_adapter/test_projection.py`, `tests/tracker_projection_adapter/test_vocabulary_sync.py`

**Interfaces:**
- Produces: dataclasses `UnitView(work_unit_id, unit_key, unit_title, unit_state)`, `BindingView(work_unit_id, tracker_system, external_item_id, external_url, projected_state)`, `Action(kind, unit, binding)`; `TERMINAL_STATES: frozenset[str]`; `unit_view(row: dict) -> UnitView`; `binding_view(row: dict) -> BindingView`; `plan_actions(units: list[UnitView], bindings: list[BindingView]) -> list[Action]`.

- [ ] **Step 1: Write failing tests** (`tests/tracker_projection_adapter/test_projection.py`)

```python
from tracker_projection_adapter.projection import (
    BindingView,
    UnitView,
    plan_actions,
)


def _unit(uid, state):
    return UnitView(work_unit_id=uid, unit_key=f"K-{uid}", unit_title="t", unit_state=state)


def _binding(uid, projected):
    return BindingView(
        work_unit_id=uid,
        tracker_system="todoist",
        external_item_id=f"task-{uid}",
        external_url=None,
        projected_state=projected,
    )


def test_new_active_unit_is_created():
    actions = plan_actions([_unit("u1", "ready")], [])
    assert [(a.kind, a.unit.work_unit_id) for a in actions] == [("create", "u1")]


def test_new_terminal_unit_without_binding_is_skipped():
    actions = plan_actions([_unit("u1", "completed")], [])
    assert actions[0].kind == "skip"


def test_changed_active_unit_is_updated():
    actions = plan_actions([_unit("u1", "executing")], [_binding("u1", "ready")])
    assert actions[0].kind == "update"


def test_unchanged_unit_is_skipped():
    actions = plan_actions([_unit("u1", "ready")], [_binding("u1", "ready")])
    assert actions[0].kind == "skip"


def test_terminal_unit_with_stale_binding_is_completed():
    actions = plan_actions([_unit("u1", "completed")], [_binding("u1", "executing")])
    assert actions[0].kind == "complete"


def test_already_completed_binding_is_skipped():
    actions = plan_actions([_unit("u1", "completed")], [_binding("u1", "completed")])
    assert actions[0].kind == "skip"
```

`tests/tracker_projection_adapter/test_vocabulary_sync.py`:

```python
from orchestrator.kernel.states import WorkUnitState

from tracker_projection_adapter.projection import TERMINAL_STATES


def test_terminal_states_are_valid_lifecycle_states():
    valid = {s.value for s in WorkUnitState}
    assert TERMINAL_STATES <= valid


def test_terminal_states_include_the_true_sinks():
    assert WorkUnitState.COMPLETED.value in TERMINAL_STATES
    assert WorkUnitState.CANCELLED.value in TERMINAL_STATES
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_projection.py tests/tracker_projection_adapter/test_vocabulary_sync.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** (`src/tracker_projection_adapter/projection.py`)

```python
"""Pure projection planning: given canonical units + existing bindings, decide per-unit actions.

No I/O. The orchestrator is canonical; this only computes what the tracker should reflect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Frozen copy of the orchestrator lifecycle states at which a tracker item is closed.
# The kernel exposes no named terminal set; COMPLETED and CANCELLED are the true sinks
# (FAILED can return to READY, so it is intentionally NOT terminal here).
# Guarded against drift by tests/tracker_projection_adapter/test_vocabulary_sync.py.
TERMINAL_STATES = frozenset({"completed", "cancelled"})


@dataclass(frozen=True)
class UnitView:
    work_unit_id: str
    unit_key: str
    unit_title: str
    unit_state: str


@dataclass(frozen=True)
class BindingView:
    work_unit_id: str
    tracker_system: str
    external_item_id: str
    external_url: str | None
    projected_state: str


@dataclass(frozen=True)
class Action:
    kind: str  # "create" | "update" | "complete" | "skip"
    unit: UnitView
    binding: BindingView | None


def unit_view(row: dict[str, Any]) -> UnitView:
    return UnitView(
        work_unit_id=str(row["unit_id"]),
        unit_key=row["unit_key"],
        unit_title=row["unit_title"],
        unit_state=row["unit_state"],
    )


def binding_view(row: dict[str, Any]) -> BindingView:
    return BindingView(
        work_unit_id=str(row["work_unit_id"]),
        tracker_system=row["tracker_system"],
        external_item_id=row["external_item_id"],
        external_url=row.get("external_url"),
        projected_state=row["projected_state"],
    )


def plan_actions(units: list[UnitView], bindings: list[BindingView]) -> list[Action]:
    by_unit = {b.work_unit_id: b for b in bindings}
    actions: list[Action] = []
    for unit in units:
        binding = by_unit.get(unit.work_unit_id)
        terminal = unit.unit_state in TERMINAL_STATES
        if binding is None:
            actions.append(Action("skip" if terminal else "create", unit, None))
        elif binding.projected_state == unit.unit_state:
            actions.append(Action("skip", unit, binding))
        elif terminal:
            actions.append(Action("complete", unit, binding))
        else:
            actions.append(Action("update", unit, binding))
    return actions
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_projection.py tests/tracker_projection_adapter/test_vocabulary_sync.py -v`
Expected: all PASS.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/tracker_projection_adapter/projection.py tests/tracker_projection_adapter/test_projection.py tests/tracker_projection_adapter/test_vocabulary_sync.py
git add src/tracker_projection_adapter/projection.py tests/tracker_projection_adapter/test_projection.py tests/tracker_projection_adapter/test_vocabulary_sync.py
git commit -m "feat(wsp27): pure projection planning + terminal-state vocabulary guard"
```

---

### Task 6: Tracker seam + Todoist projector

**Files:**
- Create: `src/tracker_projection_adapter/tracker.py`
- Test: `tests/tracker_projection_adapter/test_tracker.py`

**Interfaces:**
- Consumes: `UnitView` (Task 5).
- Produces: `ItemRef(external_item_id, external_url)`; `TrackerProjector` protocol (`create_item(unit) -> ItemRef`, `update_item(item_ref, unit) -> ItemRef`, `complete_item(item_ref) -> None`); `TodoistProjector(*, token, project_id, review_base_url, transport=None)`.

- [ ] **Step 1: Write failing tests** (`tests/tracker_projection_adapter/test_tracker.py`)

```python
import httpx

from tracker_projection_adapter.projection import UnitView
from tracker_projection_adapter.tracker import ItemRef, TodoistProjector


def _projector(seen):
    def handler(request):
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/rest/v2/tasks":
            return httpx.Response(200, json={"id": "9", "url": "https://todoist/app/task/9"})
        if request.url.path.endswith("/close"):
            return httpx.Response(204)
        if request.url.path.startswith("/rest/v2/tasks/"):
            return httpx.Response(200, json={"id": "9", "url": "https://todoist/app/task/9"})
        return httpx.Response(404)

    return TodoistProjector(
        token="tok",
        project_id="proj-1",
        review_base_url="https://sds.alobar.net",
        transport=httpx.MockTransport(handler),
    )


def test_create_item_posts_a_task_and_returns_ref():
    seen = []
    ref = _projector(seen).create_item(UnitView("u1", "K-1", "Title", "ready"))
    assert ref.external_item_id == "9"
    assert "POST /rest/v2/tasks" in seen


def test_complete_item_closes_the_task():
    seen = []
    _projector(seen).complete_item(ItemRef("9", None))
    assert "POST /rest/v2/tasks/9/close" in seen
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_tracker.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** (`src/tracker_projection_adapter/tracker.py`)

```python
"""The tracker-agnostic projection seam + the Todoist implementation.

TrackerProjector is the interface. TodoistProjector is the first concrete tracker; a Linear
implementation would be a second class behind the same protocol, with zero orchestrator change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from tracker_projection_adapter.projection import UnitView

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"


@dataclass(frozen=True)
class ItemRef:
    external_item_id: str
    external_url: str | None


class TrackerProjector(Protocol):
    def create_item(self, unit: UnitView) -> ItemRef: ...
    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef: ...
    def complete_item(self, item_ref: ItemRef) -> None: ...


class TodoistProjector:
    def __init__(
        self,
        *,
        token: str,
        project_id: str,
        review_base_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._project_id = project_id
        self._review_base_url = review_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=TODOIST_API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            transport=transport,
        )

    def _content(self, unit: UnitView) -> str:
        return f"[{unit.unit_key}] {unit.unit_title}"

    def _description(self, unit: UnitView) -> str:
        return f"{self._review_base_url}/review/units/{unit.work_unit_id}"

    def create_item(self, unit: UnitView) -> ItemRef:
        data = self._post(
            "/tasks",
            {
                "content": self._content(unit),
                "description": self._description(unit),
                "project_id": self._project_id,
                "labels": [f"sds:{unit.unit_state}"],
            },
        )
        return ItemRef(external_item_id=str(data["id"]), external_url=data.get("url"))

    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef:
        data = self._post(
            f"/tasks/{item_ref.external_item_id}",
            {
                "content": self._content(unit),
                "description": self._description(unit),
                "labels": [f"sds:{unit.unit_state}"],
            },
        )
        url = data.get("url") if isinstance(data, dict) else None
        return ItemRef(
            external_item_id=item_ref.external_item_id,
            external_url=url or item_ref.external_url,
        )

    def complete_item(self, item_ref: ItemRef) -> None:
        self._post(f"/tasks/{item_ref.external_item_id}/close", None)

    def _post(self, path: str, payload: dict[str, Any] | None) -> Any:
        response = (
            self._client.post(path, json=payload)
            if payload is not None
            else self._client.post(path)
        )
        if response.status_code >= 400:
            raise RuntimeError(f"todoist rejected POST {path}: {response.status_code}")
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_tracker.py -v`
Expected: all PASS.

- [ ] **Step 5: Format + commit**

```bash
.venv/bin/ruff format src/tracker_projection_adapter/tracker.py tests/tracker_projection_adapter/test_tracker.py
git add src/tracker_projection_adapter/tracker.py tests/tracker_projection_adapter/test_tracker.py
git commit -m "feat(wsp27): tracker-agnostic seam + Todoist REST projector"
```

---

### Task 7: CLI wiring + console script

**Files:**
- Create: `src/tracker_projection_adapter/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/tracker_projection_adapter/test_cli.py`

**Interfaces:**
- Consumes: `OrchestratorClient` (Task 4), `plan_actions`/`unit_view`/`binding_view` (Task 5), `TodoistProjector`/`ItemRef`/`TrackerProjector` (Task 6).
- Produces: `project(client, projector, *, dry_run: bool) -> dict[str, int]`; `typer` app with `project` command; console script `tracker-projection-adapter`.

- [ ] **Step 1: Write failing tests** (`tests/tracker_projection_adapter/test_cli.py`)

```python
from tracker_projection_adapter.cli import project
from tracker_projection_adapter.projection import UnitView
from tracker_projection_adapter.tracker import ItemRef


class FakeClient:
    def __init__(self, units, bindings):
        self._units = units
        self._bindings = bindings
        self.upserts = []

    def status_ledger(self):
        return self._units

    def tracker_bindings(self):
        return self._bindings

    def upsert_tracker_binding(self, **kwargs):
        self.upserts.append(kwargs)
        return {}


class FakeProjector:
    def __init__(self):
        self.calls = []

    def create_item(self, unit):
        self.calls.append(("create", unit.work_unit_id))
        return ItemRef("task-new", "https://todoist/app/task/task-new")

    def update_item(self, item_ref, unit):
        self.calls.append(("update", unit.work_unit_id))
        return ItemRef(item_ref.external_item_id, item_ref.external_url)

    def complete_item(self, item_ref):
        self.calls.append(("complete", item_ref.external_item_id))


def test_dry_run_makes_no_writes():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}],
        bindings=[],
    )
    projector = FakeProjector()
    counts = project(client, projector, dry_run=True)
    assert counts["create"] == 1
    assert projector.calls == []
    assert client.upserts == []


def test_create_flow_projects_then_writes_binding():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "ready"}],
        bindings=[],
    )
    projector = FakeProjector()
    project(client, projector, dry_run=False)
    assert ("create", "u1") in projector.calls
    assert client.upserts[0]["external_item_id"] == "task-new"
    assert client.upserts[0]["projected_state"] == "ready"


def test_complete_flow_closes_task_and_writes_binding():
    client = FakeClient(
        units=[{"unit_id": "u1", "unit_key": "K-1", "unit_title": "t", "unit_state": "completed"}],
        bindings=[{
            "work_unit_id": "u1", "tracker_system": "todoist",
            "external_item_id": "task-9", "external_url": None, "projected_state": "executing",
        }],
    )
    projector = FakeProjector()
    project(client, projector, dry_run=False)
    assert ("complete", "task-9") in projector.calls
    assert client.upserts[0]["projected_state"] == "completed"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_cli.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** (`src/tracker_projection_adapter/cli.py`)

```python
"""The adapter entry point. Operator-invoked; there is no scheduler and no loop."""

from __future__ import annotations

import json
import os
from typing import Annotated

import typer

from tracker_projection_adapter.orchestrator_client import OrchestratorClient
from tracker_projection_adapter.projection import (
    Action,
    UnitView,
    binding_view,
    plan_actions,
    unit_view,
)
from tracker_projection_adapter.tracker import ItemRef, TodoistProjector, TrackerProjector

app = typer.Typer(no_args_is_help=True)


class _NullProjector:
    """A dry-run projector: any use is a bug, so it fails loudly.

    Fully annotated (implements the TrackerProjector protocol) so no type suppression is needed.
    """

    def create_item(self, unit: UnitView) -> ItemRef:
        raise AssertionError("dry run must not create tracker items")

    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef:
        raise AssertionError("dry run must not update tracker items")

    def complete_item(self, item_ref: ItemRef) -> None:
        raise AssertionError("dry run must not complete tracker items")


def _apply(
    client: OrchestratorClient,
    projector: TrackerProjector,
    action: Action,
    binding_by_unit: dict,
) -> None:
    unit = action.unit
    if action.kind == "create":
        ref = projector.create_item(unit)
    elif action.kind == "update":
        existing = binding_by_unit[unit.work_unit_id]
        ref = projector.update_item(ItemRef(existing.external_item_id, existing.external_url), unit)
    elif action.kind == "complete":
        existing = binding_by_unit[unit.work_unit_id]
        ref = ItemRef(existing.external_item_id, existing.external_url)
        projector.complete_item(ref)
    else:
        return
    client.upsert_tracker_binding(
        work_unit_id=unit.work_unit_id,
        tracker_system="todoist",
        external_item_id=ref.external_item_id,
        external_url=ref.external_url,
        projected_state=unit.unit_state,
        idempotency_key=f"tracker-binding:{unit.work_unit_id}:{unit.unit_state}",
    )


def project(
    client: OrchestratorClient,
    projector: TrackerProjector,
    *,
    dry_run: bool,
) -> dict[str, int]:
    units = [unit_view(row) for row in client.status_ledger()]
    bindings = [binding_view(row) for row in client.tracker_bindings()]
    binding_by_unit = {b.work_unit_id: b for b in bindings}
    counts = {"create": 0, "update": 0, "complete": 0, "skip": 0}
    for action in plan_actions(units, bindings):
        counts[action.kind] += 1
        if dry_run or action.kind == "skip":
            continue
        _apply(client, projector, action, binding_by_unit)
    return counts


@app.command("project")
def project_command(
    todoist_project_id: Annotated[str, typer.Option(help="Target Todoist project id.")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    review_base_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    dry_run: Annotated[bool, typer.Option(help="Print the plan; make no writes.")] = False,
) -> None:
    token = os.environ.get("TRACKER_PROJECTION_TOKEN")
    if not token:
        typer.echo("TRACKER_PROJECTION_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    client = OrchestratorClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )
    if dry_run:
        counts = project(client, _NullProjector(), dry_run=True)
    else:
        todoist_token = os.environ.get("TODOIST_API_TOKEN")
        if not todoist_token:
            typer.echo("TODOIST_API_TOKEN is required", err=True)
            raise typer.Exit(code=1)
        projector = TodoistProjector(
            token=todoist_token,
            project_id=todoist_project_id,
            review_base_url=review_base_url,
        )
        counts = project(client, projector, dry_run=False)
    typer.echo(json.dumps(counts, indent=2, sort_keys=True))
```

- [ ] **Step 4: Add the console script** (`pyproject.toml`, `[project.scripts]`)

```toml
[project.scripts]
orchestrator = "orchestrator.cli:app"
reconciliation-runner = "reconciliation_runner.cli:app"
tracker-projection-adapter = "tracker_projection_adapter.cli:app"
```

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/ -v`
Expected: all PASS.

- [ ] **Step 6: Format + commit**

```bash
.venv/bin/ruff format src/tracker_projection_adapter/cli.py tests/tracker_projection_adapter/test_cli.py
git add src/tracker_projection_adapter/cli.py tests/tracker_projection_adapter/test_cli.py pyproject.toml
git commit -m "feat(wsp27): adapter CLI (project command, dry-run, console script)"
```

---

### Task 8: BWS launcher + secret manifest

**Files:**
- Create: `scripts/run-tracker-projection.sh`
- Modify: `.bws-secrets.toml`

**Interfaces:** none (ops).

- [ ] **Step 1: Add the manifest entry** (`.bws-secrets.toml`)

Open `.bws-secrets.toml`, mirror the existing `[[secret]]` block format (copy the shape of an existing entry — do NOT invent fields), and add:

```toml
[[secret]]
uuid = "ff396349-aec1-4250-b2f0-b493015188da"
name = "todoist-api-token-tracker-projection"
project = "orchestrator"   # match the `project` value the other orchestrator secrets use
```

- [ ] **Step 2: Create the launcher** (`scripts/run-tracker-projection.sh`)

This references only UUIDs and env-var NAMES — never a token value (writing a token value would trip the BWS write-guard).

```bash
#!/usr/bin/env bash
set -euo pipefail

# Operator-invoked outbound tracker projection pass. Scheduler is deferred (ADR-0003/ADR-0002).
# Usage: TODOIST_PROJECT_ID=<id> scripts/run-tracker-projection.sh [--dry-run]

SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"
TODOIST_TOKEN_UUID="ff396349-aec1-4250-b2f0-b493015188da"

# Load BWS_ACCESS_TOKEN from the macOS Keychain via the approved helper (never a plaintext file).
# shellcheck disable=SC1090
source "$HOME/Projects/vps-backup/bws-token.sh"

TRACKER_PROJECTION_TOKEN="$(bws secret get "$SYSTEM_BEARER_UUID" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')"
TODOIST_API_TOKEN="$(bws secret get "$TODOIST_TOKEN_UUID" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')"
export TRACKER_PROJECTION_TOKEN TODOIST_API_TOKEN

exec "$(dirname "$0")/../.venv/bin/tracker-projection-adapter" project \
  --todoist-project-id "${TODOIST_PROJECT_ID:?set TODOIST_PROJECT_ID}" \
  "$@"
```

Note: confirm the exact `bws secret get` output shape first (`bws secret get <uuid>` may print JSON with a `value` field, or the raw value). Run `bws secret get "$SYSTEM_BEARER_UUID"` once and adjust the extraction to match — but never echo the value into the transcript; inspect only the JSON key names.

- [ ] **Step 3: Make it executable + shellcheck + verify manifest**

```bash
chmod +x scripts/run-tracker-projection.sh
shellcheck scripts/run-tracker-projection.sh || true
python3 -c "import tomllib; tomllib.load(open('.bws-secrets.toml','rb')); print('manifest OK')"
```
Expected: manifest parses; shellcheck clean (or only style warnings).

- [ ] **Step 4: Commit**

```bash
git add scripts/run-tracker-projection.sh .bws-secrets.toml
git commit -m "ops(wsp27): BWS launcher + Todoist token manifest entry"
```

---

### Task 9: ADR-0003 (projection-only, out-of-process, Todoist-first)

**Files:**
- Create: `docs/decisions/0003-tracker-projection-outbound-only.md`

- [ ] **Step 1: Write the ADR**

Mirror the structure of `docs/decisions/0002-reconciliation-via-report-only-runner.md` (Context / Decision / Alternatives / Cost). Content must record:
- **Context:** WS-P2.7 was to decide Linear vs Todoist on the WS-0.6 pilot learnings note; that note was never produced (verified on disk 2026-07-26). Program exit #9 + the YAGNI ledger forbid any canonical tracker.
- **Decision:** outbound-only projection via a separate report-only process (`src/tracker_projection_adapter/`), mirroring ADR-0002; the unit↔item mapping is stored canonical-side (`unit_tracker_bindings`), never in the tracker; **Todoist chosen first on first principles** (usable today; Linear was never wired and its pilot produced nothing) behind a tracker-agnostic `TrackerProjector` seam; scheduler deferred.
- **Exit-#9 guarantees:** the `todoist` import ban in `src/orchestrator/`, the adapter client's single write-endpoint pattern, the binding-carries-no-authority service test, and the adapter isolation test.
- **D8 interim close (documentary):** the Linear "Agent Queue" pilot is retired as the interim non-canonical surface; the standing model is orchestrator-canonical + Todoist projection. No real pilot items are re-homed (that is WS-P2.13). Devon may archive the Linear project at will.
- **Alternatives considered:** Linear-first (rejected: not wired, no evidence); in-process projection (rejected: breaks the `todoist` import ban + ADR-0002 push-only invariant); tracker-as-canonical (rejected permanently by the YAGNI ledger).
- **Deferred:** the entire inbound flow (requested transitions + reconciliation) to Increment 2.

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0003-tracker-projection-outbound-only.md
git commit -m "docs(wsp27): ADR-0003 tracker projection (outbound-only, Todoist-first)"
```

---

### Task 10: Full-gate verification + whole-branch review

**Files:** none (verification).

- [ ] **Step 1: Run the full check on a clean tree**

```bash
git status   # confirm clean working tree
.venv/bin/ruff format --check .
make check
```
Expected: `ruff format --check .` clean (if it reports PRE-EXISTING format debt in files you never touched, diff against `main` — that is not your regression). `make check` green; **read the collected-test count** (it must be > the pre-change count by the number of tests added; exit 0 alone is not proof — code 5 = no tests collected is swallowed).

- [ ] **Step 2: Confirm the migration is reversible**

```bash
.venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head
```
Expected: downgrade drops `unit_tracker_bindings`, upgrade recreates it, no errors.

- [ ] **Step 3: `/code-review` the whole branch**

Run `/code-review` against the diff vs `main`. Then run a final adversarial whole-branch review on Opus (per the spec — budget for kills; this is projection/binding code where a reviewer earns its keep). Address findings before opening a PR.

- [ ] **Step 4: Write the Wave-2 closeout note**

Create `~/docs/software-delivery-system/2026-07-26-wsp27-inc1-closeout-evidence.md` recording: decisions taken (Todoist-first, outbound-only, no-event, documentary D8 close), what shipped (table/migration/API/adapter/ADR), verification evidence (collected-test count, security scan, `alembic` reversibility), the human prerequisite still open (choose the Todoist project id for a live run), and what remains (inbound Increment 2, WS-P2.8).

---

## Self-Review

**Spec coverage:** §3 architecture → Tasks 1-7; §4 orchestrator additions → Tasks 1-3; §5 adapter → Tasks 4-7; §6 projection semantics → Tasks 5-7; §7 exit-#9 guarantees → import ban (existing) + client pattern (Task 4) + state-unchanged test (Task 2) + isolation test (Task 4) + ADR (Task 9); §8 deploy/secrets → Task 8; §4.6 no-event → Task 2 (service emits nothing, matching PR-binding); §9 documentary D8 close → Task 9 + Task 10 closeout; §10 CI obligations → Task 3 (inventory) + Task 10 (full gate); §11 deferred inbound → recorded in ADR (Task 9), not built. All covered.

**Placeholder scan:** `make_work_unit`/`system_actor`/`worker_actor`/`system_headers`/`worker_headers` are named stand-ins for EXISTING shared test helpers the implementer locates via the given `grep` commands — not new code to invent. The `TransactionClock` import is explicitly "copy the real line from pr_bindings.py". No other placeholders.

**Type consistency:** `UnitView`/`BindingView`/`Action`/`ItemRef` field names and `plan_actions`/`unit_view`/`binding_view`/`project`/`_apply` signatures are consistent across Tasks 4-7. `upsert_tracker_binding` signature matches between service (Task 2), route (Task 3), and client payload (Task 4). `TRACKER_SYSTEMS`/`unit_tracker_bindings`/`external_item_id`/`projected_state` names consistent across model, migration, service, schema, API. `TERMINAL_STATES = {"completed","cancelled"}` matches the kernel `WorkUnitState` values (Task 5 guard).
