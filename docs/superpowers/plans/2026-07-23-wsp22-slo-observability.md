# WS-P2.2 Factory SLOs + Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only, time-windowed SLO/observability report over the existing event store, plus a first-class `events.improvisation` signal, exposed as a service + `GET /api/v1/slo-report` route + `orchestrator slo-report` CLI.

**Architecture:** Mirror the `status_ledger` projection (service dataclass → route with `response_model` + `from_attributes` → typer CLI HTTP-client command). All metrics carry an explicit status (`computed`/`no_data`/`not_instrumented`/`partial`) so gaps are stated, never zero-filled. Cost/tokens are hard `not_instrumented` (no source data). Improvisation is a true count of human operator-override transitions, stamped write-time in `_transition_event`.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, FastAPI, Pydantic v2, Typer, httpx, pytest, Postgres.

Design spec: `docs/superpowers/specs/2026-07-23-wsp22-slo-observability-design.md` (read it first).

## Global Constraints

- **Python 3.12+.** Use `StrEnum`, `X | None`, `list[...]` builtins.
- **No new collectors** beyond the `events.improvisation` column. The report only reads what the store already records.
- **Never read `work_units.updated_at` for timing** — a `BEFORE UPDATE` trigger rewrites it to `now()` on every mutation. All timing derives from `events.occurred_at` / claim / adjudication timestamps.
- **Never zero-fill a metric with no data.** Empty window → `no_data`; zero denominator → `no_data`; no source table → `not_instrumented`. A deleted guard must red a named test.
- **Scope guard:** `tests/architecture/test_ws32_scope_guards.py` scans runtime string literals *including docstrings* under `src/orchestrator/` for the bare words `dispatch` and `deploy`. The new `slo_report.py` (and any src prose) must not contain those bare words — use synonyms ("hand-off to the runner", "release-revert"). This applies to code/docstrings under `src/`, not to this plan or the spec.
- **`make check` exit 0 / exit-5 does not prove tests ran.** Always read the `collected N items` line. Tests need Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR` set, and `alembic upgrade head`; the `migrated_session` fixture handles the last.
- **Resolve tools from `.venv/bin` first.** Run pytest as `.venv/bin/pytest`, not a global `pytest`.
- **Commit trailers:** use the repo's commit convention (the `Co-Authored-By` / `Claude-Session` trailers from the session bash instructions). Step commit messages below omit them for brevity — add them.

**Standard test command** (used throughout; assumes Postgres is up):
```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest <path> -v
```

---

### Task 1: `events.improvisation` column (migration + model)

**Files:**
- Create: `migrations/versions/0016_wsp22_event_improvisation.py`
- Modify: `src/orchestrator/persistence/models.py` (class `Event`, ~L460-474)
- Test: `tests/persistence/test_event_improvisation_column.py`

**Interfaces:**
- Produces: `Event.improvisation: bool` (SQLAlchemy mapped column, non-null, defaults `False`); DB column `events.improvisation BOOLEAN NOT NULL DEFAULT false`.

- [ ] **Step 1: Write the failing test**

Create `tests/persistence/test_event_improvisation_column.py`:
```python
import uuid

from orchestrator.persistence.models import Event


def test_event_improvisation_defaults_false(migrated_session):
    event = Event(
        actor_id="worker-1",
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=uuid.uuid4(),
        from_state="claimed",
        to_state="executing",
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key="evt-improv-default",
    )
    migrated_session.add(event)
    migrated_session.flush()
    migrated_session.refresh(event)
    assert event.improvisation is False
```

If `tests/persistence/` has no `conftest.py` exposing `migrated_session`, reuse the services one: either add `tests/persistence/conftest.py` that imports the fixtures, or place this test under `tests/services/`. Check `tests/persistence/conftest.py` first; if absent, put the test file in `tests/services/` instead and adjust the path in later commands.

- [ ] **Step 2: Run test to verify it fails**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/persistence/test_event_improvisation_column.py -v
```
Expected: FAIL — either `AttributeError: 'Event' object has no attribute 'improvisation'` or a DB error that the column does not exist.

- [ ] **Step 3: Add the column to the model**

In `src/orchestrator/persistence/models.py`, first confirm `Boolean` is imported from `sqlalchemy` (the import block near the top). If not, add `Boolean` to the existing `from sqlalchemy import (...)` group. Then add this line to class `Event` (after `idempotency_key`):
```python
    improvisation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0016_wsp22_event_improvisation.py`:
```python
"""Add events.improvisation — a first-class marker for human operator overrides (WS-P2.2).

Revision ID: 0016_wsp22_event_improvisation
Revises: 0015_wsp216_binding_attempt

The SLO report needs to count how often a human acted outside the declared contract, truthfully
rather than by scraping. The write path (``_transition_event``) knows the actor's role, source, and
target, so it stamps this boolean at the moment of the transition. NOT NULL with a ``false`` default
is safe on the append-only ``events`` table and leaves every existing row and every other event
emit site untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_wsp22_event_improvisation"
down_revision = "0015_wsp216_binding_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "improvisation",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "improvisation")
```

Confirm `0015_wsp216_binding_attempt` is the current head: `.venv/bin/alembic heads` should print it. If a newer head exists, set `down_revision` to that and renumber this file to the next ordinal.

- [ ] **Step 5: Run test to verify it passes**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/persistence/test_event_improvisation_column.py -v
```
Expected: PASS (1 collected, 1 passed).

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0016_wsp22_event_improvisation.py src/orchestrator/persistence/models.py tests/persistence/test_event_improvisation_column.py
git commit -m "feat(slo): add events.improvisation column (WS-P2.2)"
```

---

### Task 2: `DESIGNED_HUMAN_GATES` + stamp improvisation in `_transition_event`

**Files:**
- Modify: `src/orchestrator/kernel/transitions.py` (after `HUMAN_EDGES`, ~L61)
- Modify: `src/orchestrator/services/lifecycle.py` (`_transition_event`, ~L176-200)
- Test: `tests/services/test_improvisation_stamp.py`

**Interfaces:**
- Consumes: `Event.improvisation` (Task 1); `ActorRole` (`orchestrator.kernel.states`).
- Produces: `transitions.DESIGNED_HUMAN_GATES: frozenset[Edge]`; `work_unit.transitioned` events now carry `improvisation=True` iff a HUMAN drives a non-designed-gate edge.

The definition (spec §7, "overrides only"): `improvisation = actor.role is ActorRole.HUMAN and (source, target) not in DESIGNED_HUMAN_GATES`. Counts `*→CANCELLED` and verifier-bypass `SUBMITTED/VERIFYING→COMPLETED`; excludes the approval-resume and the review verdicts.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_improvisation_stamp.py`. This drives the REAL services (write-path correctness matters here). It uses builders defined inline:
```python
import uuid

from orchestrator.clock import TransactionClock
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.claims import claim_unit

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)


def _revision(session):
    now = TransactionClock().now(session)
    return register_revision(
        session,
        package_id="pkg-improv",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:improv",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=now,
        approval_event_id=str(uuid.UUID(int=1)),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _approved_unit(session, key):
    now = TransactionClock().now(session)
    revision = _revision(session)
    return register_approved_unit(
        session,
        unit_id=None,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=now,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def _transitioned_event(session, unit_id, to_state):
    return session.scalar(
        Event.__table__.select()
        .where(Event.subject_id == unit_id)
        .where(Event.to_state == to_state.value)
        .order_by(Event.occurred_at.desc())
    )


def _latest_transition(session, unit_id, to_state):
    from sqlalchemy import select

    return session.scalar(
        select(Event)
        .where(Event.subject_id == unit_id, Event.to_state == to_state.value)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
    )


def test_worker_transition_is_not_improvisation(migrated_session):
    unit = _approved_unit(migrated_session, "u-worker")
    grant = claim_unit(migrated_session, unit.id, WORKER, "claim-1")
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="start-1",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    event = _latest_transition(migrated_session, unit.id, WorkUnitState.EXECUTING)
    assert event is not None
    assert event.improvisation is False


def test_human_cancel_is_improvisation(migrated_session):
    unit = _approved_unit(migrated_session, "u-cancel")
    grant = claim_unit(migrated_session, unit.id, WORKER, "claim-2")
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="start-2",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
            context_snapshot_id=grant.context_snapshot_id,
        ),
    )
    executing = _latest_transition(migrated_session, unit.id, WorkUnitState.EXECUTING)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.CANCELLED,
            actor=HUMAN,
            expected_version=executing_unit_version(migrated_session, unit.id),
            idempotency_key="cancel-2",
            reason="operator override",
        ),
    )
    event = _latest_transition(migrated_session, unit.id, WorkUnitState.CANCELLED)
    assert event is not None
    assert event.improvisation is True


def executing_unit_version(session, unit_id):
    return session.get(WorkUnit, unit_id).version
```

Notes for the implementer:
- `claim_unit` may require a `standing_context=` argument (see `tests/services/test_status_ledger.py` which passes `standing_context=valid_context()`). If the call raises for a missing context, import `valid_context` from the existing test helper it lives in (grep `def valid_context`) and pass it to `claim_unit` and to the EXECUTING transition.
- The `(CLAIMED, CANCELLED)` edge is also a HUMAN override — if driving to EXECUTING complicates setup, cancel directly from CLAIMED instead (skip the EXECUTING transition and cancel right after `claim_unit`, using the claimed version).
- Add a THIRD test for the designed-gate exclusion (approval-resume `AWAITING_APPROVAL → READY` → `improvisation is False`). This requires driving a unit to `AWAITING_APPROVAL` (a WORKER edge from CLAIMED/EXECUTING) and recording an approval so the guard passes. Model the approval on `record_approval` usage in `tests/services/test_status_ledger.py` / `test_lifecycle*.py`. Grep `def record_approval` and an existing test that resumes from awaiting_approval, and replicate. Assert the resulting `AWAITING_APPROVAL → READY` transitioned event has `improvisation is False`. **Do not skip this test — it is the negative control proving the metric does not over-count sanctioned gates.**

- [ ] **Step 2: Run tests to verify they fail**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_improvisation_stamp.py -v
```
Expected: the cancel/designed-gate assertions FAIL (improvisation is False for all events because nothing stamps it yet); the worker test may already pass.

- [ ] **Step 3: Add `DESIGNED_HUMAN_GATES`**

In `src/orchestrator/kernel/transitions.py`, after the `HUMAN_EDGES = {...}` block (~L61), add:
```python
# The contract's designated human decision points -- NOT operator improvisation. The approval-resume
# and the human-review verdicts are declared parts of the lifecycle; the SLO improvisation counter
# excludes them so it measures overrides, not healthy human-in-the-loop steps (WS-P2.2).
DESIGNED_HUMAN_GATES: frozenset[Edge] = frozenset(
    {
        (WorkUnitState.AWAITING_APPROVAL, WorkUnitState.READY),
        (WorkUnitState.AWAITING_REVIEW, WorkUnitState.COMPLETED),
        (WorkUnitState.AWAITING_REVIEW, WorkUnitState.REVISION_REQUIRED),
    }
)
```
Confirm `Edge` is the type alias already used in this file for `(WorkUnitState, WorkUnitState)` tuples (it appears in `EDGE_ROLES: dict[Edge, ...]`). If `Edge` is defined later than this insertion point, place `DESIGNED_HUMAN_GATES` after its definition, or after `EDGE_ROLES`.

- [ ] **Step 4: Stamp improvisation in `_transition_event`**

In `src/orchestrator/services/lifecycle.py`, add the import near the other `kernel.transitions` / `kernel.states` imports:
```python
from orchestrator.kernel.transitions import DESIGNED_HUMAN_GATES
```
(If `lifecycle.py` already imports from `orchestrator.kernel.transitions`, add `DESIGNED_HUMAN_GATES` to that import.) Then modify `_transition_event` so the `Event(...)` gets an `improvisation` kwarg. Compute it just before the `return Event(`:
```python
    improvisation = (
        command.actor.role is ActorRole.HUMAN
        and (source, command.target) not in DESIGNED_HUMAN_GATES
    )
    return Event(
        occurred_at=occurred_at,
        actor_id=command.actor.actor_id,
        action="work_unit.transitioned",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=source,
        to_state=command.target,
        payload={
            "actor_role": command.actor.role,
            "command": _command_identity(command, source),
            "registry_version": registry_version,
            "reason": command.reason,
            "version": unit.version,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=command.idempotency_key,
        improvisation=improvisation,
    )
```
Confirm `ActorRole` is imported in `lifecycle.py` (it is used for `ActorContext.role`); if not, add `from orchestrator.kernel.states import ActorRole`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_improvisation_stamp.py -v
```
Expected: PASS (all tests, including the designed-gate negative control).

- [ ] **Step 6: Run the transitions + lifecycle suites to check for regressions**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/kernel tests/services -k "transition or lifecycle or improvis" -v
```
Expected: green; read the collected count.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/kernel/transitions.py src/orchestrator/services/lifecycle.py tests/services/test_improvisation_stamp.py
git commit -m "feat(slo): stamp human operator-override transitions as improvisation (WS-P2.2)"
```

---

### Task 3: `slo_report` service skeleton + shared test builders

**Files:**
- Create: `src/orchestrator/services/slo_report.py`
- Create: `tests/services/test_slo_report.py`

**Interfaces:**
- Produces:
  - `SloReportFilters(since: datetime | None = None, until: datetime | None = None)` (frozen dataclass)
  - `MetricValue(status: str, value: float | None, basis: str)` (frozen dataclass)
  - `SloReport(...)` (frozen dataclass) with `datetime` fields `since`, `until` and `MetricValue` fields `intake_to_first_work`, `queue_age`, `claim_expiry_rate`, `waiver_frequency`, `revert_rate`, `evidence_completeness`, `cost_per_unit`, `token_consumption`, `improvisation`
  - `slo_report(session, filters: SloReportFilters | None = None) -> SloReport`
  - Status constants `STATUS_COMPUTED`, `STATUS_NO_DATA`, `STATUS_NOT_INSTRUMENTED`, `STATUS_PARTIAL`
  - `DEFAULT_WINDOW = timedelta(days=7)`
  - Private per-metric helpers `_intake_to_first_work`, `_queue_age`, `_claim_expiry_rate`, `_waiver_frequency`, `_revert_rate`, `_evidence_completeness`, `_improvisation` each `(session, since, until, now) -> MetricValue`; Tasks 4-7 fill their bodies. `_cost` / `_tokens` return `not_instrumented` now.
- Consumes (test builders, reused by Tasks 4-7): `_build_unit`, `_add_event`, `_add_claim`.

- [ ] **Step 1: Write the failing skeleton test**

Create `tests/services/test_slo_report.py`:
```python
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from orchestrator.clock import TransactionClock
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Claim, Event, WorkUnit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.slo_report import (
    STATUS_NOT_INSTRUMENTED,
    STATUS_NO_DATA,
    SloReportFilters,
    slo_report,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)


# ---- shared builders (reused by Tasks 4-7) ---------------------------------

def _build_unit(session, key, *, enforcement=None):
    now = TransactionClock().now(session)
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=now,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot=enforcement or {"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        unit_id=None,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=now,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    return revision, unit


def _add_event(session, unit_id, *, action, to_state, occurred_at, from_state=None,
               improvisation=False, actor_id="system", actor_role="system"):
    event = Event(
        occurred_at=occurred_at,
        actor_id=actor_id,
        action=action,
        subject_type="work_unit",
        subject_id=unit_id,
        from_state=from_state,
        to_state=to_state,
        payload={"actor_role": actor_role},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"evt-{uuid.uuid4()}",
        improvisation=improvisation,
    )
    session.add(event)
    session.flush()
    return event


def _add_claim(session, unit_id, *, attempt, acquired_at, terminal_reason=None,
               lease_expires_at=None):
    claim = Claim(
        work_unit_id=unit_id,
        attempt=attempt,
        claimed_by="worker-1",
        lease_token_hash=f"hash-{uuid.uuid4()}",
        idempotency_key=f"claim-{uuid.uuid4()}",
        acquired_at=acquired_at,
        lease_expires_at=lease_expires_at or (acquired_at + timedelta(minutes=30)),
        terminal_reason=terminal_reason,
        released_at=acquired_at if terminal_reason else None,
    )
    session.add(claim)
    session.flush()
    return claim


# ---- skeleton tests --------------------------------------------------------

def test_empty_store_reports_no_data_and_not_instrumented(migrated_session):
    report = slo_report(migrated_session)
    # window defaults to 7 days ending "now"
    assert (report.until - report.since) == timedelta(days=7)
    for metric in (
        report.intake_to_first_work,
        report.queue_age,
        report.claim_expiry_rate,
        report.waiver_frequency,
        report.revert_rate,
        report.evidence_completeness,
        report.improvisation,
    ):
        assert metric.status == STATUS_NO_DATA
        assert metric.value is None


def test_cost_and_tokens_are_not_instrumented(migrated_session):
    """Guard test: cost/tokens have no source data and must never be silently zero-filled."""
    report = slo_report(migrated_session)
    assert report.cost_per_unit.status == STATUS_NOT_INSTRUMENTED
    assert report.cost_per_unit.value is None
    assert report.token_consumption.status == STATUS_NOT_INSTRUMENTED
    assert report.token_consumption.value is None


def test_explicit_window_is_respected(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.since == since
    assert report.until == until
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -v
```
Expected: FAIL — `ModuleNotFoundError: orchestrator.services.slo_report`.

- [ ] **Step 3: Write the skeleton service**

Create `src/orchestrator/services/slo_report.py`. **Do not use the bare words `dispatch`/`deploy` anywhere in this file** (scope guard). Use "release-revert" for the deploy-revert blind spot.
```python
"""On-demand, time-windowed SLO report over the event store (WS-P2.2).

A read-only projection: every metric carries an explicit status so a gap is stated, never
zero-filled. Timing derives from event/claim/adjudication timestamps -- never from
``work_units.updated_at`` (a trigger rewrites it on every mutation).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock

STATUS_COMPUTED = "computed"
STATUS_NO_DATA = "no_data"
STATUS_NOT_INSTRUMENTED = "not_instrumented"
STATUS_PARTIAL = "partial"

DEFAULT_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class SloReportFilters:
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class MetricValue:
    status: str
    value: float | None
    basis: str


@dataclass(frozen=True)
class SloReport:
    since: datetime
    until: datetime
    intake_to_first_work: MetricValue
    queue_age: MetricValue
    claim_expiry_rate: MetricValue
    waiver_frequency: MetricValue
    revert_rate: MetricValue
    evidence_completeness: MetricValue
    cost_per_unit: MetricValue
    token_consumption: MetricValue
    improvisation: MetricValue


def slo_report(session: Session, filters: SloReportFilters | None = None) -> SloReport:
    criteria = filters or SloReportFilters()
    now = TransactionClock().now(session)
    until = criteria.until or now
    since = criteria.since or (until - DEFAULT_WINDOW)
    return SloReport(
        since=since,
        until=until,
        intake_to_first_work=_intake_to_first_work(session, since, until, now),
        queue_age=_queue_age(session, since, until, now),
        claim_expiry_rate=_claim_expiry_rate(session, since, until, now),
        waiver_frequency=_waiver_frequency(session, since, until, now),
        revert_rate=_revert_rate(session, since, until, now),
        evidence_completeness=_evidence_completeness(session, since, until, now),
        cost_per_unit=_cost(session, since, until, now),
        token_consumption=_tokens(session, since, until, now),
        improvisation=_improvisation(session, since, until, now),
    )


_NO_DATA_STUB = "not yet implemented"


def _intake_to_first_work(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _queue_age(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _claim_expiry_rate(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _waiver_frequency(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _revert_rate(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _evidence_completeness(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _improvisation(session, since, until, now) -> MetricValue:
    return MetricValue(STATUS_NO_DATA, None, _NO_DATA_STUB)


def _cost(session, since, until, now) -> MetricValue:
    return MetricValue(
        STATUS_NOT_INSTRUMENTED,
        None,
        "no per-unit cost actual is recorded anywhere in the store; only the declared "
        "max_llm_calls ceiling exists. Requires the actuals-capture increment (WS-P2.4 prerequisite).",
    )


def _tokens(session, since, until, now) -> MetricValue:
    return MetricValue(
        STATUS_NOT_INSTRUMENTED,
        None,
        "no token-consumption actual is recorded anywhere in the store; see cost_per_unit.",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -v
```
Expected: PASS (3 collected, 3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): slo_report skeleton with status-typed metrics + cost guard (WS-P2.2)"
```

---

### Task 4: `claim_expiry_rate` + `waiver_frequency`

**Files:**
- Modify: `src/orchestrator/services/slo_report.py` (`_claim_expiry_rate`, `_waiver_frequency`)
- Modify: `tests/services/test_slo_report.py` (add tests + a waiver builder)

**Interfaces:**
- Consumes: `_build_unit`, `_add_claim` (Task 3); `Claim`, `Adjudication` models.
- Produces: filled `_claim_expiry_rate`, `_waiver_frequency`. `value` is a ratio in `[0,1]`. Zero denominator → `no_data`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_slo_report.py`:
```python
from orchestrator.persistence.models import Adjudication
from orchestrator.services.slo_report import STATUS_COMPUTED


def _add_adjudication(session, revision_id, unit_id, *, ac_id, outcome, decided_at,
                      failed_evidence_id=None, event_id=None):
    adj = Adjudication(
        work_package_revision_id=revision_id,
        work_unit_id=unit_id,
        ac_id=ac_id,
        outcome=outcome,
        decided_by="verifier-1",
        decided_at=decided_at,
        rationale="r",
        event_id=event_id or uuid.uuid4(),
        # waived requires failed_evidence_id + non-empty rationale/risk/follow_up (CHECK)
        failed_evidence_id=failed_evidence_id,
        risk="low" if outcome == "waived" else None,
        follow_up="none" if outcome == "waived" else None,
    )
    session.add(adj)
    session.flush()
    return adj


def test_claim_expiry_rate_counts_lease_expired_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "expiry")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    outside = datetime(2026, 6, 1, tzinfo=UTC)
    _add_claim(migrated_session, unit.id, attempt=1, acquired_at=inside, terminal_reason="lease_expired")
    _add_claim(migrated_session, unit.id, attempt=2, acquired_at=inside, terminal_reason=None)
    _add_claim(migrated_session, unit.id, attempt=3, acquired_at=inside, terminal_reason="released")
    _add_claim(migrated_session, unit.id, attempt=4, acquired_at=outside, terminal_reason="lease_expired")
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    # in-window claims: attempts 1,2,3 = 3 total; lease_expired = 1 -> 1/3
    assert report.claim_expiry_rate.status == STATUS_COMPUTED
    assert report.claim_expiry_rate.value == 1 / 3


def test_claim_expiry_rate_no_claims_is_no_data(migrated_session):
    report = slo_report(
        migrated_session,
        SloReportFilters(since=datetime(2026, 7, 1, tzinfo=UTC), until=datetime(2026, 7, 8, tzinfo=UTC)),
    )
    assert report.claim_expiry_rate.status == STATUS_NO_DATA


def test_waiver_frequency_counts_waived_over_adjudications(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    revision, unit = _build_unit(migrated_session, "waiver")
    inside = datetime(2026, 7, 4, tzinfo=UTC)
    _add_adjudication(migrated_session, revision.id, unit.id, ac_id="ac-1", outcome="passed", decided_at=inside)
    _add_adjudication(
        migrated_session, revision.id, unit.id, ac_id="ac-2", outcome="waived",
        decided_at=inside, failed_evidence_id=uuid.uuid4(),
    )
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    # 2 adjudications in window, 1 waived -> 0.5
    assert report.waiver_frequency.status == STATUS_COMPUTED
    assert report.waiver_frequency.value == 0.5
```
Note: `Adjudication.failed_evidence_id` may be a FK to `evidence.id`; if the insert raises a FK violation, create a real evidence row first (see `_seed_evidence` in `tests/services/test_consistency.py`) and pass its id. Confirm the constraint by reading the `Adjudication` model + `ck_adjudications_waiver_fields`.

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "claim_expiry or waiver" -v
```
Expected: FAIL (stubs return no_data).

- [ ] **Step 3: Implement the two helpers**

In `src/orchestrator/services/slo_report.py`, add imports at top:
```python
from sqlalchemy import func, select

from orchestrator.persistence.models import Adjudication, Claim
```
Replace `_claim_expiry_rate` and `_waiver_frequency`:
```python
def _claim_expiry_rate(session, since, until, now) -> MetricValue:
    total = session.scalar(
        select(func.count(Claim.id)).where(Claim.acquired_at >= since, Claim.acquired_at < until)
    ) or 0
    if total == 0:
        return MetricValue(STATUS_NO_DATA, None, "no claims were acquired in the window")
    expired = session.scalar(
        select(func.count(Claim.id)).where(
            Claim.acquired_at >= since,
            Claim.acquired_at < until,
            Claim.terminal_reason == "lease_expired",
        )
    ) or 0
    return MetricValue(
        STATUS_COMPUTED,
        expired / total,
        f"claims acquired in window: {total}; lease_expired: {expired}",
    )


def _waiver_frequency(session, since, until, now) -> MetricValue:
    total = session.scalar(
        select(func.count(Adjudication.id)).where(
            Adjudication.decided_at >= since, Adjudication.decided_at < until
        )
    ) or 0
    if total == 0:
        return MetricValue(STATUS_NO_DATA, None, "no adjudications were decided in the window")
    waived = session.scalar(
        select(func.count(Adjudication.id)).where(
            Adjudication.decided_at >= since,
            Adjudication.decided_at < until,
            Adjudication.outcome == "waived",
        )
    ) or 0
    return MetricValue(
        STATUS_COMPUTED,
        waived / total,
        f"adjudications in window: {total}; waived: {waived}",
    )
```

- [ ] **Step 4: Run to verify pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "claim_expiry or waiver" -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): claim-expiry and waiver-frequency metrics (WS-P2.2)"
```

---

### Task 5: `intake_to_first_work` + `queue_age` (median latencies)

**Files:**
- Modify: `src/orchestrator/services/slo_report.py` (`_intake_to_first_work`, `_queue_age`, add `_median`)
- Modify: `tests/services/test_slo_report.py`

**Interfaces:**
- Consumes: `_build_unit`, `_add_claim`, `_add_event` (Task 3); `WorkPackageRevision`, `WorkUnit`.
- Produces: filled `_intake_to_first_work`, `_queue_age` (value in **seconds**, median), plus a `_median(values: list[float]) -> float` helper.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_slo_report.py`:
```python
def test_intake_to_first_work_median_latency_seconds(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    # Revision registered_at is server-set at register time; to control it, override after build.
    from orchestrator.persistence.models import WorkPackageRevision

    revision, unit = _build_unit(migrated_session, "intake")
    reg_at = datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC)
    migrated_session.get(WorkPackageRevision, revision.id).registered_at = reg_at
    migrated_session.flush()
    # first claim 120s after registration
    _add_claim(migrated_session, unit.id, attempt=1, acquired_at=reg_at + timedelta(seconds=120))
    _add_claim(migrated_session, unit.id, attempt=2, acquired_at=reg_at + timedelta(seconds=300))
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.intake_to_first_work.status == STATUS_COMPUTED
    assert report.intake_to_first_work.value == 120.0  # MIN(acquired_at) - registered_at


def test_queue_age_median_of_ready_units(migrated_session):
    from orchestrator.kernel.states import WorkUnitState

    _, unit = _build_unit(migrated_session, "queue")
    # force the unit into ready and record the ready-entry event
    unit_row = migrated_session.get(WorkUnit, unit.id)
    unit_row.state = WorkUnitState.READY.value
    ready_at = datetime(2026, 7, 5, tzinfo=UTC)
    _add_event(
        migrated_session, unit.id, action="work_unit.transitioned",
        to_state="ready", from_state="draft", occurred_at=ready_at,
    )
    migrated_session.commit()
    now = TransactionClock().now(migrated_session)
    report = slo_report(migrated_session, SloReportFilters(since=datetime(2026, 7, 1, tzinfo=UTC), until=now))
    assert report.queue_age.status == STATUS_COMPUTED
    expected = (now - ready_at).total_seconds()
    assert abs(report.queue_age.value - expected) < 5  # within a few seconds
```
Note: directly setting `unit_row.state` sidesteps the lifecycle guard; that is acceptable for a read-projection fixture (we test the read, not the write path). If a `state` CHECK constraint rejects a raw string, use `WorkUnitState.READY.value`. Confirm the `WorkUnit.state` column stores the enum `.value` string (it does — `status_ledger` compares `WorkUnit.state == "ready"`).

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "intake_to_first or queue_age" -v
```
Expected: FAIL.

- [ ] **Step 3: Implement the helpers + median**

In `slo_report.py` add imports:
```python
from statistics import median

from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
```
Add the median helper (near the bottom, above `_cost`):
```python
def _median(values: list[float]) -> float:
    return float(median(values))
```
Replace `_intake_to_first_work` and `_queue_age`:
```python
def _intake_to_first_work(session, since, until, now) -> MetricValue:
    revisions = session.scalars(
        select(WorkPackageRevision).where(
            WorkPackageRevision.registered_at >= since,
            WorkPackageRevision.registered_at < until,
        )
    ).all()
    if not revisions:
        return MetricValue(STATUS_NO_DATA, None, "no package revisions were registered in the window")
    latencies: list[float] = []
    pending = 0
    for revision in revisions:
        first_claim = session.scalar(
            select(func.min(Claim.acquired_at))
            .join(WorkUnit, WorkUnit.id == Claim.work_unit_id)
            .where(WorkUnit.work_package_revision_id == revision.id)
        )
        if first_claim is None:
            pending += 1
            continue
        latencies.append((first_claim - revision.registered_at).total_seconds())
    if not latencies:
        return MetricValue(
            STATUS_NO_DATA, None,
            f"{len(revisions)} revisions registered in window, none has a first claim yet",
        )
    return MetricValue(
        STATUS_COMPUTED,
        _median(latencies),
        f"median seconds intake->first-claim over {len(latencies)} revisions "
        f"({pending} registered-but-unclaimed excluded)",
    )


def _queue_age(session, since, until, now) -> MetricValue:
    ready_units = session.scalars(select(WorkUnit).where(WorkUnit.state == "ready")).all()
    if not ready_units:
        return MetricValue(STATUS_NO_DATA, None, "no work units are currently in the ready state")
    ages: list[float] = []
    for unit in ready_units:
        entered = session.scalar(
            select(func.max(Event.occurred_at)).where(
                Event.subject_type == "work_unit",
                Event.subject_id == unit.id,
                Event.to_state == "ready",
            )
        )
        if entered is not None:
            ages.append((now - entered).total_seconds())
    if not ages:
        return MetricValue(
            STATUS_NO_DATA, None,
            "ready units exist but none has a recorded ready-entry transition event",
        )
    return MetricValue(
        STATUS_COMPUTED,
        _median(ages),
        f"median seconds in ready over {len(ages)} units currently queued",
    )
```
(If `Event` / `WorkUnit` / `WorkPackageRevision` are already imported from Task 4's edits, merge rather than duplicate the import lines.)

- [ ] **Step 4: Run to verify pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "intake_to_first or queue_age" -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): intake->first-work and queue-age median latencies (WS-P2.2)"
```

---

### Task 6: `revert_rate` + `evidence_completeness`

**Files:**
- Modify: `src/orchestrator/services/slo_report.py` (`_revert_rate`, `_evidence_completeness`)
- Modify: `tests/services/test_slo_report.py`

**Interfaces:**
- Consumes: `_build_unit`, `_add_event`, `_add_adjudication` (Tasks 3-4); `required_ac_ids` (`orchestrator.services.lifecycle`); `_SATISFIED_ACS` (`orchestrator.services.consistency`).
- Produces: filled `_revert_rate` (status `partial` when computed — release-revert blind spot), `_evidence_completeness` (ratio in `[0,1]`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_slo_report.py`:
```python
from orchestrator.services.slo_report import STATUS_PARTIAL


def test_revert_rate_is_partial_with_release_revert_blind_spot(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "revert")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    # two submits, one revert (revision_required from submitted)
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="submitted", from_state="executing", occurred_at=inside)
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="submitted", from_state="executing", occurred_at=inside + timedelta(hours=1))
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="revision_required", from_state="submitted", occurred_at=inside + timedelta(hours=2))
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.revert_rate.status == STATUS_PARTIAL
    assert report.revert_rate.value == 0.5  # 1 revert / 2 submits
    assert "release-revert" in report.revert_rate.basis


def test_evidence_completeness_ratio(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    revision, unit = _build_unit(migrated_session, "complete", enforcement={"acceptance_criteria": ["ac-1", "ac-2"]})
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    # a transition in-window makes the unit "active in window"
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="executing", from_state="claimed", occurred_at=inside)
    # satisfy ac-1 only (passed); ac-2 unsatisfied
    _add_adjudication(migrated_session, revision.id, unit.id, ac_id="ac-1", outcome="passed", decided_at=inside)
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.evidence_completeness.status == STATUS_COMPUTED
    assert report.evidence_completeness.value == 0.5  # 1 of 2 required satisfied
```
Note: `test_evidence_completeness_ratio` depends on `required_ac_ids` returning `("ac-1","ac-2")` from the revision's `enforcement_snapshot` when there is no approved decomposition. Add a first assertion inside the test to make it self-checking:
```python
    from orchestrator.services.lifecycle import required_ac_ids
    assert set(required_ac_ids(migrated_session, revision, migrated_session.get(WorkUnit, unit.id))) == {"ac-1", "ac-2"}
```
If `required_ac_ids` returns something else (e.g. `None`), read its body + `_packagerequired_ac_ids` to learn the exact `enforcement_snapshot` shape it reads, and adjust the `enforcement=` argument accordingly.

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "revert_rate or evidence_completeness" -v
```
Expected: FAIL.

- [ ] **Step 3: Implement the helpers**

In `slo_report.py` add imports:
```python
from orchestrator.services.consistency import _SATISFIED_ACS
from orchestrator.services.lifecycle import required_ac_ids
```
Replace `_revert_rate` and `_evidence_completeness`:
```python
_REVERT_STATES = ("revision_required", "failed")
_REVERT_SOURCES = ("submitted", "verifying", "awaiting_review")


def _revert_rate(session, since, until, now) -> MetricValue:
    submits = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == "work_unit.transitioned",
            Event.to_state == "submitted",
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
    ) or 0
    if submits == 0:
        return MetricValue(STATUS_NO_DATA, None, "no submit transitions occurred in the window")
    reverts = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == "work_unit.transitioned",
            Event.to_state.in_(_REVERT_STATES),
            Event.from_state.in_(_REVERT_SOURCES),
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
    ) or 0
    return MetricValue(
        STATUS_PARTIAL,
        reverts / submits,
        f"code reverts (to revision_required/failed after submit): {reverts}; submits: {submits}. "
        "PARTIAL: release-revert is not recorded as an explicit fact (divergence detection only).",
    )


def _evidence_completeness(session, since, until, now) -> MetricValue:
    active_ids = session.scalars(
        select(Event.subject_id)
        .where(
            Event.action == "work_unit.transitioned",
            Event.subject_type == "work_unit",
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
        .distinct()
    ).all()
    if not active_ids:
        return MetricValue(STATUS_NO_DATA, None, "no work units had transitions in the window")
    total_required = 0
    total_satisfied = 0
    considered = 0
    skipped = 0
    for unit_id in active_ids:
        unit = session.get(WorkUnit, unit_id)
        if unit is None:
            continue
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            continue
        required = required_ac_ids(session, revision, unit)
        if required is None or not required:
            skipped += 1
            continue
        considered += 1
        satisfied = set(session.scalars(_SATISFIED_ACS, {"unit_id": unit.id, "now": now}))
        total_required += len(required)
        total_satisfied += len(set(required) & satisfied)
    if total_required == 0:
        return MetricValue(
            STATUS_NO_DATA, None,
            f"{len(active_ids)} active units in window, none has required acceptance criteria",
        )
    return MetricValue(
        STATUS_COMPUTED,
        total_satisfied / total_required,
        f"satisfied {total_satisfied}/{total_required} required criteria over {considered} units "
        f"({skipped} without required criteria excluded)",
    )
```

- [ ] **Step 4: Run to verify pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k "revert_rate or evidence_completeness" -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): revert-rate (partial) and evidence-completeness metrics (WS-P2.2)"
```

---

### Task 7: `improvisation` count in the report

**Files:**
- Modify: `src/orchestrator/services/slo_report.py` (`_improvisation`)
- Modify: `tests/services/test_slo_report.py`

**Interfaces:**
- Consumes: `Event.improvisation` (Task 1-2); `_add_event`, `_build_unit` (Task 3).
- Produces: filled `_improvisation`. Distinguishes "0 overrides in an active window" (`computed`, value `0.0`) from "no activity" (`no_data`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_slo_report.py`:
```python
def test_improvisation_counts_flagged_events_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "improv")
    inside = datetime(2026, 7, 3, tzinfo=UTC)
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="cancelled", from_state="executing", occurred_at=inside, improvisation=True)
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="executing", from_state="claimed", occurred_at=inside, improvisation=False)
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="cancelled", from_state="executing",
               occurred_at=datetime(2026, 6, 1, tzinfo=UTC), improvisation=True)  # out of window
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.improvisation.status == STATUS_COMPUTED
    assert report.improvisation.value == 1.0  # one flagged, in-window


def test_improvisation_zero_overrides_but_active_is_computed_zero(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "improv-zero")
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="executing", from_state="claimed", occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
               improvisation=False)
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.improvisation.status == STATUS_COMPUTED
    assert report.improvisation.value == 0.0


def test_improvisation_no_activity_is_no_data(migrated_session):
    report = slo_report(
        migrated_session,
        SloReportFilters(since=datetime(2026, 7, 1, tzinfo=UTC), until=datetime(2026, 7, 8, tzinfo=UTC)),
    )
    assert report.improvisation.status == STATUS_NO_DATA
```

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -k improvisation -v
```
Expected: FAIL.

- [ ] **Step 3: Implement the helper**

Replace `_improvisation` in `slo_report.py`:
```python
def _improvisation(session, since, until, now) -> MetricValue:
    total_transitions = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == "work_unit.transitioned",
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
    ) or 0
    if total_transitions == 0:
        return MetricValue(STATUS_NO_DATA, None, "no lifecycle transitions occurred in the window")
    overrides = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == "work_unit.transitioned",
            Event.improvisation.is_(True),
            Event.occurred_at >= since,
            Event.occurred_at < until,
        )
    ) or 0
    return MetricValue(
        STATUS_COMPUTED,
        float(overrides),
        f"human operator overrides (cancels + verifier-bypass completes): {overrides} "
        f"of {total_transitions} transitions; designed human gates excluded.",
    )
```

- [ ] **Step 4: Run to verify pass, then the whole slo suite**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -v
```
Expected: PASS — read the collected count (should be ~13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): improvisation override count with honest no-data control (WS-P2.2)"
```

---

### Task 8: `GET /api/v1/slo-report` route + response model

**Files:**
- Modify: `src/orchestrator/api/schemas.py` (add `MetricValueResponse`, `SloReportResponse`)
- Modify: `src/orchestrator/api/routes.py` (import + route)
- Test: `tests/api/test_slo_report_api.py`

**Interfaces:**
- Consumes: `slo_report`, `SloReportFilters`, `SloReport`, `MetricValue` (Task 3).
- Produces: `GET /api/v1/slo-report?since=&until=` returning `SloReportResponse`.

- [ ] **Step 1: Write the failing API test**

Find the existing API test pattern first (`grep -rn "status-ledger" tests/api/`), which shows how the test client is built + authenticated. Create `tests/api/test_slo_report_api.py` mirroring the closest existing read-route test (e.g. `tests/api/test_status_ledger_api.py`). Minimal shape:
```python
def test_slo_report_route_returns_status_typed_metrics(api_client):
    # api_client: reuse whatever authenticated client fixture the status-ledger api test uses.
    response = api_client.get("/api/v1/slo-report")
    assert response.status_code == 200
    body = response.json()
    assert body["cost_per_unit"]["status"] == "not_instrumented"
    assert body["cost_per_unit"]["value"] is None
    assert "since" in body and "until" in body
    assert body["improvisation"]["status"] in {"no_data", "computed"}
```
Copy the exact client/auth fixture import and any headers from the status-ledger API test — do not invent an auth scheme.

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/api/test_slo_report_api.py -v
```
Expected: FAIL (404 — route not defined).

- [ ] **Step 3: Add the response models**

In `src/orchestrator/api/schemas.py`, add (near `StatusLedgerRowResponse`):
```python
class MetricValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    value: float | None
    basis: str


class SloReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    since: datetime
    until: datetime
    intake_to_first_work: MetricValueResponse
    queue_age: MetricValueResponse
    claim_expiry_rate: MetricValueResponse
    waiver_frequency: MetricValueResponse
    revert_rate: MetricValueResponse
    evidence_completeness: MetricValueResponse
    cost_per_unit: MetricValueResponse
    token_consumption: MetricValueResponse
    improvisation: MetricValueResponse
```
Confirm `datetime`, `BaseModel`, `ConfigDict` are already imported at the top of `schemas.py` (they are — used by `StatusLedgerRowResponse`).

- [ ] **Step 4: Add the route**

In `src/orchestrator/api/routes.py`, add to the service-import block (near L205):
```python
from orchestrator.services.slo_report import SloReportFilters, slo_report
```
and to the schema imports add `SloReportResponse`. Then add the route (place it beside `status_ledger_route`, ~L993):
```python
@router.get("/slo-report", response_model=SloReportResponse)
def slo_report_route(
    _actor: ActorDep,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> object:
    return slo_report(session, SloReportFilters(since=since, until=until))
```
Confirm `datetime` is imported in `routes.py` (grep `from datetime import`); if not, add it.

- [ ] **Step 5: Run to verify pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/api/test_slo_report_api.py -v
```
Expected: PASS. FastAPI maps the `SloReport`/`MetricValue` dataclasses to the response models via `from_attributes`, exactly as `status_ledger_route` does.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/api/test_slo_report_api.py
git commit -m "feat(slo): GET /api/v1/slo-report route (WS-P2.2)"
```

---

### Task 9: `orchestrator slo-report` CLI command

**Files:**
- Modify: `src/orchestrator/cli.py` (add `slo-report` command)
- Test: `tests/cli/test_slo_report_cli.py`

**Interfaces:**
- Consumes: `request`, `_run`, `JsonOption` (existing in `cli.py`).
- Produces: `orchestrator slo-report [--since ...] [--until ...] [--json]` → `GET /api/v1/slo-report`.

- [ ] **Step 1: Write the failing CLI test**

Read `tests/cli/test_status_ledger_cli.py` for the exact test idiom (it mocks `request` or the HTTP transport). Create `tests/cli/test_slo_report_cli.py` mirroring it:
```python
# Mirror the mocking approach used in tests/cli/test_status_ledger_cli.py exactly.
# The command must call request("GET", "/api/v1/slo-report...") and pass --since/--until as query params.
```
Write a test asserting: invoking the `slo-report` command with `--since 2026-07-01T00:00:00 --until 2026-07-08T00:00:00` issues `GET /api/v1/slo-report?since=2026-07-01T00:00:00&until=2026-07-08T00:00:00` (order-insensitive), and that `--json` produces JSON output. Copy the runner/mocking fixtures verbatim from the status-ledger CLI test.

- [ ] **Step 2: Run to verify failure**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/cli/test_slo_report_cli.py -v
```
Expected: FAIL (no such command).

- [ ] **Step 3: Add the CLI command**

In `src/orchestrator/cli.py`, add (near the `status-ledger` command, ~L330):
```python
@app.command("slo-report")
def slo_report(
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    json_output: JsonOption = False,
) -> None:
    params: dict[str, str] = {}
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until
    query = f"?{urlencode(params)}" if params else ""
    _run(lambda: request("GET", f"/api/v1/slo-report{query}"), json_output)
```
`urlencode`, `Annotated`, `typer`, `request`, `_run`, `JsonOption` are already imported/defined in `cli.py`.

- [ ] **Step 4: Run to verify pass**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/cli/test_slo_report_cli.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/cli.py tests/cli/test_slo_report_cli.py
git commit -m "feat(slo): orchestrator slo-report CLI command (WS-P2.2)"
```

---

### Task 10: Backlog item, actuals-capture proposal, Wave-1 progress note

**Files:**
- Modify: `PROJECT.md` (Backlog section)
- Create: `docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md`
- Create/Modify: `~/docs/software-delivery-system/` Wave-1 progress note (path per existing convention)

- [ ] **Step 1: Add the cost actuals-capture backlog item**

Use the backlog skill or `portfolio add` (do not hand-edit if the skill is available). The item (P2):
```
- [ ] (P2) Capture per-attempt actual llm_calls/token consumption so cost SLO + WS-P2.4 budget enforcement become computable — added 2026-07-23. Plan: docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md
```
Also add the `Plan:` reference to the WS-P2.2 backlog item itself (if one exists), pointing at `docs/superpowers/plans/2026-07-23-wsp22-slo-observability.md`.

- [ ] **Step 2: Write the actuals-capture proposal**

Create `docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md` — one page: the problem (no actual token/cost is persisted; only the `max_llm_calls` ceiling), the proposed increment (runner and/or orchestrator persists per-attempt actual `llm_calls`/tokens as a new event payload or table), that it is a **new collector** deliberately deferred by WS-P2.2's YAGNI boundary, and that it is the shared prerequisite for both the cost SLO metric and WS-P2.4 budget enforcement. Explicitly out of scope for WS-P2.2.

- [ ] **Step 3: Write the Wave-1 progress note**

In `~/docs/software-delivery-system/`, add a short note (match the existing file/naming convention there) recording: WS-P2.2 shipped the on-demand SLO report (Tier-1 metrics computable; cost/tokens honestly `not_instrumented`; improvisation as a true operator-override count via `events.improvisation`); Wave-1 exit criterion "the SLO report runs" is satisfied once deployed; remaining Wave-1: WS-P2.3, WS-P2.4.

- [ ] **Step 4: Commit**

```bash
git add PROJECT.md docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md
git commit -m "docs(slo): backlog + actuals-capture proposal for WS-P2.2"
```

---

### Task 11: Full-suite gate, code-review, deploy readiness

**Files:** none (verification task)

- [ ] **Step 1: Run the full check gate and READ THE COLLECTED COUNT**

```bash
make check 2>&1 | tail -40
```
Confirm the `collected N items` line shows a real, non-trivial count and that the SLO tests ran. Exit 0 alone is NOT sufficient (exit-5 = no tests collected is swallowed). If Postgres/`SECURITY_STANDARDS_DIR` are missing, the failure is environmental — control against a clean clone before blaming the change.

- [ ] **Step 2: Run the scope guard explicitly**

```bash
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/architecture/test_ws32_scope_guards.py -v
```
Expected: PASS — confirms `slo_report.py` contains no bare `dispatch`/`deploy` prose. If it fails, replace the offending word with a synonym (or add the module to the guard's allowlist only if genuinely warranted).

- [ ] **Step 3: `/code-review` the diff**

Run `/code-review` against the branch diff. Address correctness bugs and simplification opportunities per the repo's standards.

- [ ] **Step 4: Deploy-readiness note**

Remember MERGED ≠ DEPLOYED: `GET /api/v1/slo-report` will 404 on `sds.alobar.net` until the image is rebuilt (amd64/multi-arch) and redeployed. The report can be run read-only against production's live event store with the SYSTEM M2M bearer to sanity-check real numbers before claiming production works. Deployment itself is a separate, Devon-gated step.

- [ ] **Step 5: Independent adversarial review**

Before merge, hand the branch to a fresh reviewer session with no stake in the implementation (per the WS-P2.15/16 lesson that adversarial review catches shipped-would-halt bugs). Every metric's fixture-verified expected value is the evidence.
