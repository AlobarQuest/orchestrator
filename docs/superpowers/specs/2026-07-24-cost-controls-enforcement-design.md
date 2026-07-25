# Design: Cost Controls Enforcement (WS-P2.4 Increment 2)

- **Status:** approved design, ready for implementation planning
- **Date:** 2026-07-24
- **Workstream:** WS-P2.4 Cost Controls — Increment 2 of 2 (enforcement)
- **Builds on:** Increment 1 (Cost-Actuals Capture), shipped + deployed 2026-07-24
  (`docs/superpowers/specs/2026-07-24-cost-actuals-capture-design.md`)
- **Repo:** `AlobarQuest/orchestrator` only (no factory-runner change)
- **Closes:** Wave-1 exit criterion #12 — "a budget-capped work unit demonstrably halts at
  its cap and records the breach."

## Why

Increment 1 made cost *measurable* (real per-attempt `llm_calls` persisted as
`attempt.cost_recorded` events). This increment turns measurement into *control*: a unit
whose cumulative actual `llm_calls` reaches its declared `max_llm_calls` ceiling is halted
at the cap in a recorded breach, and a human approving a decomposition sees the projected
cost. This is deliberately beyond measurement — the control layer neither fabro nor Factory
AI ships.

## Settled decisions

1. **Enforce on `max_llm_calls` only.** Tokens stay observability (Increment 1 captures
   them). No distinct token-budget field — that would add a `KNOWN_BUDGETS`/`KNOWN_FIELDS`
   entry and rewrite every authority fingerprint (the ledger is no longer empty). YAGNI.
2. **Breach = a recorded failure class + SLO metric now; NO circuit-breaker wiring.** A
   failure-signature breaker already exists (`services/dispatch.py::circuit_open`); whether a
   repeated budget breach should open it is a separable control that wants breach data to
   exist first. Out of scope here.
3. **Estimate line at the decomposition-approval gate**, sourced from the declared
   `max_llm_calls` ceilings of the proposed units (+ historical actuals later).
4. **Budget defaults seed from the declared ceilings already in the ledger**; tighten later
   via the WS-P2.14 graduation report. (Dispatch is off, so prod actuals won't accrue yet.)
5. **Halt mechanism = Option A: a new SYSTEM-only state edge `READY → FAILED` (reason
   `budget_exceeded`), driven at the claim/retry point.** `READY → FAILED` is not a legal
   edge today; the existing `attempts_exhausted` guard only *refuses* the claim and records
   nothing, stranding the unit in a `READY`-but-unrunnable limbo. Option A records the breach
   (a transition event the SLO metric counts), halts terminally at the cap, and does not race
   the runner's in-flight submit/fail (the gate fires only when *another* attempt would
   start). Rejected: halt-at-cost-record-time (races the runner's submit), claim-time
   refuse-only (records nothing, strands the unit).
6. **Fail-closed unknown-cost policy.** The cumulative sum counts only `cost_known=true`
   `llm_calls`; `cost_known=false` attempts do not inflate it — but they remain bounded by
   the existing `max_attempts` cap, so a unit cannot loop forever on unmeasured attempts.
   Conservative without over-engineering.

## Non-goals (explicit boundary)

- **No envelope mutation.** Enforcement READS `max_llm_calls` via
  `normalize_authority(unit.authority)` (a pure frozen-dataclass read) and NEVER writes
  `WorkUnit.authority`. `tests/architecture/test_authority_write_once.py` must stay green
  untouched — its docstring names *this very workstream* as its reason to exist.
- **No `KNOWN_FIELDS`/`KNOWN_BUDGETS` change; no authority-fingerprint rewrite.**
- **No new HTTP route.** Enforcement rides the existing claim path; the estimate line rides
  the existing `GET /review/decomposition-proposals/{id}`; the breach metric is a new field
  on the existing `GET /api/v1/slo-report`.
- **No circuit-breaker wiring, no token-budget enforcement, no production dispatch enable.**
- **WS32 word ban:** new modules under `src/orchestrator/` may not contain the bare words
  `dispatch`/`deploy` in identifiers OR string literals (incl. docstrings). `budget_exceeded`
  is clean; keep prose clear of the banned words.

## Components and data flow

```
runner completes attempt N  → POST /cost-actuals  → attempt.cost_recorded event (Increment 1)
                                                          │
next attempt would start (claim / retry-to-READY)         │  SUM(llm_calls) where cost_known
        │                                                 ▼
services/budget.py::cumulative_llm_calls(session, unit) ──┴─→ over ceiling?
        │ no → proceed (claim granted / unit returns to READY)
        │ yes → services/budget.py::halt_over_budget(session, unit)
        │        └─ SYSTEM transition READY→FAILED (reason "budget_exceeded")  [records breach]
        ▼
claim refused with DomainError("budget_exceeded", …) / retry refused
        │
        ▼
services/slo_report.py::_budget_breach  ── counts budget_exceeded transitions → SLO metric
GET /review/decomposition-proposals/{id} ── projected_llm_calls (sum of proposed ceilings)
```

### 1. Kernel: the `READY → FAILED` SYSTEM edge

Add `(READY, FAILED)` to the SYSTEM edge set in `kernel/states.py` (a SYSTEM edge with no
approval guard — the orchestrator-system actor drives it). This is the only kernel change.
If a test pins the exact edge set, update it deliberately with a comment tying the edge to
the budget halt. The reason string carried on the transition is `budget_exceeded`.

### 2. `services/budget.py` — the enforcement primitives (new module)

- `cumulative_llm_calls(session, unit_id) -> int`:
  `SELECT COALESCE(SUM(CAST(payload->>'llm_calls' AS INTEGER)), 0)
   FROM events WHERE action='attempt.cost_recorded' AND subject_type='work_unit'
   AND subject_id=:unit_id AND payload->>'cost_known'='true'` — the Increment-1 `_cost`
  JSONB idiom (`Event.payload["llm_calls"].astext` + `cast(..., Integer)`), filtered by
  subject and `cost_known=true`.
- `declared_ceiling(unit) -> int | None`:
  `normalize_authority(unit.authority).budgets.max_llm_calls` (None = no ceiling).
- `is_over_budget(session, unit) -> bool`: `ceiling is not None and cumulative >= ceiling`.
- `halt_over_budget(session, unit) -> bool`: if over budget, drive the SYSTEM
  `READY → FAILED` transition (reason `budget_exceeded`) through the **public transition
  service** using a system actor context (mirroring how reconciliation/dead-letter drive
  system transitions), commit, and return True; else False. The breach fact is the
  transition event itself (reason `budget_exceeded`) — no ad-hoc second event.

**Actor mechanics:** the claim endpoint is called by the WORKER credential, but the halt is
a SYSTEM edge. The enforcement drives the transition under a **system actor context**
constructed inside the service (the enforcement is system-initiated, not the worker's
action), exactly as other system-driven halts do. The plan must pin the precise
`TransitionCommand`/actor-context call so the transition and write-once guards are honored;
the enforcement never touches `unit.authority`.

### 3. Enforcement at the claim/retry point

In `services/claims.py::claim_unit`, immediately after the existing
`attempts_exhausted` guard region (and before granting the claim), call
`halt_over_budget(session, unit)`; if it halted the unit, refuse the claim with
`DomainError("budget_exceeded", "llm-call budget is exhausted", "approve_retry")` (the unit
is now `FAILED`, so it is no longer claimable — no READY limbo). Mirror the same gate in the
requeue/reclaim eligibility helper (`claims.py:~563`) that exists to refuse landing a unit in
`READY` that `claim_unit` would reject — so an over-budget unit is halted rather than
re-queued to `READY`.

### 4. SLO metric: `budget_breach`

- Add `budget_breach: MetricValue` to the `SloReport` dataclass and wire
  `budget_breach=_budget_breach(session, since, until, now)` in `slo_report()`.
- `_budget_breach` counts `work_unit.transitioned` events in-window whose
  `payload->>'reason' == 'budget_exceeded'` (the `.astext` idiom), returning `STATUS_COMPUTED`
  with the count (or `STATUS_NO_DATA` when zero, consistent with the other counting metrics).
- Update every `SloReport` consumer/serializer and its tests for the new field (the
  `/api/v1/slo-report` response schema and `tests/services/test_slo_report.py` /
  `tests/api/test_slo_report_api.py`).

### 5. Estimate line at the decomposition gate

- In `web.py::_decomposition_proposal_projection`, compute
  `projected_llm_calls = sum(ceiling for each proposed unit where ceiling is not None)` from
  `normalize_authority(unit.authority).budgets.max_llm_calls`, plus a count of units with no
  declared ceiling (so the number is honest — "projected over N of M units; K have no
  ceiling"). Add it to the returned context.
- Render it near the "Proposed units" table in `templates/decomposition_proposal.html`. Keep
  the summation in Python (testable), not Jinja.

### 6. Exit-#12 drill (the proof)

A drill driving the **public API/CLI** (no prod dispatch): register/approve a unit with a
small `max_llm_calls`, ingest `attempt.cost_recorded` cost-actuals whose cumulative
`llm_calls` reaches the ceiling, then attempt a claim/retry and assert: (a) the unit halts to
`FAILED` with reason `budget_exceeded`; (b) the breach transition event exists; (c) the
`/api/v1/slo-report` `budget_breach` metric counts it. This is the exit-#12 evidence, proven
on the public surface (the WS-P2.1 reachability lesson), with dispatch off.

## Testing & verification

- TDD throughout; extend invariant tests, never weaken them.
- **Every guard gets a mutation-tested assertion and a negative control:** over-budget halts
  + records + refuses claim; at-budget vs under-budget boundary; `None` ceiling = no
  enforcement (a unit with no ceiling is never halted); unknown-cost attempts don't inflate
  the sum but are bounded by `max_attempts`; the halt is a legal SYSTEM edge (WORKER cannot
  drive it); `test_authority_write_once.py` stays green (enforcement never writes authority).
- SLO: computed/no_data for `budget_breach`; the drill proves end-to-end.
- Estimate line: projection math (including `None`-ceiling units) unit-tested.
- `make check` green **with the collected-test count read** and a **clean `git status`**
  before trusting local green (Increment-1 lesson: an uncommitted edit is a false green);
  `ruff format` (never on JSON); `/code-review`; independent adversarial whole-branch review.
- **Deploy:** Devon-gated, amd64, migrate-first. **This DOES add a migration only if the
  kernel edge or metric needs a schema change — it does not** (edges are code; the breach is
  an existing `events` row). Confirm no new migration; if the edge is pinned in a DB artifact,
  reconsider. Registry bundle stays byte-identical (no actor change). Verify running digest,
  and drive the exit-#12 drill against production read surfaces where safe.

## Boundary with the deferred follow-ups (fold in here)

Increment 1 left these non-blocking items; this branch is their natural home: delete the dead
`STATUS_NOT_INSTRUMENTED` constant; re-point the concurrent idempotency test at the
SYSTEM-concurrent scenario; add SYSTEM-bypass/`role_forbidden` unit coverage for cost-actuals;
collapse the duplicated `_UNKNOWN_COST`/`_UNKNOWN_COST_ACTUALS` sentinels; clarify that
`cost_per_unit` holds a window sum (rename/basis). Address opportunistically; none blocks.
