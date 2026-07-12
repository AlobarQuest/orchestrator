# WS-P2.1 Implementation Plan — Tasks 10–13

> **Canonical cross-task contracts honored by this section** (binding; they override anything drafted independently):
> 1. `record_reconciliation_condition(session, command: ConditionCommand) -> ConditionOutcome | DomainError`, where `ConditionOutcome` is a frozen dataclass `(condition: ReconciliationCondition, suppressed: bool)`. Callers construct a `ConditionCommand` and unwrap `.condition`.
> 2. `signature_failure_count(session, unit_id, signature) -> int` and `circuit_open(count: int, threshold: int) -> bool`. Dispatch calls `circuit_open(count + 1, threshold)` (**prospective**); the dead-letter view calls `circuit_open(count, threshold)` (**at rest**).
> 3. The evidence head index is **`uq_evidence_unsuperseded_head`** — a partial unique index on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL`.
> 4. The migration is **`0014_wsp21_recovery_controls`** (Task 1). Tasks 10–13 add **no** migration.
> 5. The GET route inventory pin (`test_production_get_route_inventory_is_explicit`) is **created in Task 10** below. Task 14 later extends its literal set with `/api/v1/in-flight-units`.
> 6. The resolution route is **`/review/reconciliation/conditions/{condition_id}/resolution`** (HUMAN, `/review` router; added by Task 5b).
> 7. Advisory-lock namespaces: reconciliation = `0x57503231`, evidence head = `0x57503232` (they must not collide, and neither collides with `observations.py:30` = `0x57533631`, `deployment_observations.py:28` = `0x57533533`, `release_artifacts.py:23` = `0x57533532`).
> 8. There is no `services/evidence_recovery.py` — `recover_evidence` lives in **`services/evidence.py`**.
>
> **Grounding carried into every task:**
> - `DomainError(code, message, recovery, *, current_state=None, current_version=None)` — `src/orchestrator/errors.py:1-16`. `main.py:37-50` maps it: `*_not_found` → 404, `{"role_forbidden","human_actor_required","csrf_rejected"}` → 403, else → 409.
> - Test headers/fixtures: `HUMAN` / `WORKER` / `SYSTEM` / `VERIFIER` / `AUTHORITY` at `tests/api/test_lifecycle_api.py:59-69`; `db_client` + `migrated_engine` at `tests/api/conftest.py:102-114`; service-level `migrated_session` at `tests/services/conftest.py:27-32`.

---

### Task 10: Dead-letter view (AC-005)

**Files:**
- Create: `src/orchestrator/services/dead_letter.py`
- Modify: `src/orchestrator/services/lifecycle.py` (add `require_operator_actor`, after `ActorContext` at `:38-41`)
- Modify: `src/orchestrator/api/schemas.py` (append `DeadLetterEntryResponse`)
- Modify: `src/orchestrator/api/routes.py` (import block `:89-181`; new GET route beside `status_ledger_route` at `:826-843`)
- Modify: `src/orchestrator/cli.py` (new `@app.command("dead-letter")` after `status_ledger` at `:329-347`)
- Modify: `tests/architecture/test_scope_guards.py` (**create** the pinned GET inventory — today only POST is pinned, at `:37-82`)
- Test: `tests/services/test_dead_letter.py`, `tests/api/test_dead_letter_api.py`, `tests/cli/test_dead_letter_cli.py`

**Interfaces:**
- Consumes: `WorkUnit` (`state`, `attempt_count`, `max_attempts`, `unit_key` — `models.py:186-216`), `Claim.terminal_reason` (`models.py:280`), `DispatchRecord` (`status`, `reason_code`, `failure_signature` — `models.py:448-478`), `dispatch.signature_failure_count`, `dispatch.circuit_open`, `Settings.dispatch_failure_signature_threshold` (`config.py:27`), `ActorContext`, `ActorRole`.
- Produces: `DeadLetterEntry` (frozen dataclass), `dead_letter(session, *, failure_signature_threshold: int) -> tuple[DeadLetterEntry, ...]`, `lifecycle.require_operator_actor(actor: ActorContext) -> None`, `GET /api/v1/dead-letter`, `orchestrator dead-letter`.

#### Steps

- [ ] **Failing test — the at-rest vs prospective breaker off-by-one.** This is the load-bearing test of the task: it proves the view does not open a breaker one failure early. Create `tests/services/test_dead_letter.py`:

```python
import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import DispatchRecord, Event, WorkUnit
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.dispatch import circuit_open, failure_signature, signature_failure_count
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_dependencies import register_unit

SIGNATURE = failure_signature("workflow_dispatch", "github_api", "status:500")
THRESHOLD = 3


def _fail_dispatch(session: Session, unit: WorkUnit, attempt: int, status: str) -> DispatchRecord:
    event = Event(
        actor_id="system",
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=f"dl-dispatch-{unit.id}-{attempt}:event",
    )
    session.add(event)
    session.flush()
    record = DispatchRecord(
        work_unit_id=unit.id,
        work_package_revision_id=unit.work_package_revision_id,
        runner_attempt=attempt,
        status=status,
        reason_code="github_api",
        idempotency_key=f"dl-dispatch-{unit.id}-{attempt}",
        target_repository="AlobarQuest/orchestrator",
        workflow_id="factory-runner-pilot.yml",
        workflow_ref="main",
        failure_signature=SIGNATURE,
        payload={},
        event_id=event.id,
    )
    session.add(record)
    session.flush()
    return record


def test_at_rest_breaker_is_prospective_minus_exactly_one_failure(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "dl-breaker")
    unit.state = WorkUnitState.FAILED
    for attempt in (1, 2):
        _fail_dispatch(migrated_session, unit, attempt, "failed")
    migrated_session.commit()

    count = signature_failure_count(migrated_session, unit.id, SIGNATURE)
    assert count == THRESHOLD - 1
    # The live dispatch predicate counts the failure it is ABOUT to write; the view counts
    # only what is already at rest. Reusing the prospective call would show the breaker open
    # one failure early — this is exactly that one-failure gap.
    assert circuit_open(count + 1, THRESHOLD) is True
    assert circuit_open(count, THRESHOLD) is False

    breakers = [
        entry
        for entry in dead_letter(migrated_session, failure_signature_threshold=THRESHOLD)
        if entry.source == "circuit_breaker"
    ]
    assert breakers == []

    _fail_dispatch(migrated_session, unit, 3, "blocked")
    migrated_session.commit()
    breakers = [
        entry
        for entry in dead_letter(migrated_session, failure_signature_threshold=THRESHOLD)
        if entry.source == "circuit_breaker"
    ]
    assert [(entry.work_unit_id, entry.reason_code, entry.detail) for entry in breakers] == [
        (unit.id, "failure_signature_circuit_open", SIGNATURE)
    ]


def test_dead_letter_lists_failed_blocked_and_cancelled_units_and_dispatch_records(
    migrated_session: Session,
) -> None:
    failed = register_unit(migrated_session, "dl-failed")
    failed.state = WorkUnitState.FAILED
    blocked = register_unit(migrated_session, "dl-blocked")
    blocked.state = WorkUnitState.BLOCKED
    cancelled = register_unit(migrated_session, "dl-cancelled")
    cancelled.state = WorkUnitState.CANCELLED
    _fail_dispatch(migrated_session, failed, 1, "failed")
    migrated_session.commit()

    entries = dead_letter(migrated_session, failure_signature_threshold=THRESHOLD)
    units = {entry.work_unit_id: entry for entry in entries if entry.source == "work_unit"}
    assert set(units) == {failed.id, blocked.id, cancelled.id}
    assert units[blocked.id].requeue_eligible is True
    assert units[cancelled.id].requeue_eligible is False
    dispatches = [entry for entry in entries if entry.source == "dispatch_record"]
    assert [(entry.work_unit_id, entry.reason_code) for entry in dispatches] == [
        (failed.id, "github_api")
    ]


def test_dead_letter_requires_an_operator_actor() -> None:
    from orchestrator.errors import DomainError
    from orchestrator.services.lifecycle import require_operator_actor

    with pytest.raises(DomainError) as error:
        require_operator_actor(ActorContext("worker", ActorRole.WORKER))
    assert error.value.code == "role_forbidden"
```

- [ ] **Run — expect failure.** `.venv/bin/pytest tests/services/test_dead_letter.py` → `ModuleNotFoundError: No module named 'orchestrator.services.dead_letter'`.

- [ ] **Minimal impl — the operator guard.** In `src/orchestrator/services/lifecycle.py`, immediately after the `ActorContext` dataclass (`:38-41`):

```python
def require_operator_actor(actor: ActorContext) -> None:
    """Read surfaces that expose failure signatures and divergence are operator-only.

    SYSTEM is the M2M lane (`/api`), HUMAN is the review lane (`/review`); a worker or a
    verifier credential has no business enumerating another unit's failure signatures.
    """
    if actor.role not in {ActorRole.SYSTEM, ActorRole.HUMAN}:
        raise DomainError("role_forbidden", "only an operator may read this surface", None)
```

- [ ] **Minimal impl — the view.** Create `src/orchestrator/services/dead_letter.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Claim, DispatchRecord, WorkUnit
from orchestrator.services.dispatch import circuit_open

DEAD_LETTER_UNIT_STATES = ("failed", "blocked", "cancelled")
DEAD_LETTER_DISPATCH_STATUSES = ("failed", "blocked")
REQUEUE_STATES = ("failed", "blocked")


@dataclass(frozen=True)
class DeadLetterEntry:
    source: str
    work_unit_id: uuid.UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None


def dead_letter(
    session: Session,
    *,
    failure_signature_threshold: int,
) -> tuple[DeadLetterEntry, ...]:
    """Live-derived from the source tables — no materialized dead-letter queue exists.

    Read-only: this function performs no write, no transition, and no commit.
    """
    return (
        *_terminal_units(session),
        *_failed_dispatch_records(session),
        *_open_circuit_breakers(session, failure_signature_threshold),
    )


def _terminal_units(session: Session) -> tuple[DeadLetterEntry, ...]:
    units = session.scalars(
        select(WorkUnit)
        .where(WorkUnit.state.in_(DEAD_LETTER_UNIT_STATES))
        .order_by(WorkUnit.unit_key, WorkUnit.id)
    ).all()
    return tuple(_unit_entry(session, unit) for unit in units)


def _unit_entry(session: Session, unit: WorkUnit) -> DeadLetterEntry:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id)
        .order_by(Claim.attempt.desc(), Claim.acquired_at.desc(), Claim.id.desc())
        .limit(1)
    )
    return DeadLetterEntry(
        source="work_unit",
        work_unit_id=unit.id,
        unit_key=unit.unit_key,
        unit_state=unit.state,
        reason_code=claim.terminal_reason if claim is not None else None,
        detail=str(claim.id) if claim is not None else None,
        attempt_count=unit.attempt_count,
        max_attempts=unit.max_attempts,
        requeue_eligible=_requeue_eligible(unit),
        occurred_at=claim.released_at if claim is not None else None,
    )


def _failed_dispatch_records(session: Session) -> tuple[DeadLetterEntry, ...]:
    rows = session.execute(
        select(DispatchRecord, WorkUnit)
        .join(WorkUnit, WorkUnit.id == DispatchRecord.work_unit_id)
        .where(DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES))
        .order_by(WorkUnit.unit_key, DispatchRecord.runner_attempt, DispatchRecord.id)
    ).all()
    return tuple(
        DeadLetterEntry(
            source="dispatch_record",
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            unit_state=unit.state,
            reason_code=record.reason_code,
            detail=record.failure_signature,
            attempt_count=unit.attempt_count,
            max_attempts=unit.max_attempts,
            requeue_eligible=_requeue_eligible(unit),
            occurred_at=None,
        )
        for record, unit in rows
    )


def _open_circuit_breakers(
    session: Session,
    failure_signature_threshold: int,
) -> tuple[DeadLetterEntry, ...]:
    # `_opens_circuit` is PROSPECTIVE — it counts the failure about to be written
    # (`dispatch.py:397`, `len(failures) + 1 >= threshold`). At rest the failure is already
    # written, so the shared predicate is called with the AT-REST count, never `count + 1`.
    grouped = session.execute(
        select(
            DispatchRecord.work_unit_id,
            DispatchRecord.failure_signature,
            func.count().label("failures"),
        )
        .where(
            DispatchRecord.failure_signature.is_not(None),
            DispatchRecord.status.in_(DEAD_LETTER_DISPATCH_STATUSES),
        )
        .group_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
        .order_by(DispatchRecord.work_unit_id, DispatchRecord.failure_signature)
    ).all()
    entries: list[DeadLetterEntry] = []
    for unit_id, signature, failures in grouped:
        if not circuit_open(failures, failure_signature_threshold):
            continue
        unit = session.get(WorkUnit, unit_id)
        if unit is None:
            continue
        entries.append(
            DeadLetterEntry(
                source="circuit_breaker",
                work_unit_id=unit.id,
                unit_key=unit.unit_key,
                unit_state=unit.state,
                reason_code="failure_signature_circuit_open",
                detail=signature,
                attempt_count=unit.attempt_count,
                max_attempts=unit.max_attempts,
                requeue_eligible=_requeue_eligible(unit),
                occurred_at=None,
            )
        )
    return tuple(entries)


def _requeue_eligible(unit: WorkUnit) -> bool:
    return unit.state in REQUEUE_STATES and unit.attempt_count < unit.max_attempts
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/services/test_dead_letter.py -q` → 3 passed.

- [ ] **Failing test — the route and its pinned GET inventory.** Create `tests/api/test_dead_letter_api.py`:

```python
from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import SYSTEM, VERIFIER, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def test_dead_letter_lists_a_failed_unit(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client, "dead-letter")
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "dl-claim", "expected_version": 2},
    )
    assert claim.status_code == 200
    failed = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/fail",
        headers=WORKER,
        json={
            "idempotency_key": "dl-fail",
            "expected_version": 3,
            "attempt": claim.json()["attempt"],
            "lease_token": claim.json()["lease_token"],
            "reason": "runner crashed",
        },
    )
    assert failed.status_code == 200

    response = db_client.get("/api/v1/dead-letter", headers=SYSTEM)
    assert response.status_code == 200
    entries = [entry for entry in response.json() if entry["work_unit_id"] == unit_id]
    assert entries
    assert entries[0]["source"] == "work_unit"
    assert entries[0]["unit_state"] == "failed"
    assert entries[0]["requeue_eligible"] is True


def test_dead_letter_is_operator_only(db_client: TestClient) -> None:
    assert db_client.get("/api/v1/dead-letter", headers=WORKER).status_code == 403
    assert db_client.get("/api/v1/dead-letter", headers=VERIFIER).status_code == 403
```

And **create** the pinned GET inventory in `tests/architecture/test_scope_guards.py` (append after `test_production_post_route_inventory_is_explicit`, `:37-82`). The existing inventory pins POST only, so a new GET route would slip in unnoticed — this test is what makes "added deliberately" true:

```python
def test_production_get_route_inventory_is_explicit() -> None:
    # This pin is created by WS-P2.1 Task 10 (AC-005). Task 14 (AC-009) extends the literal
    # set below with "/api/v1/in-flight-units" — the runner's read surface.
    paths = create_app().openapi()["paths"]
    observed = {path for path, operations in paths.items() if "get" in operations}
    observed.update(
        route.path
        for route in web_router.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    )
    assert observed == {
        "/health/live",
        "/health/ready",
        "/api/v1/package-intakes/{revision_id}",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/decomposition-proposals/{proposal_id}",
        "/api/v1/observations",
        "/api/v1/event-publications",
        "/api/v1/knowledge-promotion-proposals",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/status-ledger",
        "/api/v1/dead-letter",
        "/api/v1/work-units/{unit_id}/context-snapshots",
        "/api/v1/work-units/{unit_id}/evidence",
        "/api/v1/work-units/{unit_id}/history",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        "/api/v1/work-units/{unit_id}/readiness",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        "/api/v1/work-units/{unit_id}/runner-brief",
        "/review",
        "/review/units/{unit_id}",
        "/review/units/{unit_id}/evidence-pack",
        "/review/intakes/{revision_id}",
        "/review/decomposition-proposals/{proposal_id}",
    }
```

> If Tasks 1–9 already added GET routes (e.g. a reconciliation-conditions read surface), the first run of this test will fail with those paths in the diff — add them to this literal in **this** commit. That failure is the gate working, not a defect.

- [ ] **Run — expect failure.** `.venv/bin/pytest tests/api/test_dead_letter_api.py tests/architecture/test_scope_guards.py -q` → 404 on `/api/v1/dead-letter`, and the GET inventory asserts a missing `/api/v1/dead-letter`.

- [ ] **Minimal impl — schema.** Append to `src/orchestrator/api/schemas.py`:

```python
class DeadLetterEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    work_unit_id: UUID
    unit_key: str
    unit_state: str
    reason_code: str | None
    detail: str | None
    attempt_count: int
    max_attempts: int
    requeue_eligible: bool
    occurred_at: datetime | None
```

- [ ] **Minimal impl — route.** In `src/orchestrator/api/routes.py`, add `DeadLetterEntryResponse` to the schema import block, add `from orchestrator.services.dead_letter import dead_letter`, add `require_operator_actor` to the existing `orchestrator.services.lifecycle` import (`:148-153`), and add the route beside `status_ledger_route` (`:826`):

```python
@router.get("/dead-letter", response_model=list[DeadLetterEntryResponse])
def dead_letter_route(
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    require_operator_actor(actor)
    return dead_letter(
        session,
        failure_signature_threshold=settings.dispatch_failure_signature_threshold,
    )
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/api/test_dead_letter_api.py tests/architecture/test_scope_guards.py -q`.

- [ ] **Failing test — CLI.** Create `tests/cli/test_dead_letter_cli.py`, mirroring `tests/cli/test_status_ledger_cli.py`:

```python
import json

from typer.testing import CliRunner

from orchestrator import cli


def test_dead_letter_cli_emits_json(db_client, monkeypatch) -> None:
    monkeypatch.setattr(cli, "HTTP_TRANSPORT", db_client._transport)
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "https://testserver")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "system-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "system-key")

    result = CliRunner().invoke(cli.app, ["dead-letter", "--json"])

    assert result.exit_code == 0
    assert isinstance(json.loads(result.stdout), list)
```

- [ ] **Minimal impl — CLI.** In `src/orchestrator/cli.py`, after `status_ledger` (`:347`):

```python
@app.command("dead-letter")
def dead_letter(json_output: JsonOption = False) -> None:
    _run(lambda: request("GET", "/api/v1/dead-letter"), json_output)
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/cli/test_dead_letter_cli.py -q`.

- [ ] **Commit.**
```bash
git add src/orchestrator/services/dead_letter.py src/orchestrator/services/lifecycle.py src/orchestrator/api/routes.py src/orchestrator/api/schemas.py src/orchestrator/cli.py tests/services/test_dead_letter.py tests/api/test_dead_letter_api.py tests/cli/test_dead_letter_cli.py tests/architecture/test_scope_guards.py && git commit -m "AC-005: live-derived dead-letter view with an at-rest circuit-breaker predicate

The breaker predicate is shared with dispatch but called with the at-rest failure
count, never the prospective count+1 — reusing dispatch's call site would report a
breaker open one failure early. Creates the pinned GET route inventory, which
previously pinned POST only."
```

---

### Task 11: `requeue` — the one genuinely new recovery action (AC-006)

`retry` already exists twice (`/review/units/{unit_id}/retry` → `web.py:551-577`; `POST /api/v1/work-units/{unit_id}/retry-authorization` → `routes.py:1085-1092`), both pinned at `tests/architecture/test_scope_guards.py:68,78`. **Adding a third retry route would fail the pinned-route test.** `requeue` covers the disjoint case retry does not: attempts *not* exhausted.

**Files:**
- Modify: `src/orchestrator/services/claims.py` — add `requeue_unit` after `authorize_retry` (`:298-371`); rename `_reclaim_eligibility_error` (`:464-473`) → `_readiness_eligibility_error` and update its single call site (`:254`)
- Modify: `src/orchestrator/api/schemas.py` (add `RequeueCommand`)
- Modify: `src/orchestrator/api/routes.py` (new POST route beside `retry_authorization`, `:1085`)
- Modify: `src/orchestrator/cli.py` (new `@app.command("requeue")`)
- Modify: `tests/architecture/test_scope_guards.py` (add `/api/v1/work-units/{unit_id}/requeue` to the pinned POST inventory)
- Test: `tests/services/test_requeue.py`, `tests/api/test_requeue_api.py`, `tests/architecture/test_recovery_actions_cannot_complete.py`

**Interfaces:**
- Consumes: `claims._locked_unit`, `claims._transition`, `claims._require_version`, `claims._readiness_eligibility_error` (which wraps `evaluate_readiness` → `packages.py:725`), `claims._idempotency_conflict`, `TransactionClock`, `WorkUnitState`, `ActorRole`, `Event`.
- Produces: `claims.requeue_unit(session, unit_id, actor, *, reason, idempotency_key, expected_version=None) -> WorkUnit | DomainError`; `POST /api/v1/work-units/{unit_id}/requeue`; `orchestrator requeue`.
- Error codes (exact strings, matching existing ones so operators see one vocabulary): `role_forbidden`, `requeue_not_allowed`, `attempts_exhausted` (recovery `approve_retry` — same as `claims.py:67`), `readiness_not_satisfied` (recovery `resolve_readiness` — same as `claims.py:468-472`), `version_conflict`, `idempotency_conflict`.

#### Steps

- [ ] **Failing test — the invisible-and-unrunnable hazard.** This is the reason the guard exists: without the exhaustion refusal the unit lands `READY`, `claim_unit` then rejects it with `attempts_exhausted` (`claims.py:66-67`), and it disappears from the failed-units dead-letter surface — invisible *and* unrunnable. Create `tests/services/test_requeue.py`:

```python
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.claims import claim_unit, requeue_unit
from orchestrator.services.dead_letter import dead_letter
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import DependencySpec
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker", ActorRole.WORKER)


def test_requeue_moves_a_failed_unit_to_ready(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-failed")
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()

    result = requeue_unit(
        migrated_session,
        unit.id,
        SYSTEM,
        reason="runner host died",
        idempotency_key="requeue-1",
    )

    assert isinstance(result, WorkUnit)
    assert result.state == WorkUnitState.READY
    event = migrated_session.scalar(select(Event).where(Event.idempotency_key == "requeue-1"))
    assert event is not None
    assert (event.from_state, event.to_state) == (WorkUnitState.FAILED, WorkUnitState.READY)
    assert event.actor_id == "system"
    assert event.payload["reason"] == "runner host died"


def test_requeue_moves_a_blocked_unit_to_ready(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-blocked")
    unit.state = WorkUnitState.BLOCKED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="blocker cleared", idempotency_key="requeue-2"
    )

    assert isinstance(result, WorkUnit)
    assert result.state == WorkUnitState.READY


def test_requeue_refuses_when_attempts_are_exhausted_and_the_unit_stays_visible(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "requeue-exhausted")
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = unit.max_attempts
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="one more go", idempotency_key="requeue-3"
    )

    assert isinstance(result, DomainError)
    assert result.code == "attempts_exhausted"
    assert result.recovery == "approve_retry"
    migrated_session.refresh(unit)
    # Neither invisible nor unrunnable: it stays FAILED, so it stays in the dead-letter view
    # and `retry` (the exhausted-case action) remains the operator's path.
    assert unit.state == WorkUnitState.FAILED
    visible = {
        entry.work_unit_id
        for entry in dead_letter(migrated_session, failure_signature_threshold=3)
        if entry.source == "work_unit"
    }
    assert unit.id in visible


def test_requeue_refuses_when_readiness_is_not_satisfied(migrated_session: Session) -> None:
    unit = register_unit(
        migrated_session,
        "requeue-unready",
        dependencies=(
            DependencySpec(
                kind="external",
                required_state_or_condition="approved",
                external_ref="CAB-1",
            ),
        ),
    )
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="try again", idempotency_key="requeue-4"
    )

    assert isinstance(result, DomainError)
    assert result.code == "readiness_not_satisfied"
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.FAILED


def test_requeue_requires_the_system_role(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-role")
    unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, WORKER, reason="mine now", idempotency_key="requeue-5"
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_requeue_refuses_a_completed_unit(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-completed")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    result = requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="undo", idempotency_key="requeue-6"
    )

    assert isinstance(result, DomainError)
    assert result.code == "requeue_not_allowed"


def test_requeued_unit_is_claimable(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "requeue-claimable")
    unit.state = WorkUnitState.FAILED
    unit.attempt_count = 1
    migrated_session.commit()
    requeue_unit(
        migrated_session, unit.id, SYSTEM, reason="retry the run", idempotency_key="requeue-7"
    )

    grant = claim_unit(migrated_session, unit.id, WORKER, "requeue-7-claim")

    assert not isinstance(grant, DomainError)
    assert grant.attempt == 2
```

- [ ] **Run — expect failure.** `.venv/bin/pytest tests/services/test_requeue.py -q` → `ImportError: cannot import name 'requeue_unit'`.

- [ ] **Minimal impl — rename the shared readiness/eligibility check.** In `src/orchestrator/services/claims.py`, rename `_reclaim_eligibility_error` → `_readiness_eligibility_error` (`:464`) and update its sole call site (`:254`, inside `_perform_reclaim`). Body unchanged — requeue and reclaim now share one writer of these two refusals:

```python
def _readiness_eligibility_error(session: Session, unit: WorkUnit) -> DomainError | None:
    if unit.attempt_count >= unit.max_attempts:
        return DomainError("attempts_exhausted", "attempt budget is exhausted", "approve_retry")
    if evaluate_readiness(session, unit.id).status is not ReadinessStatus.READY:
        return DomainError(
            "readiness_not_satisfied",
            "work unit is no longer ready after lease expiry",
            "resolve_readiness",
        )
    return None
```

- [ ] **Minimal impl — `requeue_unit`.** Add to `src/orchestrator/services/claims.py` after `authorize_retry` (`:371`):

```python
REQUEUE_SOURCE_STATES = {WorkUnitState.FAILED, WorkUnitState.BLOCKED}


def requeue_unit(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    *,
    reason: str,
    idempotency_key: str,
    expected_version: int | None = None,
) -> WorkUnit | DomainError:
    """SYSTEM recovery for the NOT-exhausted case; `authorize_retry` owns the exhausted one.

    The target state is hardcoded READY. Eligibility is checked BEFORE the transition: a unit
    requeued past its attempt budget would land READY, be rejected by `claim_unit`
    (`attempts_exhausted`), and drop out of the failed-units dead-letter view — invisible and
    unrunnable at once.
    """
    try:
        unit = _locked_unit(session, unit_id)
        replay = _requeue_replay(session, unit, actor, reason, idempotency_key, expected_version)
        if replay is not None:
            session.commit()
            return replay
        if actor.role is not ActorRole.SYSTEM:
            raise DomainError("role_forbidden", "only the system may requeue work", None)
        if expected_version is not None:
            _require_version(unit, expected_version)
        if WorkUnitState(unit.state) not in REQUEUE_SOURCE_STATES:
            raise DomainError(
                "requeue_not_allowed",
                "only failed or blocked work may be requeued",
                None,
                current_state=unit.state,
                current_version=unit.version,
            )
        eligibility_error = _readiness_eligibility_error(session, unit)
        if eligibility_error is not None:
            raise eligibility_error

        _transition(
            session,
            unit,
            WorkUnitState.READY,
            # The actor is attributed; the role is forced SYSTEM for edge authorization —
            # the `claims.py:91` pattern. FAILED->READY and BLOCKED->READY are SYSTEM_EDGES
            # (`transitions.py:20,22`).
            actor=ActorContext(actor.actor_id, ActorRole.SYSTEM),
            idempotency_key=idempotency_key,
            occurred_at=TransactionClock().now(session),
            payload={
                "requeued_by": actor.actor_id,
                "reason": reason,
                "attempt_count": unit.attempt_count,
                "max_attempts": unit.max_attempts,
                "expected_version": expected_version,
            },
        )
        session.commit()
        return unit
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise


def _requeue_replay(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
    expected_version: int | None,
) -> WorkUnit | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    expected = (
        event.action == "work_unit.transitioned"
        and event.subject_id == unit.id
        and event.to_state == WorkUnitState.READY
        and event.payload.get("requeued_by") == actor.actor_id
        and event.payload.get("reason") == reason
        and event.payload.get("expected_version") == expected_version
        and actor.role is ActorRole.SYSTEM
    )
    if not expected:
        raise _idempotency_conflict()
    return unit
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/services/test_requeue.py tests/services -q` (the reclaim suite must stay green through the rename).

- [ ] **Failing test — route + pinned POST inventory.** Create `tests/api/test_requeue_api.py`:

```python
from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def _fail(db_client: TestClient, unit_id: str, suffix: str) -> int:
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": f"rq-claim-{suffix}", "expected_version": 2},
    )
    assert claim.status_code == 200
    failed = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/fail",
        headers=WORKER,
        json={
            "idempotency_key": f"rq-fail-{suffix}",
            "expected_version": 3,
            "attempt": claim.json()["attempt"],
            "lease_token": claim.json()["lease_token"],
            "reason": "runner crashed",
        },
    )
    assert failed.status_code == 200
    return failed.json()["version"]


def test_requeue_returns_the_unit_in_ready(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client, "requeue-ok")
    version = _fail(db_client, unit_id, "ok")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/requeue",
        headers=SYSTEM,
        json={
            "idempotency_key": "rq-requeue-ok",
            "expected_version": version,
            "reason": "runner host died",
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "ready"


def test_requeue_is_system_only(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client, "requeue-role")
    version = _fail(db_client, unit_id, "role")

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/requeue",
        headers=HUMAN,
        json={
            "idempotency_key": "rq-requeue-role",
            "expected_version": version,
            "reason": "let me",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"
```

Add `"/api/v1/work-units/{unit_id}/requeue"` to the pinned POST set in `tests/architecture/test_scope_guards.py:45-82` (beside `"/api/v1/work-units/{unit_id}/retry-authorization"`, `:68`).

- [ ] **Run — expect failure.** 404 on the new path; the pinned POST inventory reports it missing.

- [ ] **Minimal impl — schema + route + CLI.** In `src/orchestrator/api/schemas.py`:

```python
class RequeueCommand(BaseModel):
    idempotency_key: str = Field(min_length=1)
    expected_version: int | None = None
    reason: str = Field(min_length=1)
```

In `src/orchestrator/api/routes.py`, import `RequeueCommand` and add `requeue_unit` to the `orchestrator.services.claims` import (`:89-94`), then beside `retry_authorization` (`:1085`):

```python
@router.post("/work-units/{unit_id}/requeue", response_model=UnitResponse)
def requeue(
    unit_id: UUID,
    body: RequeueCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(requeue_unit(session, unit_id, actor, **body.model_dump()))
```

In `src/orchestrator/cli.py`:

```python
@app.command("requeue")
def requeue(
    unit_id: Annotated[str, typer.Argument()],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    reason: Annotated[str, typer.Option("--reason")],
    expected_version: Annotated[int | None, typer.Option("--expected-version", min=0)] = None,
    json_output: JsonOption = False,
) -> None:
    payload: JsonObject = {"idempotency_key": idempotency_key, "reason": reason}
    if expected_version is not None:
        payload["expected_version"] = expected_version
    _run(
        lambda: request("POST", f"/api/v1/work-units/{unit_id}/requeue", payload),
        json_output,
    )
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/api/test_requeue_api.py tests/architecture/test_scope_guards.py -q`.

- [ ] **Failing test — the "cannot declare completion" proof, stated correctly.** `"no WORKER_EDGES → COMPLETED"` is a **non-sequitur** here: retry and cancel are HUMAN-surfaced, and `HUMAN_EDGES` *does* contain `SUBMITTED/VERIFYING/AWAITING_REVIEW → COMPLETED` (`transitions.py:51,54,55`). The real guarantee is two-fold. Create `tests/architecture/test_recovery_actions_cannot_complete.py`:

```python
import ast
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from orchestrator.errors import DomainError
from orchestrator.kernel.states import LEGAL_EDGES, WorkUnitState
from orchestrator.kernel.transitions import EDGE_ROLES, TransitionGuards, authorize_transition
from orchestrator.main import create_app
from orchestrator.web import router as web_router

RECOVERY_ENTRY_POINTS = {
    ("src/orchestrator/services/claims.py", "requeue_unit"),
    ("src/orchestrator/services/claims.py", "authorize_retry"),
    ("src/orchestrator/web.py", "cancel"),
}
ALLOWED_RECOVERY_TARGETS = {"READY", "CANCELLED"}


def _state_members(path: str, function: str) -> set[str]:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "WorkUnitState"
            }
    raise AssertionError(f"{function} not found in {path}")


@pytest.mark.parametrize(("path", "function"), sorted(RECOVERY_ENTRY_POINTS))
def test_each_recovery_endpoint_hardcodes_its_target_state(path: str, function: str) -> None:
    members = _state_members(path, function)
    assert "COMPLETED" not in members
    assert members & ALLOWED_RECOVERY_TARGETS


def test_every_transition_into_completed_is_gated_by_completion_satisfied() -> None:
    completing = {edge for edge in LEGAL_EDGES if edge[1] is WorkUnitState.COMPLETED}
    assert completing
    for source, target in completing:
        for role in EDGE_ROLES[(source, target)]:
            with pytest.raises(DomainError) as error:
                authorize_transition(
                    source, target, role, TransitionGuards(completion_satisfied=False)
                )
            assert error.value.code == "completion_incomplete"


def test_no_recovery_path_merges_or_waives() -> None:
    claims = Path("src/orchestrator/services/claims.py").read_text()
    tree = ast.parse(claims)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "waived" not in literals
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "Adjudication" not in names

    assert not any("merge" in state.value for state in WorkUnitState)
    paths = set(create_app().openapi()["paths"])
    paths.update(route.path for route in web_router.routes if isinstance(route, APIRoute))
    assert not any("merge" in path for path in paths)
```

- [ ] **Run — expect pass** (this test asserts properties the code already has; it fails only if a future change breaks them — that is its job). `.venv/bin/pytest tests/architecture/test_recovery_actions_cannot_complete.py -q` → 5 passed.

- [ ] **Commit.**
```bash
git add src/orchestrator/services/claims.py src/orchestrator/api/routes.py src/orchestrator/api/schemas.py src/orchestrator/cli.py tests/services/test_requeue.py tests/api/test_requeue_api.py tests/architecture/test_recovery_actions_cannot_complete.py tests/architecture/test_scope_guards.py && git commit -m "AC-006: requeue — SYSTEM FAILED/BLOCKED -> READY for the not-exhausted case

retry already exists twice; requeue is the disjoint action. It refuses when attempts
are exhausted and reuses the shared readiness/eligibility check BEFORE transitioning —
otherwise the unit lands READY, claim_unit rejects it, and it drops out of the
dead-letter view: invisible and unrunnable. Proves 'cannot declare completion' by the
hardcoded per-endpoint target plus the completion_satisfied gate, not by the
non-sequitur that no WORKER edge reaches COMPLETED."
```

---

### Task 12: Duplicate-delivery idempotency matrix (AC-007)

This task **proves** existing idempotency and fills gaps. It invents no new machinery where one exists.

**Files:**
- Create: `tests/idempotency/__init__.py`
- Create: `tests/idempotency/conftest.py` (re-export the Postgres fixtures: `from tests.api.conftest import auth_config, db_client, migrated_engine, migrated_session`)
- Create: `tests/idempotency/matrix.py` (the checked-in coverage matrix — executable, so it cannot rot)
- Create: `tests/idempotency/test_matrix.py` (completeness gate against the pinned POST route inventory)
- Create: `tests/idempotency/test_lifecycle_idempotency.py` (asymmetry #1: unique Event key + `with_for_update`, **no advisory lock**)
- Create: `tests/idempotency/test_reclaim_idempotency.py` (asymmetry #2: the compound `:failed` / `:ready` keys)
- Create: `tests/idempotency/test_ingress_idempotency.py` (every remaining pre-existing ingress)
- Create: `tests/idempotency/test_wsp21_ingress_idempotency.py` (the new WS-P2.1 ingress)

**Interfaces:**
- Consumes: `db_client` / `migrated_engine` (`tests/api/conftest.py:102-114`), `Event.idempotency_key` (globally `unique=True`, `models.py:445`), `observations._lock_idempotency_key` (`observations.py:420-427`) and the same triad in `evidence.py:206,342,453`, `deployment_observations.py:28`, `release_artifacts.py:23`; `reconciliation.ConditionCommand` / `reconciliation.record_reconciliation_condition` → `ConditionOutcome(condition, suppressed)`; `evidence.recover_evidence`; `claims.requeue_unit`.
- Produces: `MatrixRow` + `COVERAGE_MATRIX: tuple[MatrixRow, ...]`.
- **Replay contract note (binding on every claim-family assertion):** `_claim_replay` (`claims.py:503-509`) and `_renew_replay` (`claims.py:562`) return `LeaseGrant(..., lease_token="", ...)` — a replay deliberately does **not** re-issue the lease token. "Identical response" for `claim` / `renew` / `reclaim` therefore means identical `claim_id`, `attempt`, `expires_at`, `context_snapshot_id`, with the token withheld on replay. Asserting byte-identical bodies there would be asserting a credential-reissue bug.

#### Steps

- [ ] **Failing test — the matrix and its completeness gate.** Create `tests/idempotency/matrix.py`:

```python
from dataclasses import dataclass

ADVISORY_LOCK = "pg_advisory_xact_lock + unique idempotency_key + replay-equality"
ROW_LOCK = "unique Event.idempotency_key + WorkUnit row lock (with_for_update), NO advisory lock"
COMPOUND_KEY = "compound `:failed`/`:ready` Event keys + WorkUnit row lock + replay-equality"


@dataclass(frozen=True)
class MatrixRow:
    ingress: str
    route: str | None
    mechanism: str
    test: str


COVERAGE_MATRIX: tuple[MatrixRow, ...] = (
    MatrixRow(
        "lifecycle transition",
        "/api/v1/work-units/{unit_id}/commands/{command}",
        ROW_LOCK,
        "tests/idempotency/test_lifecycle_idempotency.py::test_concurrent_duplicate_transition_writes_one_event",
    ),
    MatrixRow(
        "claim",
        "/api/v1/work-units/{unit_id}/claim",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_claim_double_submit",
    ),
    MatrixRow(
        "renew",
        "/api/v1/work-units/{unit_id}/renew",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_renew_double_submit",
    ),
    MatrixRow(
        "reclaim expired claim",
        "/api/v1/work-units/{unit_id}/reclaim-expired-claim",
        COMPOUND_KEY,
        "tests/idempotency/test_reclaim_idempotency.py::test_reclaim_double_submit_writes_one_failed_and_one_ready_event",
    ),
    MatrixRow(
        "retry authorization",
        "/api/v1/work-units/{unit_id}/retry-authorization",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_retry_authorization_double_submit",
    ),
    MatrixRow(
        "worker evidence",
        "/api/v1/work-units/{unit_id}/evidence",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_worker_evidence_double_submit",
    ),
    MatrixRow(
        "verifier evidence",
        "/api/v1/work-units/{unit_id}/verify",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_verifier_evidence_double_submit",
    ),
    MatrixRow(
        "adjudication",
        "/api/v1/work-units/{unit_id}/adjudications",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_adjudication_double_submit",
    ),
    MatrixRow(
        "observation",
        "/api/v1/observations",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_observation_double_submit",
    ),
    MatrixRow(
        "deployment observation",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_deployment_observation_double_submit",
    ),
    MatrixRow(
        "dispatch",
        "/api/v1/work-units/{unit_id}/dispatch",
        "unique DispatchRecord.idempotency_key + _validate_idempotent_record",
        "tests/idempotency/test_ingress_idempotency.py::test_dispatch_double_submit",
    ),
    MatrixRow(
        "release artifact binding",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_release_artifact_double_submit",
    ),
    MatrixRow(
        "infra-lane link",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_infra_lane_link_double_submit",
    ),
    MatrixRow(
        "knowledge promotion proposal",
        "/api/v1/knowledge-promotion-proposals",
        ADVISORY_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_knowledge_promotion_double_submit",
    ),
    MatrixRow(
        "approval",
        "/api/v1/work-units/{unit_id}/approvals",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_approval_double_submit",
    ),
    MatrixRow(
        "dependency",
        "/api/v1/work-units/{unit_id}/dependencies",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_dependency_double_submit",
    ),
    MatrixRow(
        "dependency resolution",
        "/api/v1/dependencies/{dependency_id}/resolve",
        ROW_LOCK,
        "tests/idempotency/test_ingress_idempotency.py::test_dependency_resolution_double_submit",
    ),
    MatrixRow(
        "reconciliation condition (on-ingest)",
        None,
        f"{ADVISORY_LOCK} (namespace 0x57503231); UNIQUE(work_unit_id, observation_kind,"
        " normalized_divergence_hash); ConditionOutcome.suppressed on a dedup",
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_reconciliation_condition_double_ingest",
    ),
    MatrixRow(
        "reconciliation resolution",
        "/review/reconciliation/conditions/{condition_id}/resolution",
        f"{ADVISORY_LOCK}; UNIQUE(condition_id)",
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_resolution_double_submit",
    ),
    MatrixRow(
        "reconciliation detect pass",
        "/api/v1/reconciliation/detect",
        ADVISORY_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_detect_double_submit",
    ),
    MatrixRow(
        "recover evidence",
        "/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
        f"{ADVISORY_LOCK} (evidence-head namespace 0x57503232);"
        " uq_evidence_unsuperseded_head",
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_recover_evidence_double_submit",
    ),
    MatrixRow(
        "requeue",
        "/api/v1/work-units/{unit_id}/requeue",
        ROW_LOCK,
        "tests/idempotency/test_wsp21_ingress_idempotency.py::test_requeue_double_submit",
    ),
)
```

Create `tests/idempotency/test_matrix.py` — the gate that stops the matrix from rotting. It pins the matrix against the same route inventory `test_scope_guards.py` pins, so any new ingress route must either be covered or explicitly declared non-ingress:

```python
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from orchestrator.main import create_app
from orchestrator.web import router as web_router
from tests.idempotency.matrix import COVERAGE_MATRIX

# Routes that persist no event/evidence/observation of their own: they are governed by the
# ingress they delegate to, or they are pure read/render/human-gate surfaces.
NON_INGRESS_POST_ROUTES = frozenset(
    {
        "/api/v1/package-intakes",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/decomposition-proposals/{proposal_id}/approve",
        "/api/v1/decomposition-proposals/{proposal_id}/reject",
        "/api/v1/decomposition-proposals/{proposal_id}/require-revision",
        "/api/v1/revisions",
        "/api/v1/revisions/{revision_id}/work-units",
        "/api/v1/work-units/{unit_id}/preflight",
        "/api/v1/knowledge-promotion-proposals/{proposal_id}/submit-to-brain",
        "/api/v1/event-publications/queue",
        "/api/v1/event-publications/export",
        "/api/v1/event-publications/{publication_id}/retry",
        "/review/units/{unit_id}/approval",
        "/review/units/{unit_id}/review",
        "/review/units/{unit_id}/cancel",
        "/review/units/{unit_id}/retry",
        "/review/decomposition-proposals/{proposal_id}/approve",
        "/review/decomposition-proposals/{proposal_id}/reject",
        "/review/decomposition-proposals/{proposal_id}/require-revision",
    }
)


def _post_routes() -> set[str]:
    paths = create_app().openapi()["paths"]
    routes = {path for path, operations in paths.items() if "post" in operations}
    routes.update(
        route.path
        for route in web_router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    )
    return routes


def test_every_ingress_post_route_has_a_matrix_row() -> None:
    covered = {row.route for row in COVERAGE_MATRIX if row.route is not None}
    uncovered = _post_routes() - covered - NON_INGRESS_POST_ROUTES
    assert uncovered == set()


@pytest.mark.parametrize("row", COVERAGE_MATRIX, ids=lambda row: row.ingress)
def test_every_matrix_row_names_a_real_test(row) -> None:
    file_path, _, test_name = row.test.partition("::")
    source = Path(file_path).read_text()
    assert f"def {test_name}(" in source
```

- [ ] **Run — expect failure.** `.venv/bin/pytest tests/idempotency -q` → `test_every_matrix_row_names_a_real_test` fails for every row (the test files do not exist yet).

- [ ] **Minimal impl — asymmetry #1: lifecycle transitions have no advisory lock.** `_perform_transition` (`lifecycle.py:91-145`) relies on the `SELECT … FOR UPDATE` on the `WorkUnit` row plus `Event.idempotency_key`'s global `UNIQUE`. A *concurrent* double-submit must therefore serialize on the row lock, not on an advisory lock — this is the surface the design flags as asymmetric, so test it concurrently, not just sequentially. Create `tests/idempotency/test_lifecycle_idempotency.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from tests.services.test_dependencies import register_unit

KEY = "lifecycle-double-submit"


def test_concurrent_duplicate_transition_writes_one_event(
    migrated_engine: Engine, migrated_session: Session
) -> None:
    unit = register_unit(migrated_session, "idem-lifecycle")
    unit.state = WorkUnitState.DRAFT
    migrated_session.commit()
    unit_id, version = unit.id, unit.version
    barrier = Barrier(2)

    def submit() -> tuple[str, int]:
        with Session(migrated_engine) as session:
            barrier.wait(timeout=10)
            result = transition_unit(
                session,
                TransitionCommand(
                    unit_id=unit_id,
                    target=WorkUnitState.READY,
                    actor=ActorContext("system", ActorRole.SYSTEM),
                    expected_version=version,
                    idempotency_key=KEY,
                ),
            )
            return result.state, result.version

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit), pool.submit(submit)]
        results = [future.result(timeout=20) for future in futures]

    # Identical responses, and exactly one row persisted — with NO advisory lock in this path:
    # the WorkUnit row lock (`lifecycle.py:94-96`) serializes the two writers, and the loser
    # finds the Event by its globally-unique key (`lifecycle.py:100-104`) and replays it.
    assert results[0] == results[1] == (WorkUnitState.READY, version + 1)
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.idempotency_key == KEY)
            )
            == 1
        )
        assert session.get(WorkUnit, unit_id).version == version + 1
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/idempotency/test_lifecycle_idempotency.py -q`. If it fails, the asymmetry is a real defect and must be fixed in `lifecycle.py` before proceeding — do **not** weaken the test.

- [ ] **Minimal impl — asymmetry #2: the reclaim compound key.** `_perform_reclaim` writes **three** events under **two** derived keys (`{key}:failed` at `claims.py:270`, `{key}:ready` at `:282`) plus the bare `{key}` for the new claim (`:658`), and the error path replays through `{key}:failed` (`claims.py:584-585`). This is the trickiest replay surface in the repo. Create `tests/idempotency/test_reclaim_idempotency.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event
from orchestrator.services.claims import claim_unit, reclaim_expired_claim
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_dependencies import register_unit

KEY = "reclaim-double-submit"
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker", ActorRole.WORKER)
NEXT = ActorContext("worker-2", ActorRole.WORKER)


def test_reclaim_double_submit_writes_one_failed_and_one_ready_event(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "idem-reclaim")
    unit.state = WorkUnitState.READY
    unit.max_attempts = 5
    migrated_session.commit()
    first = claim_unit(migrated_session, unit.id, WORKER, "idem-reclaim-claim")
    claim = migrated_session.get(Claim, first.claim_id)
    claim.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    migrated_session.commit()

    one = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT, KEY)
    two = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT, KEY)

    # Identical response — except the lease token, which a replay deliberately does NOT
    # re-issue (`_claim_replay`, claims.py:503-509). Asserting byte-identical bodies here
    # would be asserting a credential-reissue bug.
    assert (one.claim_id, one.attempt, one.expires_at) == (
        two.claim_id,
        two.attempt,
        two.expires_at,
    )
    assert one.lease_token != ""
    assert two.lease_token == ""

    counts = {
        key: migrated_session.scalar(
            select(func.count()).select_from(Event).where(Event.idempotency_key == key)
        )
        for key in (f"{KEY}:failed", f"{KEY}:ready", KEY)
    }
    assert counts == {f"{KEY}:failed": 1, f"{KEY}:ready": 1, KEY: 1}
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Claim).where(Claim.idempotency_key == KEY)
        )
        == 1
    )
    migrated_session.refresh(unit)
    assert unit.attempt_count == 2


def test_reclaim_error_replay_returns_the_same_domain_error(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "idem-reclaim-exhausted")
    unit.state = WorkUnitState.READY
    unit.max_attempts = 1
    migrated_session.commit()
    first = claim_unit(migrated_session, unit.id, WORKER, "idem-reclaim-x-claim")
    claim = migrated_session.get(Claim, first.claim_id)
    claim.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    migrated_session.commit()

    one = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT, "reclaim-x")
    two = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, NEXT, "reclaim-x")

    # The `:failed` event is the ONLY record of the refusal — the compound key is what makes
    # a duplicate delivery of a *rejected* reclaim replay the same error instead of a 500.
    assert (one.code, two.code) == ("attempts_exhausted", "attempts_exhausted")
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.idempotency_key == "reclaim-x:failed")
        )
        == 1
    )
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.idempotency_key == "reclaim-x:ready")
        )
        == 0
    )
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/idempotency/test_reclaim_idempotency.py -q`.

- [ ] **Minimal impl — the remaining pre-existing ingress, one double-submit each.** Create `tests/idempotency/test_ingress_idempotency.py`. Every test follows the same shape — POST the identical body twice through `db_client`, assert (1) both responses share a status code and an identical body, and (2) exactly one row persisted. Shown here for two of the fifteen; the rest are mechanical repeats of this shape against the routes in the matrix:

```python
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Evidence, Observation
from tests.api.test_lifecycle_api import SYSTEM, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def test_observation_double_submit(db_client, migrated_engine: Engine) -> None:
    body = {
        "idempotency_key": "idem-observation",
        "source_system": "github",
        "source_reference": "pr:1@abc123:sha256:deadbeef",
        "trust_classification": "trusted",
        "subject_type": "work_unit",
        "subject_reference": "idem-observation-unit",
        "observation_type": "github_pr",
        "status": "open",
        "observed_at": "2026-07-11T00:00:00+00:00",
        "summary": "pull request observed",
        "facts": {"pr_number": 1, "head_sha": "abc123", "state": "open", "merged": False},
    }

    first = db_client.post("/api/v1/observations", headers=SYSTEM, json=body)
    second = db_client.post("/api/v1/observations", headers=SYSTEM, json=body)

    assert first.status_code == second.status_code
    assert first.json() == second.json()
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Observation)
                .where(Observation.idempotency_key == "idem-observation")
            )
            == 1
        )


def test_worker_evidence_double_submit(db_client, migrated_engine: Engine) -> None:
    unit_id = _register_ready_unit(db_client, "idem-evidence")
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": "idem-evidence-claim", "expected_version": 2},
    ).json()
    db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/start",
        headers=WORKER,
        json={
            "idempotency_key": "idem-evidence-start",
            "expected_version": 3,
            "attempt": claim["attempt"],
            "lease_token": claim["lease_token"],
        },
    )
    brief = db_client.get(f"/api/v1/work-units/{unit_id}/runner-brief", headers=WORKER).json()
    body = {
        "idempotency_key": "idem-evidence",
        "work_package_revision_id": brief["work_package_revision_id"],
        "ac_id": "ac-1",
        "attempt": claim["attempt"],
        "lease_token": claim["lease_token"],
        "evidence_type": "check_run",
        "stable_ref": "https://github.com/AlobarQuest/orchestrator/runs/1",
        "payload": None,
        "source_revision": "abc123",
        "supersede": False,
    }

    first = db_client.post(f"/api/v1/work-units/{unit_id}/evidence", headers=WORKER, json=body)
    second = db_client.post(f"/api/v1/work-units/{unit_id}/evidence", headers=WORKER, json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.idempotency_key == "idem-evidence")
            )
            == 1
        )
```

Repeat for: `test_claim_double_submit`, `test_renew_double_submit` (both asserting the withheld-token replay contract above), `test_retry_authorization_double_submit`, `test_verifier_evidence_double_submit`, `test_adjudication_double_submit`, `test_deployment_observation_double_submit`, `test_dispatch_double_submit`, `test_release_artifact_double_submit`, `test_infra_lane_link_double_submit`, `test_knowledge_promotion_double_submit`, `test_approval_double_submit`, `test_dependency_double_submit`, `test_dependency_resolution_double_submit`.

- [ ] **Run — expect pass, and treat any failure as a real AC-007 gap.** `.venv/bin/pytest tests/idempotency -q`. A red row here is a genuine duplicate-delivery defect in an existing ingress — fix the service, do not relax the test.

- [ ] **Minimal impl — the new WS-P2.1 ingress.** Create `tests/idempotency/test_wsp21_ingress_idempotency.py`. The condition test calls the canonical service contract directly (`record_reconciliation_condition(session, ConditionCommand(...)) -> ConditionOutcome(condition, suppressed)`), unwrapping `.condition`:

```python
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import ReconciliationCondition
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    record_reconciliation_condition,
)

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def test_reconciliation_condition_double_ingest(migrated_session: Session, pr_diverged_unit) -> None:
    command = ConditionCommand(
        work_unit_id=pr_diverged_unit.id,
        observation_kind="github_pr",
        observation_id=pr_diverged_unit.observation_id,
        deployment_observation_id=None,
        condition_type="external_merge_alarm",
        stored_state={"state": "open", "merged": False},
        observed_state={"state": "closed", "merged": True},
        detail="pull request merged outside the session",
        actor=SYSTEM,
    )

    first = record_reconciliation_condition(migrated_session, command)
    second = record_reconciliation_condition(migrated_session, command)

    assert isinstance(first, ConditionOutcome) and isinstance(second, ConditionOutcome)
    assert first.suppressed is False
    assert second.suppressed is True  # a re-detection dedups; it is counted, never silent
    assert first.condition.id == second.condition.id
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(ReconciliationCondition)
            .where(ReconciliationCondition.work_unit_id == pr_diverged_unit.id)
        )
        == 1
    )
```

Alongside it: `test_resolution_double_submit` (POST `/review/reconciliation/conditions/{condition_id}/resolution` twice; `UNIQUE(condition_id)` means the second POST **replays**, never conflicts — assert one `reconciliation_resolutions` row and one `reconciliation.resolved` event), `test_detect_double_submit` (run a detect pass twice; assert no second condition row and `suppressed_duplicates >= 1` in the response counters — the fail-open counters of §1.7), `test_recover_evidence_double_submit` (call `evidence.recover_evidence` twice with one key; assert exactly one `Evidence` row **and** that `uq_evidence_unsuperseded_head` still holds — the second call creates no second `supersedes_evidence_id IS NULL` head), and `test_requeue_double_submit` (assert one Event under the key and `unit.version` unchanged by the replay).

- [ ] **Run — expect pass; the matrix gate now closes.** `.venv/bin/pytest tests/idempotency -q` → `test_every_ingress_post_route_has_a_matrix_row` and `test_every_matrix_row_names_a_real_test` both green.

- [ ] **Commit.**
```bash
git add tests/idempotency && git commit -m "AC-007: duplicate-delivery idempotency matrix, Postgres-backed

A checked-in executable matrix (path -> mechanism -> test), gated against the pinned
POST route inventory so a new ingress cannot land uncovered. Explicit tests for the two
asymmetries: the lifecycle path (unique Event key + row lock, NO advisory lock — proven
under a concurrent double-submit) and the reclaim compound :failed/:ready keys, including
the rejected-reclaim error replay. Claim-family assertions honor the replay contract: a
replay withholds the lease token by design."
```

---

### Task 13: Projection-vs-source consistency check (AC-008)

**Files:**
- Create: `src/orchestrator/services/consistency.py`
- Modify: `src/orchestrator/services/lifecycle.py` — rename `_required_ac_ids` (`:355-397`) → `required_ac_ids` (public) and update its single call site in `_transition_guards` (`:330`)
- Modify: `src/orchestrator/api/schemas.py` (`ConsistencyFindingResponse`, `ConsistencyReportResponse`)
- Modify: `src/orchestrator/api/routes.py` (`GET /api/v1/consistency-check`)
- Modify: `src/orchestrator/cli.py` (`orchestrator check-consistency`)
- Modify: `tests/architecture/test_scope_guards.py` (add `/api/v1/consistency-check` to the pinned GET inventory created in Task 10)
- Test: `tests/services/test_consistency.py`, `tests/api/test_consistency_api.py`, `tests/cli/test_consistency_cli.py`

**Interfaces:**
- Consumes: raw SQL over `evidence`, `adjudications`, `work_units` (`text(...)` + `session.execute`, the `observations.py:420-427` idiom); `status_ledger(session, StatusLedgerFilters(include_inactive=True))` (`status_ledger.py:83`) — the projection under audit, compared against an independent recomputation; `lifecycle.required_ac_ids` (a *source* lookup over the package/decomposition tables, not a projection helper); `require_operator_actor`.
- Produces: `ConsistencyFinding`, `ConsistencyReport`, `check_consistency(session) -> ConsistencyReport`, `GET /api/v1/consistency-check`, `orchestrator check-consistency` (exit **1** on divergence — that is what makes it assertable from a drill script).
- **Must NOT reuse:** `evidence._terminal` (`evidence.py:840-855` — it **raises** `evidence_chain_invalid` on a bad chain, so a check built on it would *crash instead of report*), `evidence.current_evidence` (`:152`, which calls `_terminal`), or `lifecycle._current_terminal_is_satisfied` / `_completion_satisfied` (reusing the set-difference would make the clean-fixture result a tautology).

#### Steps

- [ ] **Failing test — clean fixture reports zero divergence; the ZERO-head corrupt fixture is reported, not crashed.** The corrupt fixture is a **zero-head** chain, not a two-headed one: migration `0014_wsp21_recovery_controls` adds **`uq_evidence_unsuperseded_head`** — a partial unique index on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL` — which makes a second head structurally impossible. A row that supersedes *itself* has no `NULL`-supersedes root and no unsuperseded terminal: head count `0`. `count != 1` catches both shapes identically. Create `tests/services/test_consistency.py`:

```python
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Evidence
from orchestrator.services.consistency import check_consistency
from orchestrator.services.evidence import current_evidence
from tests.services.test_dependencies import register_unit


def _seed_evidence(session: Session, unit, ac_id: str, key: str) -> Evidence:
    row = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=1,
        evidence_type="check_run",
        stable_ref="https://github.com/AlobarQuest/orchestrator/runs/1",
        payload=None,
        source_revision="abc123",
        recorded_by="worker",
        event_id=uuid.uuid4(),
        idempotency_key=key,
        supersedes_evidence_id=None,
    )
    session.add(row)
    session.flush()
    return row


def test_clean_fixture_reports_zero_divergence(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-clean")
    unit.state = WorkUnitState.SUBMITTED
    _seed_evidence(migrated_session, unit, "ac-1", "consistency-clean-1")
    migrated_session.commit()

    report = check_consistency(migrated_session)

    assert report.findings == ()
    assert report.divergent is False


def test_zero_head_chain_is_reported_not_raised(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-zero-head")
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()
    # Seeded corruption: a row that supersedes ITSELF. `uq_evidence_unsuperseded_head`
    # forbids a second NULL-supersedes head, so this — not a two-headed chain — is the
    # reachable corruption. It is INSERTed directly: `evidence` is append-only (a BEFORE
    # UPDATE OR DELETE trigger, `0001_ws31_core.py:16`), so no service call can produce or
    # repair it, and the partial index does not apply to a non-NULL supersedes value.
    orphan_id = uuid.uuid4()
    migrated_session.execute(
        text(
            "INSERT INTO evidence (id, work_package_revision_id, work_unit_id, ac_id, attempt,"
            " evidence_type, stable_ref, payload, source_revision, recorded_by, event_id,"
            " idempotency_key, supersedes_evidence_id)"
            " VALUES (:id, :revision, :unit, :ac, 1, 'check_run', 'ref://orphan', NULL,"
            " 'abc123', 'worker', :event, :key, :id)"
        ),
        {
            "id": orphan_id,
            "revision": unit.work_package_revision_id,
            "unit": unit.id,
            "ac": "ac-1",
            "event": uuid.uuid4(),
            "key": "consistency-orphan",
        },
    )
    migrated_session.commit()

    # The helper the check must NOT reuse raises on this row — proving the check needs an
    # independent recomputation, or AC-008 would crash instead of report.
    with pytest.raises(DomainError) as error:
        current_evidence(migrated_session, unit.work_package_revision_id, unit.id, "ac-1")
    assert error.value.code == "evidence_chain_invalid"

    report = check_consistency(migrated_session)

    assert report.divergent is True
    heads = [finding for finding in report.findings if finding.check == "evidence_head_count"]
    assert len(heads) == 1
    assert heads[0].work_unit_id == unit.id
    assert heads[0].observed == "0"
    assert heads[0].expected == "1"


def test_completion_integrity_flags_a_completed_unit_with_no_adjudication(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "consistency-completion")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    report = check_consistency(migrated_session)

    findings = [f for f in report.findings if f.check == "completion_integrity"]
    assert [(f.work_unit_id, f.subject) for f in findings] == [(unit.id, "ac-1")]


def test_check_never_repairs(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "consistency-readonly")
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    version = unit.version

    check_consistency(migrated_session)
    migrated_session.rollback()
    migrated_session.refresh(unit)

    assert (unit.state, unit.version) == (WorkUnitState.COMPLETED, version)
```

- [ ] **Run — expect failure.** `.venv/bin/pytest tests/services/test_consistency.py -q` → `ModuleNotFoundError: No module named 'orchestrator.services.consistency'`.

- [ ] **Minimal impl — promote the required-AC source lookup.** In `src/orchestrator/services/lifecycle.py`, rename `_required_ac_ids` → `required_ac_ids` (`:355`) and update the call in `_transition_guards` (`:330`). It reads the package/decomposition **source** tables (`PackageAcceptanceCriterion`, `DecompositionProposalAcMapping`, `ApprovedDecomposition`, plus the `POST_DEPLOY_AC_IDS` specialization) — it is not a projection, and it is not the helper AC-008 audits. What the check must recompute independently is the *satisfaction* determination, which it does below.

- [ ] **Minimal impl — the check.** Create `src/orchestrator/services/consistency.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.persistence.models import Evidence, WorkPackageRevision, WorkUnit
from orchestrator.services.lifecycle import required_ac_ids
from orchestrator.services.status_ledger import StatusLedgerFilters, status_ledger

# One head per (revision, unit, ac). A "head" is a row that nothing supersedes. This is an
# INDEPENDENT recomputation: `evidence._terminal` RAISES on a bad chain, so a check built on
# it would crash instead of report, and reusing its set-difference would make the clean
# fixture a tautology. The `groups` CTE is what makes a ZERO-head chain visible — a plain
# join to the heads would drop the whole group.
_EVIDENCE_HEAD_COUNTS = text(
    """
    WITH groups AS (
        SELECT DISTINCT work_package_revision_id, work_unit_id, ac_id FROM evidence
    ),
    heads AS (
        SELECT e.work_package_revision_id, e.work_unit_id, e.ac_id, count(*) AS head_count
        FROM evidence e
        WHERE NOT EXISTS (
            SELECT 1 FROM evidence s WHERE s.supersedes_evidence_id = e.id
        )
        GROUP BY 1, 2, 3
    )
    SELECT g.work_package_revision_id, g.work_unit_id, g.ac_id,
           coalesce(h.head_count, 0) AS head_count
    FROM groups g
    LEFT JOIN heads h
      ON h.work_package_revision_id = g.work_package_revision_id
     AND h.work_unit_id = g.work_unit_id
     AND h.ac_id = g.ac_id
    WHERE coalesce(h.head_count, 0) <> 1
    ORDER BY g.work_unit_id, g.ac_id
    """
)

# Independent recomputation of `_current_terminal_is_satisfied` (lifecycle.py:424-453) in SQL:
# the terminal adjudication for a (unit, ac) is the one nothing supersedes; it must be unique
# and satisfying. Never calls that function — reusing it would audit the guard with the guard.
_SATISFIED_ADJUDICATED_ACS = text(
    """
    WITH terminals AS (
        SELECT a.work_unit_id, a.ac_id, a.outcome, a.scope, a.expires_at
        FROM adjudications a
        WHERE a.work_unit_id = :unit_id
          AND NOT EXISTS (
              SELECT 1 FROM adjudications s WHERE s.supersedes_adjudication_id = a.id
          )
    )
    SELECT t.ac_id
    FROM terminals t
    GROUP BY t.ac_id
    HAVING count(*) = 1
       AND bool_and(
           t.outcome IN ('passed', 'not_applicable')
           OR (t.outcome = 'waived' AND t.scope IS NULL
               AND (t.expires_at IS NULL OR t.expires_at > :now))
       )
    """
)


@dataclass(frozen=True)
class ConsistencyFinding:
    check: str
    work_unit_id: uuid.UUID | None
    subject: str
    detail: str
    observed: str
    expected: str


@dataclass(frozen=True)
class ConsistencyReport:
    checked_at: datetime
    findings: tuple[ConsistencyFinding, ...]

    @property
    def divergent(self) -> bool:
        return bool(self.findings)


def check_consistency(session: Session) -> ConsistencyReport:
    """Re-derives each live projection from the append-only source tables and REPORTS.

    It never repairs, never transitions, never commits, and never raises on corrupt data —
    a check that crashes on the corruption it exists to find is not a check.
    """
    now = TransactionClock().now(session)
    return ConsistencyReport(
        checked_at=now,
        findings=(
            *_evidence_head_findings(session),
            *_status_ledger_findings(session),
            *_completion_findings(session, now),
        ),
    )


def _evidence_head_findings(session: Session) -> tuple[ConsistencyFinding, ...]:
    rows = session.execute(_EVIDENCE_HEAD_COUNTS).all()
    return tuple(
        ConsistencyFinding(
            check="evidence_head_count",
            work_unit_id=unit_id,
            subject=ac_id,
            detail=f"revision {revision_id} has {head_count} unsuperseded evidence heads",
            observed=str(head_count),
            expected="1",
        )
        for revision_id, unit_id, ac_id, head_count in rows
    )


def _status_ledger_findings(session: Session) -> tuple[ConsistencyFinding, ...]:
    findings: list[ConsistencyFinding] = []
    for row in status_ledger(session, StatusLedgerFilters(include_inactive=True)):
        newest = session.scalar(
            select(Evidence.id)
            .where(Evidence.work_unit_id == row.unit_id)
            .order_by(Evidence.recorded_at.desc(), Evidence.id.desc())
            .limit(1)
        )
        observed = row.latest_evidence.id if row.latest_evidence is not None else None
        if observed == newest:
            continue
        findings.append(
            ConsistencyFinding(
                check="status_ledger_current_evidence",
                work_unit_id=row.unit_id,
                subject=row.unit_key,
                detail="projected current evidence disagrees with the source table",
                observed=str(observed),
                expected=str(newest),
            )
        )
    return tuple(findings)


def _completion_findings(session: Session, now: datetime) -> tuple[ConsistencyFinding, ...]:
    findings: list[ConsistencyFinding] = []
    units = session.scalars(
        select(WorkUnit).where(WorkUnit.state == "completed").order_by(WorkUnit.unit_key)
    ).all()
    for unit in units:
        revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
        if revision is None:
            continue
        required = required_ac_ids(session, revision, unit)
        if required is None:
            findings.append(
                ConsistencyFinding(
                    check="completion_integrity",
                    work_unit_id=unit.id,
                    subject=unit.unit_key,
                    detail="completed unit has no resolvable required acceptance criteria",
                    observed="none",
                    expected="at least one",
                )
            )
            continue
        satisfied = set(
            session.scalars(_SATISFIED_ADJUDICATED_ACS, {"unit_id": unit.id, "now": now})
        )
        findings.extend(
            ConsistencyFinding(
                check="completion_integrity",
                work_unit_id=unit.id,
                subject=ac_id,
                detail="completed unit has no satisfied terminal adjudication for this AC",
                observed="unsatisfied",
                expected="satisfied",
            )
            for ac_id in required
            if ac_id not in satisfied
        )
    return tuple(findings)
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/services/test_consistency.py -q` → 4 passed. The clean fixture yields zero findings; the zero-head fixture is *reported* while `current_evidence` on the same row *raises* — that contrast **is** the AC-008 evidence.

- [ ] **Failing test — route + CLI.** `tests/api/test_consistency_api.py`:

```python
from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_consistency_check_is_clean_on_a_fresh_database(db_client) -> None:
    response = db_client.get("/api/v1/consistency-check", headers=SYSTEM)

    assert response.status_code == 200
    assert response.json()["divergent"] is False
    assert response.json()["findings"] == []


def test_consistency_check_is_operator_only(db_client) -> None:
    assert db_client.get("/api/v1/consistency-check", headers=WORKER).status_code == 403
```

`tests/cli/test_consistency_cli.py` asserts exit code **0** on a clean database and **1** once a zero-head chain is seeded (the drill's assertion surface).

Add `"/api/v1/consistency-check"` to the pinned GET inventory created in Task 10.

- [ ] **Minimal impl — schema, route, CLI.** `src/orchestrator/api/schemas.py`:

```python
class ConsistencyFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check: str
    work_unit_id: UUID | None
    subject: str
    detail: str
    observed: str
    expected: str


class ConsistencyReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked_at: datetime
    divergent: bool
    findings: list[ConsistencyFindingResponse]
```

`src/orchestrator/api/routes.py` (beside `dead_letter_route`):

```python
@router.get("/consistency-check", response_model=ConsistencyReportResponse)
def consistency_check(
    actor: ActorDep,
    session: SessionDep,
) -> object:
    require_operator_actor(actor)
    return check_consistency(session)
```

`src/orchestrator/cli.py`:

```python
@app.command("check-consistency")
def check_consistency(json_output: JsonOption = False) -> None:
    """Report projection-vs-source divergence. Exit 1 when divergent — never repairs."""
    report = request("GET", "/api/v1/consistency-check")
    _emit(report, json_output)
    if isinstance(report, dict) and report.get("divergent"):
        raise typer.Exit(code=1)
```

- [ ] **Run — expect pass.** `.venv/bin/pytest tests/api/test_consistency_api.py tests/cli/test_consistency_cli.py tests/architecture/test_scope_guards.py -q`.

- [ ] **Full gate.** `make check` — read the `collected N items` count, not the exit code (exit 5, "no tests collected", is deliberately swallowed by the vendored Makefile). Then `/code-review` on the diff.

- [ ] **Commit.**
```bash
git add src/orchestrator/services/consistency.py src/orchestrator/services/lifecycle.py src/orchestrator/api/routes.py src/orchestrator/api/schemas.py src/orchestrator/cli.py tests/services/test_consistency.py tests/api/test_consistency_api.py tests/cli/test_consistency_cli.py tests/architecture/test_scope_guards.py && git commit -m "AC-008: projection-vs-source consistency check — reports, never repairs

Independent SQL recomputation, NOT the helpers it audits: _terminal raises on a bad
chain (it would crash instead of report) and reusing the set-difference would make the
clean-fixture result a tautology. The corrupt fixture is a ZERO-head chain (a
self-superseding row) — uq_evidence_unsuperseded_head makes two heads structurally
impossible, and count != 1 catches both shapes identically."
```
