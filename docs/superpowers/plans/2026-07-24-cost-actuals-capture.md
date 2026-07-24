# Cost-Actuals Capture Implementation Plan (WS-P2.4 Increment 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist real per-attempt LLM cost actuals as events, emitted by factory-runner and validated+committed by the orchestrator behind a SHA-pinned cross-repo contract, so the SLO cost/token metrics compute from real data.

**Architecture:** The runner extracts usage from the claude-code `execution_file` it already parses, and POSTs it to a new claim-gated orchestrator route. The orchestrator appends an `attempt.cost_recorded` Event (JSONB payload) and commits. `slo_report._cost`/`_tokens` aggregate those events. Both success and failure paths emit, before the terminal transition, with honest `cost_known=false` for missing transcripts.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x (`Session`), Pydantic v2, Alembic, pytest, Postgres (JSONB); factory-runner: typer CLI + httpx client; GitHub Actions workflow.

**Design spec:** `docs/superpowers/specs/2026-07-24-cost-actuals-capture-design.md`

## Global Constraints

- **Two repos:** orchestrator at `/Users/devon/Projects/orchestrator`, factory-runner at `/Users/devon/Projects/factory-runner`. Each task names its repo.
- **Only `DomainError` and `APIAuthenticationError` have exception handlers.** Any route/service that lets a stdlib `ValueError`/`TypeError`/`IntegrityError` escape produces a bare HTTP 500. Validate up front; raise `DomainError(code, message, recovery)` (3 positional args, `recovery` often `None`) for every rejected input; pre-check + catch `IntegrityError` on the unique idempotency key. Pydantic field/model validation errors surface as a handled 422 — that is acceptable.
- **A request-entry service MUST `session.commit()`.** A flush-only write is visible in-session (the ORM returns the object it holds) and gone in production. A service called *inside* another transaction must NOT commit — cost-actuals ingestion is a request entry point, so it commits.
- **Assert persistence by re-reading, not in-session.** Persistence tests must `session.expire_all()` (or open a fresh session) and re-query — asserting on the returned object proves nothing.
- **No envelope mutation, no `KNOWN_FIELDS`/`KNOWN_BUDGETS` change.** This increment never touches `WorkUnit.authority`. `tests/architecture/test_authority_write_once.py` must stay green untouched.
- **No fabricated cost.** `_cost`/`_tokens` compute only from recorded actuals; a window with none reports `no_data`, never a number derived from `max_llm_calls`.
- **`make check` exit 0 does not prove tests ran** (code 5 = "no tests collected" is swallowed). Read `collected N items`. Resolve pytest from `.venv/bin`. `make check` needs Postgres on `127.0.0.1:5432` and `SECURITY_STANDARDS_DIR`; per-test runs need the `migrated_session` fixture (same DB).
- **`ruff format` (not just `ruff check`) before every commit** in the orchestrator repo.
- **Claim-gated writes.** The cost-actuals route accepts a WORKER holding the unit's active claim, or SYSTEM — the exact `_authorize_write` guard `pr_binding` uses. The runner emits before its terminal `fail`/`submit` transition so the lease is still live.

---

### Task 1: Cross-repo contract fixture + SHA pin (BOTH repos)

Defines the byte-identical POST-body fixture and the shared SHA constant that makes a one-sided edit loud. Everything else keys off this shape.

**Files:**
- Create: `/Users/devon/Projects/orchestrator/tests/fixtures/runner_cost_actuals.json`
- Create: `/Users/devon/Projects/orchestrator/tests/contract/test_cost_actuals_contract.py`
- Create (byte-identical copy): `/Users/devon/Projects/factory-runner/tests/fixtures/runner_cost_actuals.json`
- Create: `/Users/devon/Projects/factory-runner/tests/test_cost_actuals_contract.py`

**Interfaces:**
- Produces: the canonical cost-actuals POST body shape + `COST_ACTUALS_CONTRACT_SHA256`, consumed by Tasks 2, 3, 9.

- [ ] **Step 1: Write the fixture (orchestrator).** Create `tests/fixtures/runner_cost_actuals.json` with the exact bytes (a `cost_known: true` example — the shape all fields must satisfy):

```json
{
  "idempotency_key": "factory-runner:11111111-1111-1111-1111-111111111111:cost:a2",
  "attempt": 2,
  "lease_token": "lease-token-example",
  "cost_known": true,
  "llm_calls": 37,
  "num_turns": 12,
  "input_tokens": 812004,
  "output_tokens": 41220,
  "cost_usd": 9.14
}
```

- [ ] **Step 2: Compute the canonical SHA.** Run:

```bash
cd /Users/devon/Projects/orchestrator && python -c "import hashlib,json; d=json.load(open('tests/fixtures/runner_cost_actuals.json')); print(hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest())"
```

Record the printed hash; call it `<SHA>` below.

- [ ] **Step 3: Write the orchestrator contract test** `tests/contract/test_cost_actuals_contract.py`:

```python
"""The cost-actuals seam contract (WS-P2.4 Increment 1), orchestrator side.

`tests/fixtures/runner_cost_actuals.json` is a byte-identical copy of the file of the same
name in AlobarQuest/factory-runner. `COST_ACTUALS_CONTRACT_SHA256` is identical in both
repos' tests, so a one-sided edit fails here rather than at the next dispatch.
"""

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runner_cost_actuals.json"
COST_ACTUALS_CONTRACT_SHA256 = "<SHA>"


def golden_cost_actuals() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_cost_actuals_is_unchanged() -> None:
    """A one-sided edit here means factory-runner's copy has silently drifted."""
    canonical = json.dumps(golden_cost_actuals(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == COST_ACTUALS_CONTRACT_SHA256
```

- [ ] **Step 4: Run it (orchestrator).**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/contract/test_cost_actuals_contract.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Copy the fixture byte-identically into factory-runner and pin it there.** Copy the file, then create `/Users/devon/Projects/factory-runner/tests/test_cost_actuals_contract.py`:

```python
"""The cost-actuals seam contract (WS-P2.4 Increment 1), runner side.

`tests/fixtures/runner_cost_actuals.json` is byte-identical to the orchestrator's copy.
The two must change together — COST_ACTUALS_CONTRACT_SHA256 is identical in both tests.
"""

import hashlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "runner_cost_actuals.json"
COST_ACTUALS_CONTRACT_SHA256 = "<SHA>"


def golden_cost_actuals() -> dict:
    return json.loads(FIXTURE.read_text())


def test_golden_cost_actuals_is_unchanged() -> None:
    canonical = json.dumps(golden_cost_actuals(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == COST_ACTUALS_CONTRACT_SHA256
```

- [ ] **Step 6: Run it (factory-runner).**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_actuals_contract.py -v`
Expected: PASS. Confirm the two fixtures are byte-identical: `diff /Users/devon/Projects/orchestrator/tests/fixtures/runner_cost_actuals.json /Users/devon/Projects/factory-runner/tests/fixtures/runner_cost_actuals.json` prints nothing.

- [ ] **Step 7: Commit (each repo separately).**

```bash
cd /Users/devon/Projects/orchestrator && ruff format tests/contract/test_cost_actuals_contract.py && git add tests/fixtures/runner_cost_actuals.json tests/contract/test_cost_actuals_contract.py && git commit -m "test(wsp24): pin cost-actuals cross-repo contract fixture"
cd /Users/devon/Projects/factory-runner && git add tests/fixtures/runner_cost_actuals.json tests/test_cost_actuals_contract.py && git commit -m "test(wsp24): pin cost-actuals cross-repo contract fixture (runner side)"
```

---

### Task 2: `CostActualsCommand` schema (orchestrator)

The Pydantic model the route consumes. Structural + cross-field validation lives here (a violation is a handled 422, never a 500). No `expected_version` — this is an append, not an update.

**Files:**
- Modify: `/Users/devon/Projects/orchestrator/src/orchestrator/api/schemas.py` (add after `PrBindingResponse`, ~line 985)
- Test: `/Users/devon/Projects/orchestrator/tests/api/test_cost_actuals_schema.py` (create)

**Interfaces:**
- Consumes: fixture shape from Task 1.
- Produces: `CostActualsCommand` with fields `idempotency_key: str`, `attempt: int`, `lease_token: str`, `cost_known: bool`, `llm_calls: int | None`, `num_turns: int | None`, `input_tokens: int | None`, `output_tokens: int | None`, `cost_usd: float | None`; and `CostActualsResponse` with `work_unit_id: UUID`, `attempt: int`, `event_id: UUID`, `cost_known: bool`. Consumed by Tasks 3, 4.

- [ ] **Step 1: Write the failing test** `tests/api/test_cost_actuals_schema.py`:

```python
import pytest
from pydantic import ValidationError

from orchestrator.api.schemas import CostActualsCommand
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_schema_accepts_the_golden_fixture():
    command = CostActualsCommand.model_validate(golden_cost_actuals())
    assert command.cost_known is True
    assert command.llm_calls == 37
    assert command.cost_usd == 9.14


def test_cost_known_true_requires_all_numerics():
    payload = golden_cost_actuals() | {"llm_calls": None}
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(payload)


def test_cost_known_false_requires_all_numerics_null():
    unknown = {
        "idempotency_key": "k",
        "attempt": 2,
        "lease_token": "t",
        "cost_known": False,
        "llm_calls": None,
        "num_turns": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
    }
    assert CostActualsCommand.model_validate(unknown).cost_known is False
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(unknown | {"llm_calls": 5})


def test_negative_values_rejected():
    with pytest.raises(ValidationError):
        CostActualsCommand.model_validate(golden_cost_actuals() | {"input_tokens": -1})
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/api/test_cost_actuals_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'CostActualsCommand'`.

- [ ] **Step 3: Add the schema** in `src/orchestrator/api/schemas.py` (after `PrBindingResponse`). Note `model_validator` for the cross-field rule; import it if not already imported (`from pydantic import ... , model_validator`):

```python
class CostActualsCommand(BaseModel):
    """A runner reporting the actual LLM cost of one work-unit attempt.

    No expected_version: this is an append (an attempt.cost_recorded event), not an update to
    the work unit, so there is no optimistic-concurrency target. `attempt` + `lease_token` prove
    the caller holds this unit's live claim, exactly as evidence and pr-binding demand. When
    `cost_known` is False (a failed attempt left no usable transcript) every numeric is null --
    the cost is honestly absent, never a fabricated zero.
    """

    idempotency_key: str = Field(min_length=1, max_length=200)
    attempt: int = Field(gt=0)
    lease_token: str = Field(min_length=1)
    cost_known: bool
    llm_calls: int | None = Field(default=None, ge=0)
    num_turns: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _numerics_match_cost_known(self) -> "CostActualsCommand":
        numerics = (
            self.llm_calls,
            self.num_turns,
            self.input_tokens,
            self.output_tokens,
            self.cost_usd,
        )
        if self.cost_known and any(value is None for value in numerics):
            raise ValueError("cost_known is true but a numeric field is null")
        if not self.cost_known and any(value is not None for value in numerics):
            raise ValueError("cost_known is false but a numeric field is non-null")
        return self


class CostActualsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    attempt: int
    event_id: UUID
    cost_known: bool
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/api/test_cost_actuals_schema.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/api/schemas.py tests/api/test_cost_actuals_schema.py && git add src/orchestrator/api/schemas.py tests/api/test_cost_actuals_schema.py && git commit -m "feat(wsp24): CostActualsCommand/Response schemas"
```

---

### Task 3: `record_cost_actuals` service (orchestrator)

Validates the unit exists, authorizes the claim-holding worker (or SYSTEM), and appends an `attempt.cost_recorded` Event, committing. Idempotent on the unique key: a re-emit returns the existing event, never a 500.

**Files:**
- Create: `/Users/devon/Projects/orchestrator/src/orchestrator/services/cost_actuals.py`
- Test: `/Users/devon/Projects/orchestrator/tests/services/test_cost_actuals.py` (create)

**Interfaces:**
- Consumes: `_authorize_write` pattern from `services/pr_bindings.py`; `Event` model; `ActorContext`.
- Produces: `record_cost_actuals(session, *, actor, work_unit_id, attempt, lease_token, cost_known, llm_calls, num_turns, input_tokens, output_tokens, cost_usd, idempotency_key) -> Event`. Consumed by Task 4. The stored `Event` has `action="attempt.cost_recorded"`, `subject_type="work_unit"`, `subject_id=work_unit_id`, `payload={"attempt", "cost_known", "llm_calls", "num_turns", "input_tokens", "output_tokens", "cost_usd"}`.

- [ ] **Step 1: Write the failing tests** `tests/services/test_cost_actuals.py`. (Reuse existing test helpers for building a claimed unit — mirror how `tests/services/test_pr_bindings.py` constructs a WORKER actor + active claim; import those helpers rather than re-deriving.)

```python
import uuid

import pytest

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event
from orchestrator.services.cost_actuals import record_cost_actuals
from tests.services.test_pr_bindings import _claimed_unit, worker_actor  # existing helpers


def _record(session, unit, actor, claim, **overrides):
    kwargs = dict(
        work_unit_id=unit.id,
        actor=actor,
        attempt=claim.attempt,
        lease_token=claim.lease_token,
        cost_known=True,
        llm_calls=37,
        num_turns=12,
        input_tokens=812004,
        output_tokens=41220,
        cost_usd=9.14,
        idempotency_key=f"factory-runner:{unit.id}:cost:a{claim.attempt}",
    )
    kwargs.update(overrides)
    return record_cost_actuals(session, **kwargs)


def test_records_event_and_persists(migrated_session):
    unit, actor, claim = _claimed_unit(migrated_session, worker_actor())
    event = _record(migrated_session, unit, actor, claim)
    migrated_session.expire_all()  # prove it committed, not just flushed
    reread = migrated_session.get(Event, event.id)
    assert reread is not None
    assert reread.action == "attempt.cost_recorded"
    assert reread.subject_id == unit.id
    assert reread.payload["llm_calls"] == 37
    assert reread.payload["cost_known"] is True


def test_reemit_same_key_is_idempotent(migrated_session):
    unit, actor, claim = _claimed_unit(migrated_session, worker_actor())
    first = _record(migrated_session, unit, actor, claim)
    again = _record(migrated_session, unit, actor, claim)
    assert again.id == first.id
    count = sum(
        1
        for _ in migrated_session.query(Event).filter(Event.action == "attempt.cost_recorded")
    )
    assert count == 1


def test_unknown_unit_is_domain_error(migrated_session):
    _, actor, _ = _claimed_unit(migrated_session, worker_actor())
    with pytest.raises(DomainError) as exc:
        record_cost_actuals(
            migrated_session,
            work_unit_id=uuid.uuid4(),
            actor=actor,
            attempt=1,
            lease_token="x",
            cost_known=False,
            llm_calls=None,
            num_turns=None,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            idempotency_key="k",
        )
    assert exc.value.code == "work_unit_not_found"


def test_wrong_lease_is_domain_error(migrated_session):
    unit, actor, claim = _claimed_unit(migrated_session, worker_actor())
    with pytest.raises(DomainError):
        _record(migrated_session, unit, actor, claim, lease_token="not-the-lease")
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/services/test_cost_actuals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.services.cost_actuals'`.

> If `_claimed_unit`/`worker_actor` do not exist under those names in `tests/services/test_pr_bindings.py`, open that file and use whatever helper builds a WORKER + active claim (the pr-binding tests must construct one to pass `validate_active_claim`). Match its real signature.

- [ ] **Step 3: Write the service** `src/orchestrator/services/cost_actuals.py`:

```python
"""Record the actual LLM cost of a work-unit attempt as an append-only event (WS-P2.4).

The runner reports what its claude-code run actually consumed. This is a request entry point,
so it OWNS its transaction and commits. The events table's unique idempotency_key makes a
re-emit a no-op: we pre-check and also catch the race, so a duplicate is never a bare 500.
No envelope is touched; this only appends an event.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.claims import validate_active_claim
from orchestrator.services.lifecycle import ActorContext

ACTION = "attempt.cost_recorded"


def record_cost_actuals(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    attempt: int,
    lease_token: str,
    cost_known: bool,
    llm_calls: int | None,
    num_turns: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    idempotency_key: str,
) -> Event:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    _authorize(session, unit, actor, attempt, lease_token)

    existing = session.scalar(
        select(Event).where(Event.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    event = Event(
        id=uuid.uuid4(),
        occurred_at=TransactionClock().now(session),
        actor_id=actor.actor_id,
        action=ACTION,
        subject_type="work_unit",
        subject_id=work_unit_id,
        from_state=None,
        to_state=None,
        payload={
            "attempt": attempt,
            "cost_known": cost_known,
            "llm_calls": llm_calls,
            "num_turns": num_turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent emit won the unique idempotency_key. First write wins; return it.
        session.rollback()
        winner = session.scalar(
            select(Event).where(Event.idempotency_key == idempotency_key)
        )
        if winner is None:
            raise
        return winner
    return event


def _authorize(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    attempt: int,
    lease_token: str,
) -> None:
    if actor.role is ActorRole.SYSTEM:
        return
    if actor.role is not ActorRole.WORKER:
        raise DomainError(
            "role_forbidden",
            "only a claim-holding worker or the system actor may record cost actuals",
            None,
        )
    validate_active_claim(session, unit, actor, attempt, lease_token)
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/services/test_cost_actuals.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/cost_actuals.py tests/services/test_cost_actuals.py && git add src/orchestrator/services/cost_actuals.py tests/services/test_cost_actuals.py && git commit -m "feat(wsp24): record_cost_actuals service (claim-gated, idempotent event append)"
```

---

### Task 4: `POST /work-units/{unit_id}/cost-actuals` route (orchestrator)

Wires the schema to the service. All semantic errors are `DomainError`; structural errors are the schema's 422. No stdlib exception escapes.

**Files:**
- Modify: `/Users/devon/Projects/orchestrator/src/orchestrator/api/routes.py` (add route near the `evidence` route; import the service and response schema)
- Test: `/Users/devon/Projects/orchestrator/tests/api/test_cost_actuals_route.py` (create)

**Interfaces:**
- Consumes: `CostActualsCommand`/`CostActualsResponse` (Task 2), `record_cost_actuals` (Task 3), the `ActorDep`/`SessionDep`/`_raise_error` conventions already in `routes.py`.
- Produces: HTTP `POST /api/v1/work-units/{unit_id}/cost-actuals`.

- [ ] **Step 1: Write the failing route test** `tests/api/test_cost_actuals_route.py`. Mirror the existing route-test harness (find how `tests/api/test_*route*.py` builds the FastAPI `TestClient` with a WORKER M2M actor + a claimed unit; reuse that fixture/helper — do not hand-roll auth):

```python
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_post_cost_actuals_persists_event(worker_client, claimed_unit):
    body = golden_cost_actuals() | {
        "idempotency_key": f"factory-runner:{claimed_unit.id}:cost:a{claimed_unit.attempt}",
        "attempt": claimed_unit.attempt,
        "lease_token": claimed_unit.lease_token,
    }
    resp = worker_client.post(f"/api/v1/work-units/{claimed_unit.id}/cost-actuals", json=body)
    assert resp.status_code == 200
    assert resp.json()["cost_known"] is True


def test_post_cost_actuals_bad_body_is_422_not_500(worker_client, claimed_unit):
    resp = worker_client.post(
        f"/api/v1/work-units/{claimed_unit.id}/cost-actuals",
        json={"idempotency_key": "k", "attempt": 1, "lease_token": "t", "cost_known": True},
    )
    assert resp.status_code == 422  # cost_known true but numerics missing -> handled, never 500


def test_post_cost_actuals_unknown_unit_is_clean_4xx(worker_client):
    import uuid

    body = golden_cost_actuals()
    resp = worker_client.post(f"/api/v1/work-units/{uuid.uuid4()}/cost-actuals", json=body)
    assert resp.status_code in (400, 404, 409)  # DomainError -> handled, not 500
    assert resp.status_code != 500
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/api/test_cost_actuals_route.py -v`
Expected: FAIL (404 route not found, or fixture import error to resolve against the real harness).

- [ ] **Step 3: Add the route** in `src/orchestrator/api/routes.py`. Add imports (`CostActualsCommand, CostActualsResponse` to the schemas import block; `from orchestrator.services.cost_actuals import record_cost_actuals`). Then, near the `evidence` route:

```python
@router.post("/work-units/{unit_id}/cost-actuals", response_model=CostActualsResponse)
def cost_actuals(
    unit_id: UUID,
    body: CostActualsCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """The runner reports the actual LLM cost of one attempt (WS-P2.4 Increment 1).

    Claim-gated exactly like evidence/pr-binding: a worker must prove it holds this unit's live
    claim. Emitted before the terminal fail/submit transition so the lease is still valid.
    """
    event = record_cost_actuals(
        session,
        actor=actor,
        work_unit_id=unit_id,
        attempt=body.attempt,
        lease_token=body.lease_token,
        cost_known=body.cost_known,
        llm_calls=body.llm_calls,
        num_turns=body.num_turns,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost_usd=body.cost_usd,
        idempotency_key=body.idempotency_key,
    )
    return CostActualsResponse(
        work_unit_id=unit_id,
        attempt=body.attempt,
        event_id=event.id,
        cost_known=body.cost_known,
    )
```

> Note: `record_cost_actuals` returns an `Event` directly (raising `DomainError` on failure), so the route returns the response model straight — it does not need the `_raise_error(...)` wrapper that services returning a Result/error union use. If a nearby convention requires `_raise_error`, check what `record_cost_actuals` returns and match the pattern; this plan's service raises, so no wrapper.

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/api/test_cost_actuals_route.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/api/routes.py tests/api/test_cost_actuals_route.py && git add src/orchestrator/api/routes.py tests/api/test_cost_actuals_route.py && git commit -m "feat(wsp24): POST /cost-actuals route"
```

---

### Task 5: SLO `_cost`/`_tokens` real aggregation + replace guard test (orchestrator)

Aggregate `attempt.cost_recorded` events into real metric values; delete the `not_instrumented` guard and replace it with computed/no_data/partial tests. Removing the guard is intended and reviewed.

**Files:**
- Modify: `/Users/devon/Projects/orchestrator/src/orchestrator/services/slo_report.py:325-340` (`_cost`, `_tokens`)
- Modify: `/Users/devon/Projects/orchestrator/tests/services/test_slo_report.py` (remove `test_cost_and_tokens_are_not_instrumented:187-193`, add new tests)

**Interfaces:**
- Consumes: the `attempt.cost_recorded` event shape from Task 3; the `_add_event` fixture builder + `MetricValue`/status constants already in the test module.
- Produces: `_cost`/`_tokens` returning `computed`/`no_data`/`partial`.

- [ ] **Step 1: Write the new failing tests** in `tests/services/test_slo_report.py` (add near the improvisation tests). Note `_add_event` currently sets `payload={"actor_role": ...}`; add a small helper to insert a cost event with a real payload:

```python
def _add_cost_event(session, unit_id, *, occurred_at, cost_known=True,
                    llm_calls=10, input_tokens=1000, output_tokens=200, cost_usd=1.5):
    event = Event(
        occurred_at=occurred_at,
        actor_id="worker",
        action="attempt.cost_recorded",
        subject_type="work_unit",
        subject_id=unit_id,
        from_state=None,
        to_state=None,
        payload={
            "attempt": 1, "cost_known": cost_known,
            "llm_calls": llm_calls if cost_known else None,
            "num_turns": 3 if cost_known else None,
            "input_tokens": input_tokens if cost_known else None,
            "output_tokens": output_tokens if cost_known else None,
            "cost_usd": cost_usd if cost_known else None,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"cost-{uuid.uuid4()}",
    )
    session.add(event)
    session.flush()
    return event


def test_cost_and_tokens_no_data_when_no_cost_events(migrated_session):
    report = slo_report(migrated_session)
    assert report.cost_per_unit.status == STATUS_NO_DATA
    assert report.token_consumption.status == STATUS_NO_DATA


def test_cost_and_tokens_computed_from_events(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "cost")
    _add_cost_event(migrated_session, unit.id, occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
                    cost_usd=2.0, input_tokens=1000, output_tokens=200)
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.cost_per_unit.status == STATUS_COMPUTED
    assert report.cost_per_unit.value == 2.0
    assert report.token_consumption.status == STATUS_COMPUTED
    assert report.token_consumption.value == 1200.0


def test_cost_partial_when_some_unknown(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "cost")
    _add_cost_event(migrated_session, unit.id, occurred_at=datetime(2026, 7, 3, tzinfo=UTC))
    _add_cost_event(migrated_session, unit.id, occurred_at=datetime(2026, 7, 4, tzinfo=UTC),
                    cost_known=False)
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.cost_per_unit.status == STATUS_PARTIAL
```

Delete `test_cost_and_tokens_are_not_instrumented` (lines 187-193) and remove the now-unused `STATUS_NOT_INSTRUMENTED` import only if nothing else references it (grep first).

- [ ] **Step 2: Run to verify failure.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/services/test_slo_report.py -k cost -v`
Expected: the new tests FAIL (`_cost` still returns `not_instrumented`).

- [ ] **Step 3: Implement aggregation.** Replace `_cost` and `_tokens` in `src/orchestrator/services/slo_report.py`. Use `func.sum` over JSONB numerics cast to numeric, plus a `partial` signal when any in-window cost event is `cost_known=false`. Add needed imports (`from sqlalchemy import cast, Float` etc. — match the module's existing sqlalchemy import line):

```python
_COST_ACTION = "attempt.cost_recorded"


def _cost_events_in_window(session, since, until):
    known = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == _COST_ACTION,
            Event.occurred_at >= since,
            Event.occurred_at < until,
            Event.payload["cost_known"].astext == "true",
        )
    ) or 0
    unknown = session.scalar(
        select(func.count(Event.id)).where(
            Event.action == _COST_ACTION,
            Event.occurred_at >= since,
            Event.occurred_at < until,
            Event.payload["cost_known"].astext == "false",
        )
    ) or 0
    return known, unknown


def _cost(session, since, until, now) -> MetricValue:
    known, unknown = _cost_events_in_window(session, since, until)
    if known == 0 and unknown == 0:
        return MetricValue(STATUS_NO_DATA, None, "no cost actuals were recorded in the window")
    total = session.scalar(
        select(func.sum(cast(Event.payload["cost_usd"].astext, Float))).where(
            Event.action == _COST_ACTION,
            Event.occurred_at >= since,
            Event.occurred_at < until,
            Event.payload["cost_known"].astext == "true",
        )
    ) or 0.0
    status = STATUS_PARTIAL if unknown else STATUS_COMPUTED
    return MetricValue(
        status,
        float(total),
        f"summed cost_usd over {known} cost-known attempts in window"
        + (f"; {unknown} attempts had unknown cost (excluded)" if unknown else ""),
    )


def _tokens(session, since, until, now) -> MetricValue:
    known, unknown = _cost_events_in_window(session, since, until)
    if known == 0 and unknown == 0:
        return MetricValue(STATUS_NO_DATA, None, "no token actuals were recorded in the window")
    total = session.scalar(
        select(
            func.sum(
                cast(Event.payload["input_tokens"].astext, Float)
                + cast(Event.payload["output_tokens"].astext, Float)
            )
        ).where(
            Event.action == _COST_ACTION,
            Event.occurred_at >= since,
            Event.occurred_at < until,
            Event.payload["cost_known"].astext == "true",
        )
    ) or 0.0
    status = STATUS_PARTIAL if unknown else STATUS_COMPUTED
    return MetricValue(
        status,
        float(total),
        f"summed input+output tokens over {known} cost-known attempts in window"
        + (f"; {unknown} attempts had unknown cost (excluded)" if unknown else ""),
    )
```

> Verify the JSONB accessor idiom against the module's existing usage. If `Event.payload[...].astext` is not already used in this codebase, the equivalent is `Event.payload["cost_known"].as_string()` or a `cast(..., ...)` — check one existing JSONB read in `src/orchestrator/` and match it. The `_improvisation` metric uses a real `Event.improvisation` column, so payload-JSONB reads may be new here; confirm the SQLAlchemy JSONB indexing form the repo uses before finalizing.

- [ ] **Step 4: Run tests.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/services/test_slo_report.py -v`
Expected: PASS (all, including the 3 new cost tests; the old guard test is gone).

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/slo_report.py tests/services/test_slo_report.py && git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py && git commit -m "feat(wsp24): SLO cost/tokens compute from attempt.cost_recorded events"
```

---

### Task 6: Public-surface drill (orchestrator)

Drive the real HTTP endpoint end-to-end and assert the SLO report then computes — proving a real caller path exists (the WS-P2.1 reachability lesson), not just unit-tested services.

**Files:**
- Test: `/Users/devon/Projects/orchestrator/tests/drills/test_cost_actuals_drill.py` (create; if `tests/drills/` does not exist, place under `tests/api/` following the nearest existing end-to-end test's location)

**Interfaces:**
- Consumes: the route (Task 4), the SLO report endpoint/service, the worker-client + claimed-unit harness.

- [ ] **Step 1: Write the drill.**

```python
from tests.contract.test_cost_actuals_contract import golden_cost_actuals


def test_posted_cost_actuals_flow_into_the_slo_report(worker_client, claimed_unit):
    body = golden_cost_actuals() | {
        "idempotency_key": f"factory-runner:{claimed_unit.id}:cost:a{claimed_unit.attempt}",
        "attempt": claimed_unit.attempt,
        "lease_token": claimed_unit.lease_token,
        "cost_usd": 3.0,
        "input_tokens": 1000,
        "output_tokens": 500,
    }
    post = worker_client.post(f"/api/v1/work-units/{claimed_unit.id}/cost-actuals", json=body)
    assert post.status_code == 200

    report = worker_client.get("/api/v1/slo-report")
    assert report.status_code == 200
    data = report.json()
    assert data["cost_per_unit"]["status"] in ("computed", "partial")
    assert data["cost_per_unit"]["value"] == 3.0
    assert data["token_consumption"]["value"] == 1500.0
```

> If `/api/v1/slo-report` requires a SYSTEM bearer (read endpoints do), use the system client fixture for the GET and the worker client for the POST. Match the auth the real endpoints demand.

- [ ] **Step 2: Run.**

Run: `cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/drills/test_cost_actuals_drill.py -v`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format tests/drills/test_cost_actuals_drill.py && git add tests/drills/test_cost_actuals_drill.py && git commit -m "test(wsp24): public-surface drill POST cost-actuals -> SLO computes"
```

- [ ] **Step 4: Full-repo gate.**

Run: `cd /Users/devon/Projects/orchestrator && make check 2>&1 | tail -30`
Expected: green; **read `collected N items`** and confirm the new tests ran (exit 0 alone is not proof). If pre-existing format-debt in untouched files reddens `ruff format --check .`, confirm it fails on `main` too before attributing it here.

---

### Task 7: Runner usage extraction (factory-runner)

Extract usage/cost from the terminal `result` record of the execution file, honestly returning "unknown" when no usable transcript exists.

**Files:**
- Modify: `/Users/devon/Projects/factory-runner/src/factory_runner/coding_result.py`
- Test: `/Users/devon/Projects/factory-runner/tests/test_cost_extraction.py` (create)

**Interfaces:**
- Consumes: `_execution_records` (existing).
- Produces: `@dataclass(frozen=True) CostActuals(cost_known: bool, llm_calls: int | None, num_turns: int | None, input_tokens: int | None, output_tokens: int | None, cost_usd: float | None)` and `extract_cost_actuals(path: Path) -> CostActuals`. Consumed by Task 9/10.

- [ ] **Step 1: Write failing tests** `tests/test_cost_extraction.py`:

```python
import json
from pathlib import Path

from factory_runner.coding_result import CostActuals, extract_cost_actuals


def _write(tmp_path, records):
    p = tmp_path / "exec.json"
    p.write_text(json.dumps(records))
    return p


def test_extracts_usage_from_terminal_result(tmp_path):
    path = _write(tmp_path, [
        {"type": "assistant", "message": {}},
        {"type": "assistant", "message": {}},
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 5, "total_cost_usd": 4.25,
         "usage": {"input_tokens": 1000, "output_tokens": 200}},
    ])
    actuals = extract_cost_actuals(path)
    assert actuals == CostActuals(
        cost_known=True, llm_calls=2, num_turns=5,
        input_tokens=1000, output_tokens=200, cost_usd=4.25,
    )


def test_missing_file_is_unknown(tmp_path):
    actuals = extract_cost_actuals(tmp_path / "does-not-exist.json")
    assert actuals.cost_known is False
    assert actuals.llm_calls is None
    assert actuals.cost_usd is None


def test_no_terminal_result_is_unknown(tmp_path):
    path = _write(tmp_path, [{"type": "assistant", "message": {}}])
    assert extract_cost_actuals(path).cost_known is False
```

- [ ] **Step 2: Run to verify failure.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_extraction.py -v`
Expected: FAIL (`ImportError: cannot import name 'CostActuals'`).

- [ ] **Step 3: Implement** in `src/factory_runner/coding_result.py` (append):

```python
@dataclass(frozen=True)
class CostActuals:
    cost_known: bool
    llm_calls: int | None
    num_turns: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


_UNKNOWN_COST = CostActuals(
    cost_known=False, llm_calls=None, num_turns=None,
    input_tokens=None, output_tokens=None, cost_usd=None,
)


def extract_cost_actuals(path: Path) -> CostActuals:
    """Best-effort usage from the terminal result record; unknown when no usable transcript.

    llm_calls is the count of assistant records (true model calls), NOT num_turns (agentic
    turns) -- the declared budget field is max_llm_calls, so the actual must be a faithful call
    count. A missing/partial transcript yields cost_known=False rather than a fabricated zero.
    """
    try:
        records = _execution_records(path)
    except CodingResultError:
        return _UNKNOWN_COST
    results = [r for r in records if r.get("type") == "result"]
    if len(results) != 1 or results[0] is not records[-1]:
        return _UNKNOWN_COST
    result = results[0]
    usage = result.get("usage")
    cost_usd = result.get("total_cost_usd")
    if not isinstance(usage, dict) or not isinstance(cost_usd, (int, float)):
        return _UNKNOWN_COST
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    num_turns = result.get("num_turns")
    if not all(isinstance(v, int) for v in (input_tokens, output_tokens, num_turns)):
        return _UNKNOWN_COST
    llm_calls = sum(1 for r in records if r.get("type") == "assistant")
    return CostActuals(
        cost_known=True,
        llm_calls=llm_calls,
        num_turns=num_turns,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(cost_usd),
    )
```

- [ ] **Step 4: Run tests.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_extraction.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/factory-runner && git add src/factory_runner/coding_result.py tests/test_cost_extraction.py && git commit -m "feat(wsp24): extract cost actuals from claude-code execution file"
```

---

### Task 8: `OrchestratorClient.cost_actuals` + client contract test (factory-runner)

The HTTP method the runner uses, plus a test asserting its parameters cover the contract fields.

**Files:**
- Modify: `/Users/devon/Projects/factory-runner/src/factory_runner/client.py`
- Modify: `/Users/devon/Projects/factory-runner/tests/test_cost_actuals_contract.py` (add the coverage test from Task 1's file)

**Interfaces:**
- Consumes: `_request` (existing), `golden_cost_actuals()` (Task 1).
- Produces: `OrchestratorClient.cost_actuals(unit_id, *, attempt, lease_token, cost_known, llm_calls, num_turns, input_tokens, output_tokens, cost_usd, idempotency_key) -> dict`. Consumed by Task 9.

- [ ] **Step 1: Write the failing coverage test** (append to `tests/test_cost_actuals_contract.py`):

```python
def test_cost_actuals_client_parameters_cover_the_contract() -> None:
    import inspect

    from factory_runner.client import OrchestratorClient

    parameters = set(inspect.signature(OrchestratorClient.cost_actuals).parameters) - {"self"}
    body_fields = set(golden_cost_actuals().keys())
    # unit_id is a path param, not a body field; everything else in the body is a client kwarg.
    assert parameters == {"unit_id"} | body_fields
```

- [ ] **Step 2: Run to verify failure.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_actuals_contract.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'cost_actuals'`).

- [ ] **Step 3: Add the client method** in `src/factory_runner/client.py` (near `pr_binding`):

```python
    def cost_actuals(
        self,
        unit_id: str,
        *,
        attempt: int,
        lease_token: str,
        cost_known: bool,
        llm_calls: int | None,
        num_turns: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/work-units/{unit_id}/cost-actuals",
            json={
                "idempotency_key": idempotency_key,
                "attempt": attempt,
                "lease_token": lease_token,
                "cost_known": cost_known,
                "llm_calls": llm_calls,
                "num_turns": num_turns,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            },
        )
        return response.json()
```

- [ ] **Step 4: Run tests.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_actuals_contract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/factory-runner && git add src/factory_runner/client.py tests/test_cost_actuals_contract.py && git commit -m "feat(wsp24): OrchestratorClient.cost_actuals + client contract coverage"
```

---

### Task 9: Emit on both paths + workflow plumbing (factory-runner)

Thread the execution file into `finalize-run` and `fail-run`, and emit cost actuals **before** the terminal `submit`/`fail` transition (lease still live) on both paths.

**Files:**
- Modify: `/Users/devon/Projects/factory-runner/src/factory_runner/cli.py` (`_finalize_workspace`, `finalize_run`, `fail_run`)
- Modify: `/Users/devon/Projects/factory-runner/.github/workflows/factory-runner.yml`
- Test: `/Users/devon/Projects/factory-runner/tests/test_cost_emit.py` (create)

**Interfaces:**
- Consumes: `extract_cost_actuals` (Task 7), `OrchestratorClient.cost_actuals` (Task 8), the `run.json` keys (`attempt`, `lease_token`, `work_unit_id`).

- [ ] **Step 1: Write failing tests** `tests/test_cost_emit.py`. Use a fake/stub client capturing `cost_actuals` calls (mirror how existing `tests/test_cli*.py` stub the client):

```python
# Assert that finalize and fail both call client.cost_actuals exactly once, before the
# terminal submit/fail call, with the extracted values (or cost_known=False when no file).
# Follow the existing CLI-test harness in tests/ for constructing workspace + stub client.
```

Write concrete assertions against whatever stub pattern the repo already uses (open `tests/` to find it). At minimum: (a) success path calls `cost_actuals(cost_known=True, ...)` before `submit`; (b) failure path calls `cost_actuals(...)` before `fail`; (c) a `--execution-file` pointing nowhere yields `cost_known=False`.

- [ ] **Step 2: Run to verify failure.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_emit.py -v`
Expected: FAIL.

- [ ] **Step 3a: Add an `--execution-file` option** to `finalize_run` and `fail_run` (typer options, defaulting to `None`), and thread it into `_finalize_workspace` and the `fail_run` body. In `_finalize_workspace`, immediately before `client.submit(...)`:

```python
    _emit_cost_actuals(client, work_unit_id, attempt, str(run["lease_token"]), execution_file)
```

In `fail_run`, immediately before `client.fail(...)`:

```python
    _emit_cost_actuals(client, work_unit_id, attempt, str(run["lease_token"]), execution_file)
```

- [ ] **Step 3b: Add the helper** in `cli.py`:

```python
def _emit_cost_actuals(client, work_unit_id, attempt, lease_token, execution_file):
    from factory_runner.coding_result import extract_cost_actuals

    if execution_file:
        actuals = extract_cost_actuals(Path(execution_file))
    else:
        from factory_runner.coding_result import _UNKNOWN_COST

        actuals = _UNKNOWN_COST
    client.cost_actuals(
        work_unit_id,
        attempt=attempt,
        lease_token=lease_token,
        cost_known=actuals.cost_known,
        llm_calls=actuals.llm_calls,
        num_turns=actuals.num_turns,
        input_tokens=actuals.input_tokens,
        output_tokens=actuals.output_tokens,
        cost_usd=actuals.cost_usd,
        idempotency_key=f"factory-runner:{work_unit_id}:cost:a{attempt}",
    )
```

- [ ] **Step 3c: Wire the workflow.** In `.github/workflows/factory-runner.yml`, pass `--execution-file "${{ steps.coding.outputs.execution_file }}"` to BOTH the `finalize-run` and `fail-run` invocations. (The `report_failure` step already runs on `always()` after coding; `steps.coding.outputs.execution_file` is in scope there.)

- [ ] **Step 4: Run tests.**

Run: `cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_emit.py -v`
Expected: PASS.

- [ ] **Step 5: Full runner suite + commit.**

```bash
cd /Users/devon/Projects/factory-runner && python -m pytest 2>&1 | tail -15
git add src/factory_runner/cli.py .github/workflows/factory-runner.yml tests/test_cost_emit.py && git commit -m "feat(wsp24): emit cost actuals on success and failure paths"
```

Read the collected count; confirm the suite is green.

---

### Task 10: Cross-repo verification, review, and handoff-to-deploy

Prove both sides agree and the full gates pass; leave the branches ready for Devon's review and the Devon-gated deploy.

**Files:** none (verification only)

- [ ] **Step 1: Cross-repo contract agreement.** Confirm both contract tests pass with the same SHA and the fixtures are byte-identical:

```bash
diff /Users/devon/Projects/orchestrator/tests/fixtures/runner_cost_actuals.json /Users/devon/Projects/factory-runner/tests/fixtures/runner_cost_actuals.json && echo IDENTICAL
cd /Users/devon/Projects/orchestrator && .venv/bin/pytest tests/contract/test_cost_actuals_contract.py -v
cd /Users/devon/Projects/factory-runner && python -m pytest tests/test_cost_actuals_contract.py -v
```

- [ ] **Step 2: Orchestrator full gate.** `cd /Users/devon/Projects/orchestrator && make check 2>&1 | tail -30` — green, `collected N items` read, and confirm `test_authority_write_once.py` passed untouched.

- [ ] **Step 3: Runner full gate.** `cd /Users/devon/Projects/factory-runner && python -m pytest 2>&1 | tail -20` — green, collected count read.

- [ ] **Step 4: `/code-review`** on each repo's branch diff; address correctness/simplification findings.

- [ ] **Step 5: Independent adversarial review** (a fresh agent, budget for kills) of the whole branch on each side, specifically probing: does the ingestion actually commit (not flush)? does a re-emit produce a clean no-op rather than a 500? is any input path able to reach an unhandled 500? is the emit truly before the terminal transition on the failure path? does `_cost`/`_tokens` ever fabricate from the ceiling? Fix what survives.

- [ ] **Step 6: Push both branches; open PRs; hand off to Devon** for review + the Devon-gated deploy. Deploy discipline (orchestrator): amd64/multi-arch build, migrate-first (none needed here — no schema change — but run `alembic upgrade head` as the standard step), verify running `RepoDigest` == pushed digest, then confirm `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json;print('/api/v1/work-units/{unit_id}/cost-actuals' in json.load(sys.stdin)['paths'])"` prints `True`. The runner side deploys by merging; verify a real dispatched attempt records a cost event before declaring done. **MERGED ≠ DEPLOYED.**

---

## Self-Review

**Spec coverage:**
- Event storage (`attempt.cost_recorded`) → Tasks 3, 5. ✓
- Runner emits, orchestrator validates+persists → Tasks 3/4 (ingest), 7/8/9 (emit). ✓
- SHA-pinned cross-repo contract in both repos → Tasks 1, 8. ✓
- Both success + failure paths, emit-before-transition → Task 9. ✓
- Honest `cost_known=false` for missing transcripts → Tasks 7, 9; `partial` SLO status → Task 5. ✓
- `llm_calls` = assistant-record count; `cost_usd` = `total_cost_usd` → Task 7. ✓
- Claim-gated, DomainError-validated, commits, idempotent → Tasks 3, 4. ✓
- Replace `not_instrumented` guard deliberately → Task 5. ✓
- Public-surface drill → Task 6. ✓
- No envelope mutation / no `KNOWN_FIELDS` change → enforced by Global Constraints + Task 10 Step 2 (write-once test stays green). ✓
- No fabricated cost → Task 5 (`no_data` when empty). ✓

**Deviations from spec, flagged:** (a) claim-gated instead of "not lease-gated" — more secure, reuses the validated guard, failure-path handled by emit-before-transition; (b) no migration (free-form `Event.action`); (c) no `expected_version`; (d) aggregation index deferred (YAGNI — `improvisation` runs unindexed). All improvements discovered by grounding in the real code.

**Placeholder scan:** Task 9 Step 1 leaves the stub-client assertions to the repo's existing CLI-test harness rather than inventing a stub that may not match — this is a deliberate "match the existing pattern" instruction, not an undefined step; the concrete behaviors to assert are enumerated. Two "verify the idiom against existing code" notes (JSONB accessor in Task 5; `_raise_error` wrapper in Task 4) are guardrails against a pattern I could not fully confirm from extraction, with the fallback spelled out.

**Type consistency:** `CostActuals` fields (Task 7) ↔ `cost_actuals` client kwargs (Task 8) ↔ `CostActualsCommand` fields (Task 2) ↔ event payload keys (Task 3) ↔ SLO reads (Task 5) — all use the same names: `cost_known, llm_calls, num_turns, input_tokens, output_tokens, cost_usd`. ✓
