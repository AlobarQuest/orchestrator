# WS-3.1 Persistent Orchestrator Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimum persistent, domain-neutral service that owns canonical work-unit lifecycle truth.

**Architecture:** A pure lifecycle kernel defines states, readiness, authority comparison, and lease policy. FastAPI, a thin HTTP CLI, and a Jinja/HTMX UI call application services that persist through SQLAlchemy and PostgreSQL; every mutation and its immutable local event commit atomically.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16, psycopg 3, Typer, HTTPX, Jinja2/HTMX, pytest, Ruff, Pyright, Docker Compose.

## Global Constraints

- Governing intent: `ws-3.1-orchestrator-core` revision 1, hash `4414eae543d9dac8b1983f796593569d9abf97dfee1b8a06ef29b308e7b8337b`.
- Governing design: `docs/superpowers/specs/2026-07-04-ws31-orchestrator-core-design.md` at or after commit `f233498`.
- Python is 3.12; PostgreSQL is 16; SQLite is unsupported.
- The default lease is 15 minutes; workers renew every 5 minutes; the default maximum is 3 attempts.
- Capability restriction order is `prohibited < requires_approval < allowed`; ambiguous authority changes fail closed as expansion.
- Package revisions, evidence, adjudications, and events are database-enforced append-only.
- M2M identities map one-to-one to active Phase-1 agent IDs from a version-pinned registry bundle.
- Workers may submit evidence but may not grant waivers, set `Completed`, approve decomposition, expand authority, or merge.
- No automatic merge, worker dispatch, external event publication, production mutation, or tracker-canonical state.
- Runtime secrets are never tracked. Before touching an env file, BWS integration, or deployment configuration, read `~/Projects/security-standards/docs/build-agent-secrets.md`.
- Every code slice uses TDD, passes its focused tests, passes `make check`, receives coherent-slice review, and commits separately.
- Devon alone reviews and merges the final pull request.

## Planned file map

```text
orchestrator/
├── pyproject.toml                     # package, dependencies, tool configuration
├── uv.lock                            # reproducible Python dependency lock
├── Makefile                           # portfolio quality gate
├── Dockerfile                         # one immutable port-8000 service image
├── docker-compose.yml                 # local PostgreSQL 16 plus application
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       └── 0001_ws31_core.py           # tables, constraints, append-only triggers
├── scripts/
│   └── build_registry_bundle.py       # pinned credential-free actor bundle
├── src/orchestrator/
│   ├── __init__.py
│   ├── config.py                      # runtime settings
│   ├── db.py                          # engine/session/transaction setup
│   ├── errors.py                      # stable domain error contract
│   ├── clock.py                       # injectable and PostgreSQL clocks
│   ├── kernel/
│   │   ├── states.py                  # complete legal graph
│   │   ├── transitions.py             # role and guard decisions
│   │   ├── authority.py               # normalization, lattice, fingerprints
│   │   ├── readiness.py               # ready/blocked/not-authorized decisions
│   │   └── leases.py                  # lease policy values and decisions
│   ├── persistence/
│   │   ├── models.py                  # SQLAlchemy models
│   │   └── repositories.py            # locked aggregate and query operations
│   ├── services/
│   │   ├── packages.py                # manual package/decomposition registration
│   │   ├── lifecycle.py               # transition transaction and events
│   │   ├── claims.py                  # claim/renew/reclaim/retry
│   │   └── evidence.py                # evidence, adjudication, waiver services
│   ├── identity/
│   │   ├── registry.py                # registry adapter and bundle validation
│   │   └── auth.py                    # human and M2M actor contexts
│   ├── api/
│   │   ├── schemas.py                 # request/response contracts
│   │   ├── dependencies.py            # sessions, actor, idempotency
│   │   ├── routes.py                  # lifecycle REST routes
│   │   └── health.py                  # live/ready routes
│   ├── cli.py                         # HTTP-only CLI
│   ├── web.py                         # review UI routes and CSRF
│   ├── main.py                        # FastAPI composition root
│   └── templates/
│       ├── base.html
│       ├── queue.html
│       ├── unit.html
│       └── evidence_pack.html
├── tests/
│   ├── conftest.py                    # PostgreSQL fixtures and actors
│   ├── kernel/
│   ├── persistence/
│   ├── services/
│   ├── api/
│   ├── cli/
│   ├── web/
│   └── architecture/
├── docs/
│   ├── operations/local-development.md
│   ├── operations/migrations.md
│   ├── operations/authentication.md
│   └── evidence/ws-3.1-evidence-index.md
├── PROJECT.md
├── STANDARD_VERSION
└── .github/
    ├── dependabot.yml
    └── workflows/quality.yml
```

---

## Execution preflight

Before Task 1:

1. Invoke `superpowers:using-git-worktrees`.
2. Confirm this documentation-only `main` is clean at the approved spec/plan commits.
3. Create the private empty repository `AlobarQuest/orchestrator`, add it as `origin`, and
   push documentation-only `main`.
4. Create isolated branch/worktree `codex/ws31-orchestrator-core`.
5. Run all implementation tasks inside that worktree.
6. Confirm `~/Projects/intent-packages/packages/ws-3.1-orchestrator-core` still verifies
   approval for hash `4414eae543d9dac8b1983f796593569d9abf97dfee1b8a06ef29b308e7b8337b`.

Do not create or push implementation commits directly on `main`.

---

### Task 1: Establish the repository quality and PostgreSQL test harness

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `Makefile`
- Create: `src/orchestrator/__init__.py`
- Create: `src/orchestrator/config.py`
- Create: `src/orchestrator/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_foundation.py`
- Create: `docker-compose.yml`
- Create: `.gitignore`
- Create: `PROJECT.md`
- Create: `STANDARD_VERSION`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, `session_factory`, and PostgreSQL `db_session`.
- Consumes: local `DATABASE_URL`; the test default is `postgresql+psycopg://postgres:postgres@127.0.0.1:5432/orchestrator_test`.

- [ ] **Step 1: Read the portfolio standards and secrets boundary**

Run:

```bash
sed -n '1,240p' ~/Developer/code-standards/STANDARDS.md
sed -n '1,260p' ~/Projects/security-standards/docs/build-agent-secrets.md
```

Expected: both files are readable; no secret value is copied into the repository.

- [ ] **Step 2: Write the failing foundation test**

```python
# tests/test_foundation.py
from sqlalchemy import text


def test_postgresql_fixture_uses_postgresql(db_session):
    dialect = db_session.bind.dialect.name
    assert dialect == "postgresql"
    assert db_session.scalar(text("select 1")) == 1
```

- [ ] **Step 3: Run the test and verify collection fails**

Run: `uv run pytest tests/test_foundation.py -v`

Expected: FAIL because the project and `db_session` fixture do not exist.

- [ ] **Step 4: Add the minimal project and database setup**

```toml
# pyproject.toml
[project]
name = "orchestrator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "alembic>=1.13",
  "fastapi>=0.110",
  "httpx>=0.27",
  "jinja2>=3.1",
  "psycopg[binary]>=3.1",
  "pydantic>=2.6",
  "pydantic-settings>=2.2",
  "python-multipart>=0.0.9",
  "sqlalchemy>=2.0",
  "typer>=0.12",
  "uvicorn[standard]>=0.29",
]

[dependency-groups]
dev = [
  "pytest>=8.0",
  "pytest-xdist>=3.6",
  "ruff==0.15.20",
  "pyright==1.1.411",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "C90"]

[tool.pyright]
typeCheckingMode = "basic"
pythonVersion = "3.12"
venvPath = "."
venv = ".venv"
```

```python
# src/orchestrator/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_")
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# src/orchestrator/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session_factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
```

```python
# tests/conftest.py
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/orchestrator_test",
)
os.environ.setdefault("ORCHESTRATOR_DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def db_session():
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()
```

Create `docker-compose.yml` with a `postgres:16-alpine` service named
`orchestrator-postgres`, database `orchestrator_test`, health check
`pg_isready -U postgres`, and host port `5432`. The literal local password `postgres` is
development-only and must not appear in deployment configuration.

- [ ] **Step 5: Lock dependencies and run the test**

Run:

```bash
uv lock
docker compose up -d orchestrator-postgres
uv run pytest tests/test_foundation.py -v
```

Expected: PASS; the fixture reports PostgreSQL and returns `1`.

- [ ] **Step 6: Add the portfolio quality entry points**

Vendor the current portfolio `Makefile` and `.github/workflows/quality.yml` from
`change-manager` at merged main, then set `PROJECT.md` to:

```yaml
---
name: orchestrator
tier: active
status: active
purpose: Canonical work-unit lifecycle control plane for the software factory.
version: 0.1.0
version_source: pyproject
updated: '2026-07-04'
foundation: true
foundation_contract: 1
applicable_standards:
  project: '1.0'
  security: '1.0'
  code: '1.0'
  infra: null
required_checks:
- id: quality
  executor: github-actions:quality.yml
---

## Backlog

## Future plans
```

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all checks pass.

- [ ] **Step 7: Review and commit**

Run `/code-review` on the slice, resolve findings, then:

```bash
git add pyproject.toml uv.lock Makefile docker-compose.yml .gitignore PROJECT.md \
  STANDARD_VERSION .github src tests
git commit -m "chore: establish orchestrator foundation"
```

---

### Task 2: Implement the complete lifecycle kernel

**Files:**
- Create: `src/orchestrator/errors.py`
- Create: `src/orchestrator/kernel/states.py`
- Create: `src/orchestrator/kernel/transitions.py`
- Create: `tests/kernel/test_state_graph.py`
- Create: `tests/kernel/test_transition_authority.py`

**Interfaces:**
- Produces: `WorkUnitState`, `ActorRole`, `LEGAL_EDGES`, and `authorize_transition(source, target, role, guards)`.
- Produces: `DomainError(code, message, recovery)`.

- [ ] **Step 1: Write exhaustive failing graph tests**

```python
# tests/kernel/test_state_graph.py
import itertools

import pytest

from orchestrator.kernel.states import LEGAL_EDGES, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition


@pytest.mark.parametrize(("source", "target"), sorted(LEGAL_EDGES))
def test_every_declared_edge_is_legal(source, target):
    authorize_transition(
        source,
        target,
        next(iter(EDGE_ROLES[(source, target)])),
        TransitionGuards(approval_recorded=True, completion_satisfied=True),
    )


INVALID_EDGES = set(itertools.permutations(WorkUnitState, 2)) - LEGAL_EDGES


@pytest.mark.parametrize(("source", "target"), sorted(INVALID_EDGES))
def test_every_undeclared_edge_is_invalid(source, target):
    with pytest.raises(Exception) as exc:
        authorize_transition(source, target, ActorRole.HUMAN, TransitionGuards())
    assert getattr(exc.value, "code") == "invalid_transition"
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest tests/kernel/test_state_graph.py -v`

Expected: FAIL because kernel modules do not exist.

- [ ] **Step 3: Implement the states and exact graph**

```python
# src/orchestrator/kernel/states.py
from enum import StrEnum


class WorkUnitState(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    CLAIMED = "claimed"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    AWAITING_REVIEW = "awaiting_review"
    REVISION_REQUIRED = "revision_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActorRole(StrEnum):
    SYSTEM = "system"
    WORKER = "worker"
    VERIFIER = "verifier"
    HUMAN = "human"


def _edges(source: WorkUnitState, *targets: WorkUnitState):
    return {(source, target) for target in targets}


LEGAL_EDGES = frozenset(
    _edges(WorkUnitState.DRAFT, WorkUnitState.READY)
    | _edges(WorkUnitState.READY, WorkUnitState.CLAIMED)
    | _edges(
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
        WorkUnitState.BLOCKED,
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.FAILED,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.EXECUTING,
        WorkUnitState.SUBMITTED,
        WorkUnitState.BLOCKED,
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.FAILED,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.SUBMITTED,
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    | _edges(
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    | _edges(WorkUnitState.BLOCKED, WorkUnitState.READY)
    | _edges(
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.READY,
        WorkUnitState.CANCELLED,
    )
    | _edges(
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.COMPLETED,
        WorkUnitState.REVISION_REQUIRED,
    )
    | _edges(WorkUnitState.REVISION_REQUIRED, WorkUnitState.READY)
    | _edges(WorkUnitState.FAILED, WorkUnitState.READY, WorkUnitState.CANCELLED)
)
```

- [ ] **Step 4: Implement stable errors and role/guard checks**

`authorize_transition` first rejects edges outside `LEGAL_EDGES`, then enforces an
edge-specific role map:

- System: `Draft → Ready`, `Ready → Claimed`, deterministic return-to-Ready edges, and
  the three expiry-recovery edges.
- Worker: `Claimed → Executing|Blocked|Awaiting Approval|Failed` and
  `Executing → Submitted|Blocked|Awaiting Approval|Failed`.
- Verifier: transitions from `Submitted` or `Verifying`.
- Human: approval/review outcomes, cancellation, retry return-to-Ready, and completion
  when every completion guard passes.

A role is denied unless its exact `(source, target)` edge appears in `EDGE_ROLES`.
Implement:

```python
class DomainError(Exception):
    def __init__(self, code: str, message: str, recovery: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery


@dataclass(frozen=True)
class TransitionGuards:
    approval_recorded: bool = False
    completion_satisfied: bool = False


SYSTEM_EDGES = {
    (WorkUnitState.DRAFT, WorkUnitState.READY),
    (WorkUnitState.READY, WorkUnitState.CLAIMED),
    (WorkUnitState.CLAIMED, WorkUnitState.FAILED),
    (WorkUnitState.EXECUTING, WorkUnitState.FAILED),
    (WorkUnitState.BLOCKED, WorkUnitState.READY),
    (WorkUnitState.REVISION_REQUIRED, WorkUnitState.READY),
    (WorkUnitState.FAILED, WorkUnitState.READY),
}
WORKER_EDGES = {
    (WorkUnitState.CLAIMED, WorkUnitState.EXECUTING),
    (WorkUnitState.CLAIMED, WorkUnitState.BLOCKED),
    (WorkUnitState.CLAIMED, WorkUnitState.AWAITING_APPROVAL),
    (WorkUnitState.CLAIMED, WorkUnitState.FAILED),
    (WorkUnitState.EXECUTING, WorkUnitState.SUBMITTED),
    (WorkUnitState.EXECUTING, WorkUnitState.BLOCKED),
    (WorkUnitState.EXECUTING, WorkUnitState.AWAITING_APPROVAL),
    (WorkUnitState.EXECUTING, WorkUnitState.FAILED),
}
VERIFIER_EDGES = {
    (source, target)
    for source in (WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING)
    for target in (
        WorkUnitState.VERIFYING,
        WorkUnitState.REVISION_REQUIRED,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.FAILED,
        WorkUnitState.COMPLETED,
    )
    if (source, target) in LEGAL_EDGES
}
HUMAN_EDGES = {
    (WorkUnitState.CLAIMED, WorkUnitState.CANCELLED),
    (WorkUnitState.EXECUTING, WorkUnitState.CANCELLED),
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.READY),
    (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.CANCELLED),
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.COMPLETED),
    (WorkUnitState.AWAITING_REVIEW, WorkUnitState.REVISION_REQUIRED),
    (WorkUnitState.FAILED, WorkUnitState.CANCELLED),
    (WorkUnitState.SUBMITTED, WorkUnitState.COMPLETED),
    (WorkUnitState.VERIFYING, WorkUnitState.COMPLETED),
}
EDGE_ROLES = {
    edge: frozenset(
        role
        for role, edges in (
            (ActorRole.SYSTEM, SYSTEM_EDGES),
            (ActorRole.WORKER, WORKER_EDGES),
            (ActorRole.VERIFIER, VERIFIER_EDGES),
            (ActorRole.HUMAN, HUMAN_EDGES),
        )
        if edge in edges
    )
    for edge in LEGAL_EDGES
}
assert all(EDGE_ROLES.values())


def authorize_transition(source, target, role, guards):
    if (source, target) not in LEGAL_EDGES:
        raise DomainError("invalid_transition", f"{source} -> {target} is not legal", None)
    if role not in EDGE_ROLES[(source, target)]:
        raise DomainError("role_forbidden", f"{role} may not perform this transition", None)
    if source is WorkUnitState.AWAITING_APPROVAL and target is WorkUnitState.READY:
        if not guards.approval_recorded:
            raise DomainError("approval_required", "record approval before resuming", "approve")
    if target is WorkUnitState.COMPLETED:
        if role not in {ActorRole.VERIFIER, ActorRole.HUMAN}:
            raise DomainError("role_forbidden", "worker may not complete a unit", None)
        if not guards.completion_satisfied:
            raise DomainError("completion_incomplete", "completion guards failed", "verify")
```

- [ ] **Step 5: Add explicit regression tests and pass the kernel suite**

Test the five named forbidden transitions and assert workers are denied cancellation,
every post-submission transition, all returns to `Ready`, every adjudication/review
edge, and every edge targeting `Completed`.

Run: `uv run pytest tests/kernel -v`

Expected: all graph and authorization tests pass.

- [ ] **Step 6: Run the full gate, review, and commit**

Run: `make check`

Expected: pass.

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/errors.py src/orchestrator/kernel tests/kernel
git commit -m "feat: define canonical work-unit lifecycle"
```

---

### Task 3: Create the persistent schema and enforce append-only records

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_ws31_core.py`
- Create: `src/orchestrator/persistence/models.py`
- Create: `tests/persistence/test_migrations.py`
- Create: `tests/persistence/test_constraints.py`
- Create: `tests/persistence/test_append_only.py`

**Interfaces:**
- Produces SQLAlchemy models: `WorkPackage`, `WorkPackageRevision`, `WorkUnit`, `Dependency`, `Claim`, `Approval`, `Evidence`, `Adjudication`, `Event`.
- Produces an Alembic head that upgrades an empty PostgreSQL database.

- [ ] **Step 1: Write failing migration and append-only tests**

```python
def test_alembic_upgrades_empty_database(database_url):
    run_alembic("upgrade", "head", database_url)
    assert current_revision(database_url) == head_revision()


@pytest.mark.parametrize("table", ["work_package_revisions", "evidence", "adjudications", "events"])
def test_append_only_tables_reject_update_and_delete(db_session, seeded_row, table):
    with pytest.raises(IntegrityError):
        db_session.execute(text(f"update {table} set id = id where id = :id"), {"id": seeded_row.id})
        db_session.commit()
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest tests/persistence/test_migrations.py tests/persistence/test_append_only.py -v`

Expected: FAIL because Alembic, models, and triggers do not exist.

- [ ] **Step 3: Implement typed models and constraints**

Use UUID primary keys, timezone-aware timestamps, JSONB payloads, enum check constraints,
foreign keys, and the exact columns in design section 4. Define the readiness constraint:

```python
CheckConstraint(
    "state = 'draft' OR "
    "(decomposition_approved_by IS NOT NULL AND decomposition_approved_at IS NOT NULL)",
    name="ck_work_units_approved_beyond_draft",
)
```

Define evidence payload presence:

```python
CheckConstraint(
    "stable_ref IS NOT NULL OR payload IS NOT NULL",
    name="ck_evidence_reference_or_payload",
)
```

Define dependency reference shape:

```python
CheckConstraint(
    "(depends_on_work_unit_id IS NOT NULL) <> (external_ref IS NOT NULL)",
    name="ck_dependencies_exactly_one_reference",
)
```

- [ ] **Step 4: Add database-enforced append-only triggers**

The migration creates one function:

```sql
CREATE FUNCTION reject_append_only_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
```

Attach `BEFORE UPDATE OR DELETE` triggers to `work_package_revisions`, `evidence`,
`adjudications`, and `events`. The downgrade removes triggers before the function.

- [ ] **Step 5: Pass migration, constraint, and append-only tests**

Run:

```bash
uv run alembic upgrade head
uv run pytest tests/persistence -v
```

Expected: empty-to-head succeeds; invalid rows fail; append-only updates/deletes fail.

- [ ] **Step 6: Run the full gate, review, and commit**

Run `make check` and `/code-review`, resolve findings, then:

```bash
git add alembic.ini migrations src/orchestrator/persistence tests/persistence
git commit -m "feat: add persistent orchestrator schema"
```

---

### Task 4: Implement authority comparison, registration, dependencies, and readiness

**Files:**
- Create: `src/orchestrator/kernel/authority.py`
- Create: `src/orchestrator/kernel/readiness.py`
- Create: `src/orchestrator/persistence/repositories.py`
- Create: `src/orchestrator/services/packages.py`
- Create: `tests/kernel/test_authority.py`
- Create: `tests/kernel/test_readiness.py`
- Create: `tests/services/test_package_registration.py`
- Create: `tests/services/test_dependencies.py`

**Interfaces:**
- Produces: `AuthorityEnvelope`, `authority_fingerprint()`, `is_expansion()`.
- Produces: `ReadinessDecision(status, reasons)`.
- Produces: `register_revision()`, `register_approved_unit()`, `resolve_dependency()`, `evaluate_readiness()`.

- [ ] **Step 1: Write failing authority lattice tests**

```python
@pytest.mark.parametrize(
    ("old", "new", "expanded"),
    [
        ({"repository_write": "allowed"}, {"repository_write": "requires_approval"}, False),
        ({"repository_write": "requires_approval"}, {"repository_write": "allowed"}, True),
        ({"repository_write": "prohibited"}, {"repository_write": "allowed"}, True),
        ({"repository_write": "allowed"}, {"repository_write": "allowed", "email_send": "allowed"}, True),
    ],
)
def test_authority_expansion_is_fail_closed(old, new, expanded):
    assert is_expansion(envelope(old), envelope(new)) is expanded
```

Add finite-budget tests: `3 → 2` is non-expanding; `3 → 4`, `3 → null`, and unknown
fields are expanding.

- [ ] **Step 2: Write failing readiness tests**

Test:

- Unapproved revision/decomposition returns `not_authorized`.
- Pending dependency returns `blocked`.
- Authority mismatch returns `not_authorized`.
- Satisfied dependencies plus exact authority approval returns `ready`.
- Draft cannot be claimed through the registration service.
- Internal dependency cycles are rejected.

- [ ] **Step 3: Implement canonical authority normalization**

```python
@dataclass(frozen=True)
class AuthorityBudgets:
    max_attempts: int | None
    max_llm_calls: int | None


@dataclass(frozen=True)
class AuthorityEnvelope:
    capabilities: Mapping[str, str]
    budgets: AuthorityBudgets
    unknown_fields: frozenset[str] = frozenset()

    def level_for(self, capability: str) -> str:
        return self.capabilities.get(capability, "prohibited")


RESTRICTION = {"prohibited": 0, "requires_approval": 1, "allowed": 2}


def is_expansion(old: AuthorityEnvelope, new: AuthorityEnvelope) -> bool:
    if new.unknown_fields:
        return True
    capabilities = set(old.capabilities) | set(new.capabilities)
    if any(
        RESTRICTION[new.level_for(capability)] > RESTRICTION[old.level_for(capability)]
        for capability in capabilities
    ):
        return True
    return new.budgets.expands(old.budgets)
```

Serialize normalized envelopes with sorted keys and compact JSON before SHA-256 hashing.

- [ ] **Step 4: Implement manual immutable registration**

`register_revision` requires a registered human actor, exact package revision/hash,
source commit/path, approval event, normalized snapshot, authority fingerprint, and
registry version. Repeating the identical registration is idempotent; conflicting
content for the same package revision returns `revision_conflict`.

`register_approved_unit` requires the human decomposition approval in the same
transaction. It creates only `Draft`; readiness is a separate command.

- [ ] **Step 5: Implement dependency and readiness decisions**

```python
class ReadinessStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    NOT_AUTHORIZED = "not_authorized"


@dataclass(frozen=True)
class ReadinessReason:
    code: str
    subject_id: UUID | None
    detail: str


@dataclass(frozen=True)
class ReadinessDecision:
    status: ReadinessStatus
    reasons: tuple[ReadinessReason, ...]
```

Precedence is `not_authorized`, then `blocked`, then `ready`; return every reason in the
winning category so the decision is explainable.

- [ ] **Step 6: Pass focused and full tests**

Run:

```bash
uv run pytest tests/kernel/test_authority.py tests/kernel/test_readiness.py \
  tests/services/test_package_registration.py tests/services/test_dependencies.py -v
make check
```

Expected: all pass.

- [ ] **Step 7: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/kernel src/orchestrator/persistence \
  src/orchestrator/services/packages.py tests/kernel tests/services
git commit -m "feat: enforce work-unit readiness"
```

---

### Task 5: Make lifecycle state and events atomic

**Files:**
- Create: `src/orchestrator/clock.py`
- Create: `src/orchestrator/services/lifecycle.py`
- Create: `tests/services/test_lifecycle_events.py`
- Create: `tests/services/test_lifecycle_rollback.py`

**Interfaces:**
- Produces: `transition_unit(command: TransitionCommand) -> TransitionResult`.
- Produces immutable `Event` rows for every successful transition.

- [ ] **Step 1: Write the failing atomicity tests**

```python
def test_transition_appends_attributable_event(db_session, ready_unit, worker):
    result = transition_unit(
        db_session,
        TransitionCommand(
            unit_id=ready_unit.id,
            target=WorkUnitState.CLAIMED,
            actor=worker,
            expected_version=ready_unit.version,
            idempotency_key="claim-1",
        ),
    )
    assert result.event.actor_id == worker.actor_id
    assert result.event.from_state == "ready"
    assert result.event.to_state == "claimed"


def test_event_failure_rolls_back_state(db_session, ready_unit, worker, fail_event_insert):
    with pytest.raises(IntegrityError):
        transition_unit(db_session, command_for(ready_unit, worker))
    db_session.expire_all()
    assert db_session.get(WorkUnit, ready_unit.id).state == "ready"
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/services/test_lifecycle_events.py tests/services/test_lifecycle_rollback.py -v`

Expected: FAIL because the lifecycle service does not exist.

- [ ] **Step 3: Implement one transaction boundary**

Use these service contracts:

```python
@dataclass(frozen=True)
class TransitionCommand:
    unit_id: UUID
    target: WorkUnitState
    actor: ActorContext
    expected_version: int
    idempotency_key: str


@dataclass(frozen=True)
class TransitionResult:
    unit_id: UUID
    state: WorkUnitState
    version: int
    event_id: UUID
```

`transition_unit`:

1. Locks the unit with `SELECT ... FOR UPDATE`.
2. Checks expected version and idempotency key.
3. Builds transition guards from persisted approvals/adjudications.
4. Calls `authorize_transition`.
5. Updates state/version.
6. Inserts the immutable event with actor and registry version.
7. Commits once.

Use an injected `Clock` for pure tests and `SELECT transaction_timestamp()` for
PostgreSQL mutation time.

- [ ] **Step 4: Pass tests, full gate, review, and commit**

Run:

```bash
uv run pytest tests/services/test_lifecycle_events.py tests/services/test_lifecycle_rollback.py -v
make check
```

Expected: pass.

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/clock.py src/orchestrator/services/lifecycle.py tests/services
git commit -m "feat: make lifecycle events transactional"
```

---

### Task 6: Implement atomic claims, leases, reclaim, and attempts

**Files:**
- Create: `src/orchestrator/kernel/leases.py`
- Create: `src/orchestrator/services/claims.py`
- Create: `tests/services/test_claims.py`
- Create: `tests/services/test_claim_concurrency.py`
- Create: `tests/services/test_reclaim.py`

**Interfaces:**
- Produces: `claim_unit()`, `renew_claim()`, `reclaim_expired_claim()`, `authorize_retry()`.
- Produces: `LeaseGrant(claim_id, attempt, lease_token, expires_at)`.

- [ ] **Step 1: Write failing two-worker concurrency test**

```python
def test_two_workers_cannot_claim_same_unit(postgres_engine, ready_unit_id):
    barrier = threading.Barrier(2)

    def acquire(worker_id):
        with Session(postgres_engine) as session:
            barrier.wait()
            return claim_unit(session, ready_unit_id, actor(worker_id), f"claim-{worker_id}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ["worker-a", "worker-b"]))

    grants = [result for result in results if isinstance(result, LeaseGrant)]
    conflicts = [result for result in results if isinstance(result, DomainError)]
    assert len(grants) == 1
    assert [error.code for error in conflicts] == ["claim_conflict"]
```

- [ ] **Step 2: Write failing renewal and reclaim tests**

Test:

- Lease duration is exactly 15 minutes from database transaction time.
- Same idempotency key returns the same grant.
- Different input with reused key returns `idempotency_conflict`.
- Only the owning actor/attempt/token can renew.
- Renewal after expiry returns `lease_expired`.
- Reclaim records `claimed|executing → failed → ready → claimed` in one transaction.
- Reclaim increments attempt and invalidates the stale token.
- Third failed attempt exhausts the default budget.
- A human retry approval is required after exhaustion.

- [ ] **Step 3: Implement token and lease policy**

```python
LEASE_DURATION = timedelta(minutes=15)
RENEWAL_CADENCE = timedelta(minutes=5)
DEFAULT_MAX_ATTEMPTS = 3


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

Generate tokens with `secrets.token_urlsafe(32)`, store only the hash, and return the raw
token exactly once.

- [ ] **Step 4: Implement locked claim acquisition and renewal**

Use `SELECT ... FOR UPDATE` on `work_units`. Claim only from `Ready`; increment
`attempt_count`, insert `Claim`, transition to `Claimed`, and append an event in one
transaction.

Renew only the current claim where actor, attempt, and token hash match and
`lease_expires_at > transaction_timestamp()`.

- [ ] **Step 5: Implement expiry recovery**

Within one transaction:

1. Lock the unit and latest claim.
2. Confirm expiry using PostgreSQL time.
3. Terminate the old claim with `terminal_reason = 'lease_expired'`.
4. Record `Claimed|Executing → Failed`.
5. Reevaluate readiness and attempt budget.
6. If eligible, record `Failed → Ready → Claimed`, create the new claim, and increment
   the attempt.
7. If exhausted, remain `Failed` and return `attempts_exhausted`.

- [ ] **Step 6: Pass concurrency and reclaim tests**

Run:

```bash
uv run pytest tests/services/test_claims.py tests/services/test_claim_concurrency.py \
  tests/services/test_reclaim.py -v
make check
```

Expected: exactly one concurrent winner; all renewal/reclaim cases pass.

- [ ] **Step 7: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/kernel/leases.py src/orchestrator/services/claims.py tests/services
git commit -m "feat: add exclusive expiring claims"
```

---

### Task 7: Implement registry bundles and authenticated actor contexts

**Files:**
- Create: `scripts/build_registry_bundle.py`
- Create: `src/orchestrator/identity/registry.py`
- Create: `src/orchestrator/identity/auth.py`
- Create: `tests/identity/test_registry_bundle.py`
- Create: `tests/identity/test_m2m_auth.py`
- Create: `tests/identity/test_forward_auth.py`
- Create: `tests/fixtures/registry-bundle.json`

**Interfaces:**
- Produces: `RegistryAdapter.resolve(agent_id) -> RegistryActor`.
- Produces: `ActorContext(actor_id, role, authority_profile, credential_key_id, registry_version)`.
- Produces: `authenticate_m2m()` and `authenticate_human()`.

- [ ] **Step 1: Write failing bundle and fail-closed tests**

Test:

- Bundle generation includes actor ID, status, runtime, authority profile, and version.
- Bundle contains no credential values.
- Unknown, inactive, reserved, duplicate, and schema-invalid identities fail closed.
- An M2M key maps to exactly one agent.
- A spoofed forward-auth header from an untrusted peer is rejected.
- Only active `human-operator-v1` resolves as a human.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/identity -v`

Expected: FAIL because identity modules do not exist.

- [ ] **Step 3: Implement deterministic bundle generation**

Input arguments:

```text
--registry-dir <security-standards>/registry
--source-revision <40-character git SHA>
--output <path>
```

Output:

```json
{
  "schema": "orchestrator-actor-bundle/v1",
  "source_revision": "0123456789abcdef0123456789abcdef01234567",
  "actors": [
    {
      "agent_id": "devon",
      "version": 1,
      "status": "active",
      "runtime": "human",
      "authority_profile": "human-operator-v1"
    }
  ]
}
```

Sort actors by `agent_id`; reject secrets, unknown keys, duplicate IDs, and a dirty or
non-exact source revision.

- [ ] **Step 4: Implement M2M and human authentication**

Runtime M2M configuration maps credential key IDs to token hashes and agent IDs. Compare
bearer tokens with `secrets.compare_digest`. Never log or return the token.

Forward-auth requires the configured proxy marker/header and maps the authenticated
email to `devon`; strip or reject forward-auth headers outside the trusted proxy path.

- [ ] **Step 5: Pass identity tests and full gate**

Run:

```bash
uv run pytest tests/identity -v
make check
```

Expected: all identity cases pass and credential values are absent from snapshots.

- [ ] **Step 6: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add scripts src/orchestrator/identity tests/identity tests/fixtures
git commit -m "feat: map authenticated actors to registry identities"
```

---

### Task 8: Implement immutable evidence, adjudications, and waivers

**Files:**
- Create: `src/orchestrator/services/evidence.py`
- Create: `tests/services/test_evidence.py`
- Create: `tests/services/test_adjudications.py`
- Create: `tests/services/test_waivers.py`

**Interfaces:**
- Produces: `append_evidence()`, `supersede_evidence()`, `record_adjudication()`.
- Produces: `current_evidence()` and `current_adjudication()`.

- [ ] **Step 1: Write failing evidence tests**

Test:

- Evidence requires `stable_ref` or structured payload.
- Evidence binds package revision, unit, AC, attempt, actor, source revision, and event.
- Duplicate idempotency key returns the original evidence.
- Conflicting reuse returns `idempotency_conflict`.
- Supersession must preserve package revision/unit/AC.
- Database update/delete is rejected.
- Stale attempt evidence is rejected.

- [ ] **Step 2: Write failing adjudication and waiver tests**

Test each outcome: `passed`, `failed`, `waived`, `not_applicable`.

For waiver, assert rejection without:

- Human approver.
- Failed evidence reference.
- Rationale.
- Risk.
- Follow-up.
- Required scope or expiry.

Assert workers receive `role_forbidden`; expired or out-of-scope waivers fail completion
guards.

- [ ] **Step 3: Implement evidence append and supersession**

`append_evidence` validates the current attempt token for attempt-scoped worker evidence,
inserts evidence plus event atomically, and never updates a prior row.

`supersede_evidence` inserts a new row whose `supersedes_evidence_id` targets the current
terminal record for the same package revision, unit, and AC.

- [ ] **Step 4: Implement adjudication authorization**

Verifier roles may record `passed`, `failed`, and `not_applicable` when authorized.
Only a human operator may record `waived`. All corrections insert a new adjudication
linked by `supersedes_adjudication_id`.

- [ ] **Step 5: Pass tests, full gate, review, and commit**

Run:

```bash
uv run pytest tests/services/test_evidence.py tests/services/test_adjudications.py \
  tests/services/test_waivers.py -v
make check
```

Expected: all pass.

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/services/evidence.py tests/services
git commit -m "feat: record immutable AC evidence and outcomes"
```

---

### Task 9: Expose lifecycle behavior through the REST API

**Files:**
- Create: `src/orchestrator/api/schemas.py`
- Create: `src/orchestrator/api/dependencies.py`
- Create: `src/orchestrator/api/routes.py`
- Create: `src/orchestrator/api/health.py`
- Create: `src/orchestrator/main.py`
- Create: `tests/api/test_lifecycle_api.py`
- Create: `tests/api/test_api_errors.py`
- Create: `tests/api/test_health.py`

**Interfaces:**
- Produces versioned `/api/v1` routes over existing services.
- Produces `/health/live` and `/health/ready`.

- [ ] **Step 1: Write failing API contract tests**

Exercise:

- Register revision and approved unit.
- Explain readiness.
- Ready, claim, renew, start, block, request approval, approve, submit, verify, review,
  complete, fail, retry, and cancel.
- Append/list evidence and history.
- Worker completion returns HTTP 403 with `role_forbidden`.
- Invalid edge returns HTTP 409 with `invalid_transition`.
- Version conflict returns HTTP 409 with current state/version.

Expected error shape:

```json
{
  "error": {
    "code": "invalid_transition",
    "message": "executing -> completed is not legal",
    "current_state": "executing",
    "current_version": 4,
    "recovery": "submit"
  }
}
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/api -v`

Expected: FAIL because the app and routes do not exist.

- [ ] **Step 3: Implement request/response schemas and dependencies**

Every mutation schema includes:

```python
class CommandBase(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0)
```

Dependencies provide one SQLAlchemy session and one authenticated `ActorContext` per
request. Domain errors map centrally to stable HTTP responses.

- [ ] **Step 4: Implement thin routes**

Routes parse requests, call one application service, and serialize results. They do not
query models directly or implement transition rules.

- [ ] **Step 5: Implement honest health endpoints**

`/health/live` returns `200 {"status":"ok"}` without a database query.

`/health/ready` checks `SELECT 1` and compares the database revision with the single
Alembic head. Database failure or drift returns HTTP 503 with a stable reason code and
no connection details.

- [ ] **Step 6: Pass tests, full gate, review, and commit**

Run:

```bash
uv run pytest tests/api -v
make check
```

Expected: pass.

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/api src/orchestrator/main.py tests/api
git commit -m "feat: expose orchestrator lifecycle API"
```

---

### Task 10: Add the HTTP-only CLI and prove API parity

**Files:**
- Create: `src/orchestrator/cli.py`
- Create: `tests/cli/test_cli_contract.py`
- Create: `tests/cli/test_cli_errors.py`

**Interfaces:**
- Produces console entry point `orchestrator = orchestrator.cli:app`.
- Consumes only the REST API through HTTPX.

- [ ] **Step 1: Add the console entry point and write failing parity tests**

Add to `pyproject.toml`:

```toml
[project.scripts]
orchestrator = "orchestrator.cli:app"
```

The parity matrix invokes API and CLI for each lifecycle command and compares:

- Result state.
- Event ID.
- Error code.
- Current version.
- Recovery action.

Assert the CLI package does not import `orchestrator.persistence`, `sqlalchemy`, or
`orchestrator.kernel`.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/cli -v`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement the thin Typer client**

```python
app = typer.Typer(no_args_is_help=True)


def request(method: str, path: str, payload: dict | None = None) -> dict:
    with httpx.Client(
        base_url=settings.api_url,
        headers={"Authorization": f"Bearer {settings.api_token}"},
        timeout=30.0,
    ) as client:
        response = client.request(method, path, json=payload)
    if response.is_error:
        raise CliError.from_response(response)
    return response.json()
```

Commands mirror the API nouns and verbs exactly. `--json` writes deterministic JSON;
human output includes unit ID, state, version, and event ID.

- [ ] **Step 4: Pass parity tests and full gate**

Run:

```bash
uv run pytest tests/cli -v
make check
```

Expected: every command matches API behavior.

- [ ] **Step 5: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add pyproject.toml uv.lock src/orchestrator/cli.py tests/cli
git commit -m "feat: add lifecycle CLI with API parity"
```

---

### Task 11: Add the minimal human review UI and Evidence Pack

**Files:**
- Create: `src/orchestrator/web.py`
- Create: `src/orchestrator/templates/base.html`
- Create: `src/orchestrator/templates/queue.html`
- Create: `src/orchestrator/templates/unit.html`
- Create: `src/orchestrator/templates/evidence_pack.html`
- Create: `tests/web/test_queue.py`
- Create: `tests/web/test_human_actions.py`
- Create: `tests/web/test_csrf.py`
- Create: `tests/web/test_evidence_pack.py`

**Interfaces:**
- Produces human-authenticated `/review` routes.
- Produces read-only `/review/units/{id}/evidence-pack`.

- [ ] **Step 1: Write failing UI behavior tests**

Test:

- Queue groups units by state and displays readiness reason codes.
- Detail shows package revision/hash, authority, dependency, attempt/lease, evidence,
  adjudication, approval, and event data.
- Approval, review outcome, cancellation, and retry require human auth and valid CSRF.
- Worker operations and creation are absent from the UI.
- Evidence Pack includes current and superseded evidence and named waiver facts.
- Evidence Pack forms no independent editable record.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/web -v`

Expected: FAIL because web routes/templates do not exist.

- [ ] **Step 3: Implement the read-oriented UI**

Use semantic HTML, visible focus styles, labels for every control, and no color-only
state indicators. HTMX may replace queue/detail fragments, but every action must work as
a normal POST/redirect/GET flow.

The mutation form shape is:

```html
<form method="post" action="{{ action_url }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="expected_version" value="{{ unit.version }}">
  <label for="reason">Reason</label>
  <textarea id="reason" name="reason" required></textarea>
  <button type="submit">{{ action_label }}</button>
</form>
```

- [ ] **Step 4: Implement Evidence Pack projection**

Query canonical records and render:

- Package revision/hash/source commit.
- Unit state and actors.
- Authority and approvals.
- Dependencies and claims.
- AC-keyed evidence including supersession chain.
- Current adjudication and waiver metadata.
- Event history.

No POST route exists for Evidence Pack content.

- [ ] **Step 5: Pass tests and perform rendered review**

Run:

```bash
uv run pytest tests/web -v
make check
uv run uvicorn orchestrator.main:app --port 8000
```

Expected: tests pass; local queue and unit pages render without console or server errors.

Use the in-app browser to inspect keyboard navigation, focus, empty states, long
evidence references, expired leases, and waiver display. Record screenshots only as
review evidence; do not track generated browser data.

- [ ] **Step 6: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add src/orchestrator/web.py src/orchestrator/templates tests/web
git commit -m "feat: add human review surface"
```

---

### Task 12: Add container, registry build, migration operations, and CI

**Files:**
- Create: `Dockerfile`
- Modify: `docker-compose.yml`
- Create: `.dockerignore`
- Modify: `.github/workflows/quality.yml`
- Create: `.github/dependabot.yml`
- Create: `docs/operations/local-development.md`
- Create: `docs/operations/migrations.md`
- Create: `docs/operations/authentication.md`
- Create: `tests/architecture/test_container.py`
- Create: `tests/architecture/test_registry_provenance.py`
- Create: `tests/architecture/test_no_automatic_merge.py`
- Create: `tests/architecture/test_scope_guards.py`

**Interfaces:**
- Produces one port-8000 image with an embedded registry bundle.
- Produces the exact named CI check `Quality`.

- [ ] **Step 1: Write failing architecture tests**

Test:

- Dockerfile uses Python 3.12 and runs as non-root.
- Registry bundle source revision is exact and recorded.
- No workflow contains `gh pr merge`, GitHub merge API calls, or direct push to `main`.
- No application module imports InfraOps, GitHub Actions dispatch, Linear, or Todoist.
- No event publisher or production mutation route exists.
- Healthcheck targets `/health/live`.
- Migration is an explicit command, not an implicit web startup side effect.

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/architecture -v`

Expected: FAIL because image and operations files are missing.

- [ ] **Step 3: Implement the immutable image**

Use a multi-stage Dockerfile:

1. `python:3.12-slim` builder installs from `uv.lock`.
2. Builder runs `scripts/build_registry_bundle.py` against a pinned
   `SECURITY_STANDARDS_REVISION` build context artifact.
3. Runtime copies the virtual environment, application, migrations, templates, and
   generated bundle.
4. Runtime uses an unprivileged UID, exposes 8000, and starts Uvicorn.

No credential or runtime env file is copied into the image.

- [ ] **Step 4: Document exact local and migration operations**

Document:

```bash
docker compose up -d orchestrator-postgres
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn orchestrator.main:app --reload --port 8000
uv run orchestrator --help
```

Migration documentation states:

- Check current/head before upgrade.
- Back up durable databases before an infrastructure package applies migrations.
- WS-3.1 uses disposable local databases only.
- Production/dev-Coolify deployment requires a separate approved
  `infrastructure-change` package.

- [ ] **Step 5: Implement exact `Quality` CI**

The workflow:

1. Starts `postgres:16-alpine`.
2. Installs Python 3.12 and pinned uv.
3. Runs `uv sync --frozen`.
4. Runs `uv run alembic upgrade head`.
5. Runs `make check`.
6. Builds the container with a fixture registry bundle.

The workflow never deploys and never merges.

- [ ] **Step 6: Pass architecture, full, and image checks**

Run:

```bash
uv run pytest tests/architecture -v
make check
docker build -t orchestrator:ws31 .
docker run --rm --entrypoint orchestrator orchestrator:ws31 --help
```

Expected: all checks pass; image builds; CLI help exits zero.

- [ ] **Step 7: Review and commit**

Run `/code-review`, resolve findings, then:

```bash
git add Dockerfile docker-compose.yml .dockerignore .github docs/operations \
  tests/architecture
git commit -m "chore: make orchestrator build and CI reproducible"
```

---

### Task 13: Run whole-branch verification and create the WS-3.1 evidence index

**Files:**
- Create: `docs/evidence/ws-3.1-evidence-index.md`
- Modify: `PROJECT.md`

**Interfaces:**
- Produces literal evidence for AC-001 through AC-014.
- Produces no completion claim; Devon's UI review and merge remain outstanding.

- [ ] **Step 1: Run the full local verification**

Run:

```bash
docker compose up -d orchestrator-postgres
uv sync --frozen
uv run alembic downgrade base
uv run alembic upgrade head
make check
docker build -t orchestrator:ws31 .
```

Expected:

- Empty-to-head migration succeeds.
- Ruff, formatting, Pyright, and all tests pass.
- Image builds.

- [ ] **Step 2: Run focused acceptance evidence**

Run:

```bash
uv run pytest tests/kernel/test_state_graph.py -v
uv run pytest tests/services/test_claim_concurrency.py tests/services/test_reclaim.py -v
uv run pytest tests/services/test_evidence.py tests/services/test_waivers.py -v
uv run pytest tests/api tests/cli -v
uv run pytest tests/architecture -v
```

Expected: all pass with counts recorded verbatim in the evidence index.

- [ ] **Step 3: Run required security and code review**

Run:

```bash
PYTHONPATH="$HOME/Projects/security-standards/src" \
  python3 -m security_scan.cli . --category security
```

Expected: no BLOCK findings.

Run `/code-review` on the complete diff against
`~/Developer/code-standards/STANDARDS.md`. Explicitly inspect wrong abstractions,
duplication, comments that restate code, weak tests, and new suppression comments.
Resolve every blocking finding.

- [ ] **Step 4: Run the final adversarial architecture review**

Check:

- Lifecycle graph implementation exactly matches the spec.
- Expiry recovery uses only `Failed → Ready → Claimed`.
- PostgreSQL, not process memory, arbitrates claims.
- Worker routes cannot complete or waive.
- Every mutation appends an event in the same transaction.
- Package content is not duplicated as an editable canonical document.
- No external event publication, dispatch, deployment, merge, or tracker-canonical path.

Record findings and resolutions in the evidence index.

- [ ] **Step 5: Write the literal AC evidence index**

Use one section per AC:

```markdown
## AC-001

- Outcome: passed | failed | waived | not_applicable
- Evidence: exact test command, result count, commit SHA, and stable artifact/reference
- Recorded by: actor ID
- Recorded at: UTC timestamp
- Review: policy or named human
```

Do not mark AC-011 or AC-014 passed until Devon performs UI review and merge. Do not
reinterpret a failing named check as success.

- [ ] **Step 6: Run the foundation matrix**

Run:

```bash
cd ~/Projects/project-standards
uv run portfolio foundation
```

Expected: orchestrator appears with no new violation or unknown cell. If it does not
appear, record onboarding as a blocker rather than editing project-standards out of
scope.

- [ ] **Step 7: Commit verification artifacts**

Run:

```bash
git add docs/evidence/ws-3.1-evidence-index.md PROJECT.md
git commit -m "docs: record WS-3.1 verification evidence"
```

---

### Task 14: Publish a draft pull request and stop at Devon's merge gate

**Files:**
- No application changes expected.
- Modify evidence index only if CI adds exact-revision results.

**Interfaces:**
- Produces a draft PR in `AlobarQuest/orchestrator`.
- Consumes the exact named `Quality` check.

- [ ] **Step 1: Verify branch and remote scope**

Confirm:

```bash
git branch --show-current
git remote get-url origin
git diff --check main...HEAD
```

Expected:

- Branch is `codex/ws31-orchestrator-core`.
- Remote is `AlobarQuest/orchestrator`.
- Diff contains only approved WS-3.1 work and has no whitespace errors.

- [ ] **Step 2: Open a draft PR**

The PR body includes:

- Intent package revision/hash.
- Design and plan links.
- AC-001 through AC-014 checklist.
- Exact local commands/results.
- Security and architecture review summaries.
- Explicit statement: “Devon alone merges; no automatic merge path exists.”

- [ ] **Step 3: Wait for exact named CI**

Run:

```bash
gh pr checks --watch
```

Expected: `Quality` succeeds on the exact PR head SHA. A historical run or differently
named check does not count.

- [ ] **Step 4: Record CI evidence without rewriting failures**

Append the PR URL, head SHA, workflow URL, check name, conclusion, and completion time to
the evidence index. If `Quality` fails, leave the failure recorded, transition the work
to revision-required behavior, fix through another reviewed slice, and rerun.

- [ ] **Step 5: Perform final whole-branch review**

Repeat the security scan, `make check`, `/code-review`, and adversarial architecture
check against the exact PR head. Push only review fixes with their own tests and commits.

- [ ] **Step 6: Stop for Devon**

Report:

- Exact PR head SHA.
- Intent hash.
- `Quality` result.
- Remaining human ACs.
- Known risks or absent evidence.

Do not run `gh pr ready`, `gh pr merge`, merge locally, or push a merge commit. Devon
alone decides whether and when to merge.

## Acceptance-criterion coverage

| Intent AC | Primary tasks |
|---|---|
| AC-001 complete legal/invalid graph | 2, 13 |
| AC-002 approval/dependency/authority readiness | 4, 9, 13 |
| AC-003 concurrent claims, renewal, reclaim, attempts | 6, 13 |
| AC-004 worker role boundary | 2, 8, 9, 13 |
| AC-005 atomic attributable events | 5, 6, 8, 13 |
| AC-006 immutable associated evidence | 3, 8, 13 |
| AC-007 adjudication and human waiver | 3, 8, 11, 13 |
| AC-008 authority expansion approval | 4, 13 |
| AC-009 API/CLI parity | 9, 10, 13 |
| AC-010 migration and health checks | 3, 9, 12, 13 |
| AC-011 human review UI | 7, 11, 13 |
| AC-012 explicit architecture decisions | approved design commit `f233498` |
| AC-013 no merge/mutation/secret/worker-completion path | 7, 12, 13 |
| AC-014 Devon-only review and merge | 14 |
