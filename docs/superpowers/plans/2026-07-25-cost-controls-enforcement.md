# Cost Controls Enforcement Implementation Plan (WS-P2.4 Increment 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce a per-unit LLM-call budget: halt an over-budget unit at its `max_llm_calls` cap via a recorded breach, count breaches in the SLO report, and show projected cost at the decomposition-approval gate.

**Architecture:** A new `services/budget.py` provides pure predicates (cumulative actual `llm_calls` vs the declared ceiling). `claim_unit` calls them and, when over budget, drives a new SYSTEM `READY→FAILED` transition (reason `budget_exceeded`) using its own private `_transition`, then commits and returns a `DomainError` refusal. A `budget_breach` SLO metric counts those transitions; the decomposition-review page sums proposed ceilings.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Pydantic v2, pytest, Postgres (JSONB), Jinja2. Orchestrator repo only.

**Design spec:** `docs/superpowers/specs/2026-07-24-cost-controls-enforcement-design.md`

## Global Constraints

- **Read-only on authority.** Enforcement reads `normalize_authority(unit.authority).budgets.max_llm_calls` (a pure frozen-dataclass read). NEVER write/mutate `WorkUnit.authority`. `tests/architecture/test_authority_write_once.py` must stay green untouched.
- **No new HTTP route, no migration, no `KNOWN_FIELDS`/fingerprint change.** The breach is an existing `events` row; the edge is pure Python; the metric/estimate ride existing routes.
- **WS32 word-ban:** new/edited code under `src/orchestrator/` may not contain the bare words `dispatch` or `deploy` in identifiers OR string literals (incl. docstrings). `budget_exceeded` is clean — keep prose clear of the banned words.
- **`claim_unit` owns its transaction and RETURNS `DomainError`** (does not raise to caller). The budget halt must `session.commit()` the FAILED transition and `return DomainError(...)` — NOT `raise` (raising hits the `except DomainError: session.rollback()` path and would undo the halt).
- **Use the private `_transition` helper inside `claim_unit`, never the public `transition_unit`** (the public one commits internally and would break `claim_unit`'s transaction). The `(READY, FAILED)` SYSTEM edge must exist first (Task 1) or `_transition`'s `authorize_transition` rejects it.
- **`max_llm_calls` is `int | None`; `None` = no ceiling = no enforcement** (never halt a unit with no declared ceiling).
- **Cumulative sums only `cost_known=true` `llm_calls`** (unknown-cost attempts are excluded from the sum but remain bounded by `max_attempts`).
- `make check` green **with the collected-test count read AND a clean `git status`** before trusting local green (Increment-1 lesson: an uncommitted edit is a false green — verify `git status --short` shows no unstaged source). `ruff format` (NEVER on `.json`). Read the collected count. `/code-review` + independent adversarial whole-branch review before merge.

---

### Task 1: Kernel — the `READY → FAILED` SYSTEM edge

Add the single new legal edge and update the four sites that pin the edge set. No behavior yet — just make the edge legal for a SYSTEM actor.

**Files:**
- Modify: `src/orchestrator/kernel/states.py` (`LEGAL_EDGES`)
- Modify: `src/orchestrator/kernel/transitions.py` (`SYSTEM_EDGES`)
- Modify: `tests/kernel/canonical_expectations.py` (`EXPECTED_EDGE_ROLES`)
- Modify: `tests/kernel/test_state_graph.py` (hard-coded counts)

**Interfaces:**
- Produces: `(WorkUnitState.READY, WorkUnitState.FAILED)` is a legal SYSTEM edge, guardless. Consumed by Task 3.

- [ ] **Step 1: Update the count assertions first (RED).** In `tests/kernel/test_state_graph.py::test_graph_partition_covers_all_169_ordered_state_pairs`, change `29`→`30` and `140`→`139`:

```python
    assert len(all_ordered_pairs) == 169
    assert len(EXPECTED_LEGAL_EDGES) == 30
    assert len(INVALID_EDGES) == 139
    assert EXPECTED_LEGAL_EDGES | INVALID_EDGES == all_ordered_pairs
```

- [ ] **Step 2: Add the edge to the canonical expectation.** In `tests/kernel/canonical_expectations.py::EXPECTED_EDGE_ROLES`, add (place it near the other `READY`/`*→FAILED` entries):

```python
    (WorkUnitState.READY, WorkUnitState.FAILED): SYSTEM,
```

- [ ] **Step 3: Run — expect FAIL** (production `LEGAL_EDGES` doesn't yet contain the edge, so `test_declared_graph_matches_canonical_edges` / `test_transition_authority` fail).

Run: `.venv/bin/pytest tests/kernel/ -q`
Expected: FAIL — `LEGAL_EDGES != EXPECTED_LEGAL_EDGES`.

- [ ] **Step 4: Add the edge to production `LEGAL_EDGES`** (`src/orchestrator/kernel/states.py`) — extend the READY edge group:

```python
    | _edges(WorkUnitState.READY, WorkUnitState.CLAIMED, WorkUnitState.FAILED)
```

(If `_edges` takes one target per call in this codebase, add a sibling `| _edges(WorkUnitState.READY, WorkUnitState.FAILED)` instead — match the actual `_edges` signature you see in the file.)

- [ ] **Step 5: Add the role in `SYSTEM_EDGES`** (`src/orchestrator/kernel/transitions.py`):

```python
    (WorkUnitState.READY, WorkUnitState.FAILED),
```

`EDGE_ROLES` derives automatically and `assert all(EDGE_ROLES.values())` will pass once both sets agree. `authorize_transition` needs no new guard clause for this edge (a bare `TransitionGuards()`, like the other `*→FAILED` SYSTEM edges).

- [ ] **Step 6: Run kernel tests — expect PASS.**

Run: `.venv/bin/pytest tests/kernel/ -q`
Expected: PASS (all).

- [ ] **Step 7: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/kernel/states.py src/orchestrator/kernel/transitions.py tests/kernel/canonical_expectations.py tests/kernel/test_state_graph.py && git add -A src/orchestrator/kernel tests/kernel && git commit -m "feat(wsp24): add SYSTEM READY->FAILED edge for budget halt"
```

---

### Task 2: `services/budget.py` — pure enforcement predicates

Pure, DB-read-only helpers. No transitions here (the halt is driven in `claim_unit`) — keeps these trivially testable.

**Files:**
- Create: `src/orchestrator/services/budget.py`
- Test: `tests/services/test_budget.py` (create)

**Interfaces:**
- Produces: `cumulative_llm_calls(session, unit_id: UUID) -> int`, `declared_ceiling(unit: WorkUnit) -> int | None`, `is_over_budget(session, unit: WorkUnit) -> bool`. Consumed by Tasks 3, 6.

- [ ] **Step 1: Write the failing tests** `tests/services/test_budget.py`. Reuse the claim-setup helpers from `tests/services/test_evidence.py` (a `ready_unit` + `worker()`/`active_claim()` — the same ones `tests/services/test_cost_actuals.py` used). To seed cost, insert `attempt.cost_recorded` events directly (mirror `_add_cost_event` from `tests/services/test_slo_report.py`).

```python
import uuid

from orchestrator.persistence.models import Event, WorkUnit
from orchestrator.services.budget import (
    cumulative_llm_calls,
    declared_ceiling,
    is_over_budget,
)


def _cost_event(session, unit_id, *, llm_calls, cost_known=True):
    session.add(
        Event(
            occurred_at=__import__("orchestrator.clock", fromlist=["TransactionClock"])
            .TransactionClock()
            .now(session),
            actor_id="worker",
            action="attempt.cost_recorded",
            subject_type="work_unit",
            subject_id=unit_id,
            from_state=None,
            to_state=None,
            payload={"attempt": 1, "cost_known": cost_known, "llm_calls": llm_calls if cost_known else None},
            correlation_id=uuid.uuid4(),
            idempotency_key=f"cost-{uuid.uuid4()}",
        )
    )
    session.flush()


def test_cumulative_sums_known_calls(migrated_session, ready_unit):  # ready_unit fixture from conftest
    _cost_event(migrated_session, ready_unit.id, llm_calls=3)
    _cost_event(migrated_session, ready_unit.id, llm_calls=5)
    assert cumulative_llm_calls(migrated_session, ready_unit.id) == 8


def test_cumulative_excludes_unknown_cost(migrated_session, ready_unit):
    _cost_event(migrated_session, ready_unit.id, llm_calls=4)
    _cost_event(migrated_session, ready_unit.id, llm_calls=None, cost_known=False)
    assert cumulative_llm_calls(migrated_session, ready_unit.id) == 4


def test_declared_ceiling_reads_authority(migrated_session, ready_unit):
    # ready_unit's authority carries max_llm_calls; assert it round-trips
    assert declared_ceiling(ready_unit) == ready_unit_max_llm_calls(ready_unit)


def test_over_budget_boundary(migrated_session, ready_unit):
    ceiling = declared_ceiling(ready_unit)
    assert ceiling is not None
    _cost_event(migrated_session, ready_unit.id, llm_calls=ceiling - 1)
    assert is_over_budget(migrated_session, ready_unit) is False
    _cost_event(migrated_session, ready_unit.id, llm_calls=1)  # now == ceiling
    assert is_over_budget(migrated_session, ready_unit) is True


def test_no_ceiling_never_over_budget(migrated_session, ready_unit_no_ceiling):
    _cost_event(migrated_session, ready_unit_no_ceiling.id, llm_calls=10_000)
    assert declared_ceiling(ready_unit_no_ceiling) is None
    assert is_over_budget(migrated_session, ready_unit_no_ceiling) is False
```

> Adapt fixture names to the repo's real ones. If there's no `ready_unit_no_ceiling`, build a unit whose authority has `max_llm_calls=None` (see `tests/services/test_slo_report.py::_build_unit` + `AuthorityBudgets(max_attempts=3, max_llm_calls=None)`). Replace `ready_unit_max_llm_calls(...)` with the literal the fixture uses (e.g. `4`).

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: services.budget`).

Run: `.venv/bin/pytest tests/services/test_budget.py -q`

- [ ] **Step 3: Implement** `src/orchestrator/services/budget.py`:

```python
"""Per-unit LLM-call budget predicates (WS-P2.4 Increment 2).

Pure, read-only: sum the unit's actual llm_calls from attempt.cost_recorded events and
compare to the declared max_llm_calls ceiling. Never writes WorkUnit.authority -- the ceiling
is read through normalize_authority, a pure frozen-dataclass projection.
"""

import uuid

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import Event, WorkUnit

_COST_ACTION = "attempt.cost_recorded"


def cumulative_llm_calls(session: Session, unit_id: uuid.UUID) -> int:
    """Sum actual llm_calls across the unit's cost-known attempts. Unknown-cost attempts
    (cost_known=false, llm_calls=null) are excluded and remain bounded by max_attempts."""
    total = session.scalar(
        select(func.coalesce(func.sum(cast(Event.payload["llm_calls"].astext, Integer)), 0)).where(
            Event.action == _COST_ACTION,
            Event.subject_type == "work_unit",
            Event.subject_id == unit_id,
            Event.payload["cost_known"].astext == "true",
        )
    )
    return int(total or 0)


def declared_ceiling(unit: WorkUnit) -> int | None:
    """The unit's declared max_llm_calls, or None when no ceiling is declared."""
    return normalize_authority(unit.authority).budgets.max_llm_calls


def is_over_budget(session: Session, unit: WorkUnit) -> bool:
    ceiling = declared_ceiling(unit)
    if ceiling is None:
        return False
    return cumulative_llm_calls(session, unit.id) >= ceiling
```

- [ ] **Step 4: Run — expect PASS.**

Run: `.venv/bin/pytest tests/services/test_budget.py -v`

- [ ] **Step 5: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/budget.py tests/services/test_budget.py && git add src/orchestrator/services/budget.py tests/services/test_budget.py && git commit -m "feat(wsp24): budget predicates (cumulative llm_calls vs ceiling)"
```

---

### Task 3: Enforce at `claim_unit` — halt + record breach

When a claim is attempted on an over-budget READY unit, drive `READY→FAILED(budget_exceeded)` via the private `_transition`, commit, and return the refusal.

**Files:**
- Modify: `src/orchestrator/services/claims.py::claim_unit`
- Test: `tests/services/test_claims_budget.py` (create; or extend the existing claims test module — match where claim tests live)

**Interfaces:**
- Consumes: `is_over_budget` (Task 2), the `(READY, FAILED)` SYSTEM edge (Task 1), the existing private `_transition` in `claims.py`.

- [ ] **Step 1: Write failing tests.** Use the same claim-setup harness the existing claims tests use (a READY unit + a WORKER actor; seed `attempt.cost_recorded` events to push cumulative to/over the ceiling).

```python
# test_over_budget_claim_halts_and_records_breach:
#   seed cost events summing >= ceiling; call claim_unit as WORKER;
#   assert the returned value is a DomainError with code "budget_exceeded";
#   re-read the unit -> state == "failed";
#   a work_unit.transitioned event exists with to_state="failed" and payload["reason"]=="budget_exceeded".
# test_under_budget_claim_succeeds:
#   seed cost < ceiling; claim_unit returns a LeaseGrant; unit state == "claimed".
# test_no_ceiling_claim_succeeds:
#   unit with max_llm_calls=None + large cost; claim_unit returns a LeaseGrant.
# test_unknown_cost_does_not_trip_budget:
#   seed cost_known=false events (llm_calls large-but-null); under known-ceiling -> claim succeeds.
```

Write these against the real harness (open the existing claims test file to reuse its unit/actor builders and its assertion of `isinstance(result, DomainError)` / `result.code`).

- [ ] **Step 2: Run — expect FAIL** (claim succeeds even when over budget).

- [ ] **Step 3: Implement** the guard in `claim_unit`, immediately AFTER the `attempts_exhausted` check and BEFORE `now = TransactionClock().now(session)` / claim creation. Add the import `from orchestrator.services.budget import is_over_budget` (module-level):

```python
        if unit.attempt_count >= unit.max_attempts:
            raise DomainError("attempts_exhausted", "attempt budget is exhausted", "approve_retry")
        if is_over_budget(session, unit):
            # Halt at the cap and record the breach, then refuse. Driving the failure through the
            # private _transition keeps it inside this function's transaction (the public
            # transition_unit commits and would break it). We commit the halt and RETURN the
            # error -- raising would hit the except-rollback below and undo the halt.
            now = TransactionClock().now(session)
            _transition(
                session,
                unit,
                WorkUnitState.FAILED,
                actor=ActorContext(actor.actor_id, ActorRole.SYSTEM),
                idempotency_key=f"{idempotency_key}:budget-halt",
                occurred_at=now,
                payload={"reason": "budget_exceeded"},
            )
            session.commit()
            return DomainError(
                "budget_exceeded", "llm-call budget is exhausted", "approve_retry"
            )
```

> Verify the private `_transition` signature against the existing READY→CLAIMED call in `claim_unit` (same file) — match its exact parameters (`actor=`, `idempotency_key=`, `occurred_at=`, `payload=`). The `payload={"reason": ...}` must land where the SLO metric reads it — confirm the transition event's payload includes `reason` (Task 4 reads `payload["reason"].astext`); if `_transition` puts the reason elsewhere, align the two. If `_transition` already flushes (not commits), the explicit `session.commit()` here is what persists the halt.

- [ ] **Step 4: Run — expect PASS.**

Run: `.venv/bin/pytest tests/services/test_claims_budget.py -v`

- [ ] **Step 5: Regression — the existing claims suite still green.**

Run: `.venv/bin/pytest tests/services/test_claims.py -q` (and any other claim test modules)
Expected: PASS (the guard only fires for over-budget units).

- [ ] **Step 6: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/claims.py tests/services/test_claims_budget.py && git add src/orchestrator/services/claims.py tests/services/test_claims_budget.py && git commit -m "feat(wsp24): claim_unit halts over-budget units (budget_exceeded breach)"
```

---

### Task 4: `budget_breach` SLO metric (+ delete dead `STATUS_NOT_INSTRUMENTED`)

Count `budget_exceeded` failure transitions in the window; surface on the existing SLO report. Fold in the Increment-1 dead-constant cleanup (same file).

**Files:**
- Modify: `src/orchestrator/services/slo_report.py` (dataclass field, constructor wiring, `_budget_breach`, delete `STATUS_NOT_INSTRUMENTED`)
- Modify: `src/orchestrator/api/schemas.py` (`SloReportResponse` field)
- Test: `tests/services/test_slo_report.py` (add metric tests), `tests/api/test_slo_report_api.py` (response includes field)

**Interfaces:**
- Consumes: the `budget_exceeded` transition event (Task 3).
- Produces: `SloReport.budget_breach: MetricValue`, `SloReportResponse.budget_breach`.

- [ ] **Step 1: Write failing tests** in `tests/services/test_slo_report.py`. Extend `_add_event` with a `reason=None` kwarg so its payload carries `reason` (needed for the metric):

```python
# in _add_event, change payload to include reason:
#   payload={"actor_role": actor_role, "reason": reason},
# and add `reason=None` to its signature.

def test_budget_breach_counts_in_window(migrated_session):
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 7, 8, tzinfo=UTC)
    _, unit = _build_unit(migrated_session, "breach")
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="failed", from_state="ready",
               occurred_at=datetime(2026, 7, 3, tzinfo=UTC), reason="budget_exceeded")
    _add_event(migrated_session, unit.id, action="work_unit.transitioned",
               to_state="failed", from_state="executing",
               occurred_at=datetime(2026, 7, 4, tzinfo=UTC), reason="work_unit_failed")  # not a breach
    migrated_session.commit()
    report = slo_report(migrated_session, SloReportFilters(since=since, until=until))
    assert report.budget_breach.status == STATUS_COMPUTED
    assert report.budget_breach.value == 1.0


def test_budget_breach_no_data_when_none(migrated_session):
    report = slo_report(migrated_session)
    assert report.budget_breach.status == STATUS_NO_DATA
```

- [ ] **Step 2: Run — expect FAIL** (`SloReport` has no `budget_breach`).

- [ ] **Step 3: Implement.** In `services/slo_report.py`: add `budget_breach: MetricValue` to the `SloReport` dataclass (after `improvisation`); wire `budget_breach=_budget_breach(session, since, until, now)` in `slo_report()`; add:

```python
def _budget_breach(session, since, until, now) -> MetricValue:
    breaches = (
        session.scalar(
            select(func.count(Event.id)).where(
                Event.action == "work_unit.transitioned",
                Event.to_state == "failed",
                Event.payload["reason"].astext == "budget_exceeded",
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        or 0
    )
    if breaches == 0:
        return MetricValue(STATUS_NO_DATA, None, "no llm-call budget breaches occurred in the window")
    return MetricValue(
        STATUS_COMPUTED, float(breaches), f"{breaches} unit(s) halted at their llm-call cap in the window"
    )
```

Then DELETE the now-dead `STATUS_NOT_INSTRUMENTED` constant (grep `src/` and `tests/` first; if the only references are its definition and the stale test-name substring `test_empty_store_reports_no_data_and_not_instrumented`, remove the constant and, if that test imports it, drop the import / fix the stale name).

- [ ] **Step 4: Add the response field.** In `api/schemas.py::SloReportResponse`, add after `improvisation`:

```python
    budget_breach: MetricValueResponse
```

- [ ] **Step 5: Add the API test** in `tests/api/test_slo_report_api.py` asserting the response JSON has a `budget_breach` object with a `status`.

- [ ] **Step 6: Run.**

Run: `.venv/bin/pytest tests/services/test_slo_report.py tests/api/test_slo_report_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/services/slo_report.py src/orchestrator/api/schemas.py tests/services/test_slo_report.py tests/api/test_slo_report_api.py && git add -A src/orchestrator/services/slo_report.py src/orchestrator/api/schemas.py tests/services/test_slo_report.py tests/api/test_slo_report_api.py && git commit -m "feat(wsp24): budget_breach SLO metric; drop dead STATUS_NOT_INSTRUMENTED"
```

---

### Task 5: Estimated-cost line at the decomposition gate

Sum proposed units' `max_llm_calls` in the projection; render it on the review page.

**Files:**
- Modify: `src/orchestrator/web.py::_decomposition_proposal_projection`
- Modify: `src/orchestrator/templates/decomposition_proposal.html`
- Test: `tests/web/test_decomposition_review.py` (or wherever the decomposition-review page is tested — match the repo)

**Interfaces:**
- Consumes: the per-unit `normalize_authority(unit.authority).normalized()["budgets"]["max_llm_calls"]` already available in the projection.

- [ ] **Step 1: Write a failing test** asserting the projection context carries a projected total. Find the existing test that exercises `_decomposition_proposal_projection` / the GET page and add:

```python
# Build a proposal with 2 proposed units (max_llm_calls 4 and 6, say);
# call _decomposition_proposal_projection (or GET the page) and assert:
#   context["projected_llm_calls"] == 10
#   context["units_without_ceiling"] == 0
# And a case with a None-ceiling unit -> that unit excluded from the sum, counted separately.
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** in `_decomposition_proposal_projection`. Compute over `proposal_units` (before building the return dict), handling `None`:

```python
    ceilings = [
        normalize_authority(unit.authority).budgets.max_llm_calls for unit in proposal_units
    ]
    projected_llm_calls = sum(c for c in ceilings if c is not None)
    units_without_ceiling = sum(1 for c in ceilings if c is None)
```

Add both keys to the returned context dict:

```python
        "projected_llm_calls": projected_llm_calls,
        "units_without_ceiling": units_without_ceiling,
```

- [ ] **Step 4: Render it** in `templates/decomposition_proposal.html`, right after the "Proposed units" table (after its closing `</table>`):

```html
<p class="projected-cost">Projected LLM-call budget: <strong>{{ projected_llm_calls }}</strong>
  across {{ units|length }} unit(s){% if units_without_ceiling %} — {{ units_without_ceiling }} with no declared ceiling (excluded){% endif %}.</p>
```

- [ ] **Step 5: Run.**

Run: `.venv/bin/pytest tests/web/test_decomposition_review.py -v` (adjust path)
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format src/orchestrator/web.py tests/web/test_decomposition_review.py && git add -A src/orchestrator/web.py src/orchestrator/templates/decomposition_proposal.html tests/web/test_decomposition_review.py && git commit -m "feat(wsp24): projected LLM-call budget at the decomposition gate"
```

---

### Task 6: Exit-#12 drill (public surface)

Prove end-to-end: over-budget → halt + recorded breach → SLO counts it, driven through the public API/CLI (dispatch off).

**Files:**
- Test: `tests/drills/test_budget_enforcement_drill.py` (create; or `tests/api/` if that's where DB-client drills live — mirror `tests/api/test_cost_actuals_drill.py`)

**Interfaces:** consumes the cost-actuals route, the claim route, and the slo-report route.

- [ ] **Step 1: Write the drill.** Reuse the `db_client` + `ready_claimed_unit`/`WORKER`/`SYSTEM` harness. Approach: get a claimable unit with a small `max_llm_calls` (the standard fixture authority uses `max_llm_calls=4`); POST `attempt.cost_recorded` cost-actuals whose `llm_calls` sum ≥ the ceiling (as the claim-holding worker, for the current attempt); then attempt a fresh claim/retry on that unit and assert:

```python
# 1. POST cost-actuals with llm_calls >= ceiling (worker, current claim).
# 2. Drive the unit back toward READY for another attempt (fail the current attempt via the
#    worker fail command, then the SYSTEM/human retry-authorization path the tests already use),
#    OR seed the unit READY with cost events over the ceiling if that's simpler in the harness.
# 3. Attempt POST /claim -> assert 4xx with code "budget_exceeded" (or the DomainError surfaces
#    as the mapped status); re-GET the unit -> state "failed".
# 4. GET /api/v1/slo-report (SYSTEM) -> budget_breach.status in ("computed",) and value >= 1.
```

Keep it driven through HTTP (the WS-P2.1 reachability lesson). If the multi-step retry choreography is heavy, the minimal honest drill is: seed a READY unit + cost-actuals events over the ceiling through the public cost-actuals route, then POST /claim and assert the halt + breach + SLO count.

- [ ] **Step 2: Run.**

Run: `.venv/bin/pytest tests/drills/test_budget_enforcement_drill.py -v`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
cd /Users/devon/Projects/orchestrator && ruff format tests/drills/test_budget_enforcement_drill.py && git add tests/drills/test_budget_enforcement_drill.py && git commit -m "test(wsp24): exit-#12 drill — over-budget unit halts + records breach"
```

---

### Task 7: Full gate, reviews, and handoff

**Files:** none (verification only)

- [ ] **Step 1: Full-repo gate on a CLEAN tree.** Confirm `git status --short` shows no unstaged source, THEN `make check 2>&1 | tail -30`. Read `collected N items` / `N passed` — exit 0 is not proof. Confirm `test_authority_write_once.py`, `tests/kernel/` (edge count), and the new budget/SLO tests are all in the pass set.

- [ ] **Step 2: Confirm no migration + no route added.** `git diff main --stat` shows no `migrations/versions/*` and no new route in `api/routes.py` (enforcement is in `claim_unit`; estimate line is the existing GET). Confirm the POST/GET route-inventory scope guards pass (no new entries needed).

- [ ] **Step 3: `/code-review`** the branch diff; address correctness/simplification findings.

- [ ] **Step 4: Independent adversarial whole-branch review** (fresh agent, most-capable model; budget for kills). Probe specifically: (a) the halt commits and is NOT rolled back by the claim path's `except` (re-read the unit after an over-budget claim in a fresh session); (b) `is_over_budget` never mutates authority (write-once test green); (c) the `None`-ceiling path never halts; (d) unknown-cost attempts don't trip the cap; (e) `_transition` reason lands where `_budget_breach` reads it; (f) the new SYSTEM edge can't be driven by a WORKER/HUMAN (authorize_transition rejects); (g) no `dispatch`/`deploy` prose slipped into new modules. Fix what survives (one fix subagent, complete findings list).

- [ ] **Step 5: Push + open PR; hand off to Devon.** Deploy is Devon-gated (amd64, migrate-first — expected zero migrations, registry bundle byte-identical, digest-verify, then drive the exit-#12 read surfaces where safe). Note in the PR: closes Wave-1 exit #12; production dispatch stays off.

---

## Self-Review

**Spec coverage:** enforce on `max_llm_calls` only → Tasks 2,3 ✓. Breach failure class + SLO metric, no breaker → Tasks 3,4 ✓. Estimate line → Task 5 ✓. Defaults from declared ceilings → inherent (reads the ceiling) ✓. Option A (SYSTEM `READY→FAILED`, halt at claim/retry) → Tasks 1,3 ✓. Unknown-cost policy (sum `cost_known=true` only) → Task 2 ✓. Exit-#12 drill → Task 6 ✓. No envelope mutation / no `KNOWN_FIELDS` / no route / no migration → Global Constraints + Task 7 Step 2 ✓. Dead-constant cleanup folded → Task 4 ✓.

**Deferred (documented, not in this plan to keep the enforcement PR focused):** sentinel `_UNKNOWN_COST`/`_UNKNOWN_COST_ACTUALS` dedup; SYSTEM-bypass/`role_forbidden` cost-actuals coverage; concurrent-idempotency-test fidelity re-point; `cost_per_unit` rename. Flag these in the PR as remaining follow-ups.

**Placeholder scan:** the test steps name real behaviors + assertions but defer to the repo's real fixture names (`ready_unit`, the claims harness, the decomposition-review test location) rather than inventing fixtures that may not exist — a deliberate "match the existing harness" instruction, with the concrete assertions spelled out. Two "verify the private `_transition` signature / reason payload location against the existing call" notes are guardrails against a signature I quote from a sibling call but must be matched exactly.

**Type consistency:** `cumulative_llm_calls -> int`, `declared_ceiling -> int | None`, `is_over_budget -> bool` (Task 2) ↔ used in `claim_unit` (Task 3) and the drill (Task 6). `budget_exceeded` reason string is identical across Task 3 (written), Task 4 (`_budget_breach` reads it), and Task 6 (asserted). `MetricValue`/`MetricValueResponse` field name `budget_breach` identical across dataclass, constructor, serializer, and tests (Task 4). ✓
