# Design: Cost-Actuals Capture (WS-P2.4 Increment 1)

- **Status:** approved design, ready for implementation planning
- **Date:** 2026-07-24
- **Workstream:** WS-P2.4 Cost Controls — Increment 1 of 2 (the prerequisite)
- **Supersedes / realizes:** `docs/superpowers/specs/2026-07-23-wsp22-cost-actuals-capture-proposal.md`
  (that proposal deliberately left "event vs table" open; this design decides it)
- **Repos:** `AlobarQuest/orchestrator` (ingest + SLO) and `AlobarQuest/factory-runner` (emit)

## Why

WS-P2.4 is "enforce a budget against reality." Today there is no reality to enforce
against: no per-attempt **actual** LLM consumption is persisted anywhere in the event
store. The only cost-shaped value that exists is a **declared ceiling** —
`WorkUnit.authority.constraints.budgets.max_llm_calls` (`kernel/authority.py:23,29`) —
and nothing ever compares an attempt's real consumption to it. Consequently the WS-P2.2
SLO metrics `_cost` / `_tokens` return `not_instrumented` by design
(`services/slo_report.py:325-340`), pinned by a guard test.

This increment creates that reality: it persists real per-attempt `llm_calls`, token
counts, and dollar cost, emitted by the runner and validated + stored by the
orchestrator, so that (a) the cost SLO metrics compute from real data and (b) Increment 2
(enforcement) has an actual to compare the ceiling against.

**This increment does NOT enforce anything.** No claim-path guard, no estimate line, no
breaker, no token-budget field. Those are Increment 2 and get their own spec.

## Non-goals (explicit boundary)

- **Do not fabricate cost from the declared ceiling.** `_cost` / `_tokens` must compute
  only from recorded actuals; a window with no actuals reports `no_data`, never a number
  derived from `max_llm_calls`. This is the exact anti-pattern WS-P2.2 refused.
- **No enforcement.** Recording actuals must not gate, block, or fail any attempt.
- **No envelope mutation.** This increment writes an event; it never touches
  `WorkUnit.authority`. The write-once envelope test
  (`tests/architecture/test_authority_write_once.py`) must stay green untouched.
- **No `KNOWN_FIELDS` / fingerprint change.** No authority field is added or altered, so
  no authority fingerprint is rewritten.

## Key decisions (settled during brainstorming)

1. **Storage = event, not table.** Actuals are recorded as a new `Event`
   (`action="attempt.cost_recorded"`) carrying the numbers in the JSONB `payload`. The
   SLO report is already "a projection over the event store" and aggregates over `events`
   (e.g. the `improvisation` metric); enforcement (Increment 2) will `SUM` per-unit
   actuals the same way `attempt_count` gates `claim_unit` today. Rationale: matches the
   event-sourced grain, keeps cost reconstructable from the log, gives exactly-once for
   free via the unique `idempotency_key` column, and needs no new entity.
2. **Runner emits; orchestrator validates + persists.** The runner is the source of truth
   for its own attempt cost. The orchestrator validates the POST body against a
   SHA-pinned cross-repo contract and appends the event.
3. **Both success and failure paths emit.** Runaway spend manifests as repeated *failing*
   attempts; capturing success-only would make Increment 2 blind to the dominant runaway
   case. Failed attempts emit too.
4. **Honest "unknown", never fabricated zero.** When an attempt has no usable transcript
   (e.g. a timed-out coding action left no terminal `result` record), the event is still
   recorded with `cost_known: false` and null numerics. The attempt's cost is absent, not
   zero. Increment 2 decides the (conservative) enforcement policy for unknowns.

## The runner can already see the numbers

The runner does not call the model itself — `anthropics/claude-code-base-action` does and
emits a stream-json `execution_file`. The runner **already parses that file** in
`factory_runner/coding_result.py::classify_execution_file`, reading only `subtype` /
`is_error` off the terminal `result` record and discarding the usage fields in that same
dict: `usage.input_tokens`, `usage.output_tokens`, `total_cost_usd`, `num_turns`. So
capturing actuals is extracting fields from an artifact already in hand — not new LLM
instrumentation.

Two nuances:
- **`llm_calls` is the count of `assistant` records in the transcript**, not `num_turns`.
  Claude Code reports agentic *turns*, not HTTP calls; since the declared budget field is
  `max_llm_calls`, the actual must be a faithfully-named call count. `num_turns` is
  carried alongside as observability (it is also the quantity the runner's current
  `max_turns` literal bounds).
- **`cost_usd` is Claude Code's own `total_cost_usd`** — authoritative dollars, computed
  from its own pricing; the orchestrator does no pricing math.

## Components and data flow

```
factory-runner attempt completion
  (_finalize_workspace on success;  fail-run/report_failure on failure)
        │  parse execution_file (already parsed for classify)
        │  extract: assistant-record count, usage.input/output_tokens,
        │           total_cost_usd, num_turns  (or cost_known=false if no transcript)
        ▼
  OrchestratorClient.cost_actuals(...)   # new; modeled on pr_binding
        │  POST /api/v1/work-units/{id}/cost-actuals   (runner-role M2M)
        ▼
orchestrator route  (api/routes.py)
        │  validate up front -> DomainError on any malformed input
        ▼
service  services/cost_actuals.py::record_cost_actuals(...)
        │  append Event(action="attempt.cost_recorded", ...); session.commit()
        ▼
events table  (idempotency_key unique -> exactly-once; re-emit is a no-op)
        ▲
        │  SUM / aggregate over window
services/slo_report.py::_cost, ::_tokens  -> real MetricValue (computed | no_data | partial)
```

### 1. Wire contract (cross-repo, SHA-pinned — highest-risk part)

New endpoint: `POST /api/v1/work-units/{unit_id}/cost-actuals`, runner-role M2M
(`Authorization: Bearer` + `X-Credential-Key-Id`). Request body:

```json
{
  "attempt": 2,
  "cost_known": true,
  "llm_calls": 37,
  "num_turns": 12,
  "input_tokens": 812004,
  "output_tokens": 41220,
  "cost_usd": 9.14,
  "idempotency_key": "factory-runner:<unit>:cost:a2"
}
```

When `cost_known` is `false`, all numeric fields are `null`.

**Auth is claim-gated** (refined during planning, from the real code). Cost-actuals reuses
the exact `_authorize_write` guard `pr_binding` uses: a WORKER holding the unit's live claim
(`validate_active_claim`), or SYSTEM. This binds each write to the claim holder — the same
integrity property `pr_binding` deliberately enforces, and it matters more here because
Increment 2 *trusts these numbers to halt units*. The failure-path concern that first
suggested a non-lease-gated design is instead solved by **emitting before the terminal
`fail`/`submit` transition, while the lease is still live** (the runner renews its lease
throughout the run). No `expected_version`: this is an append with no optimistic-concurrency
target, so the command carries only `idempotency_key`. Idempotency is enforced by the unique
`events.idempotency_key` — the service pre-checks and also catches `IntegrityError` so a
re-emit is a clean no-op, never an unhandled 500.

**No migration.** `Event.action` is a free-form string (no enum/check), so a new
`attempt.cost_recorded` value is pure code. A supporting aggregation index on `events` is
deferred (the existing `improvisation` metric runs unindexed at this scale — YAGNI).

**Contract pinning:** a fixture `tests/fixtures/runner_cost_actuals.json` (the exact POST
body shape) with a `CONTRACT_SHA256` constant in a contract test, mirrored byte-identically
in **both** repos — `tests/contract/test_cost_actuals_contract.py` here and the
equivalent in factory-runner. Follow the established pattern
(`tests/contract/test_runner_envelope_contract.py`,
factory-runner `tests/test_orchestrator_command_contract.py`): a one-sided edit fails the
repo that was not updated. The runner's production request builder must be asserted to
match the fixture (as the envelope test asserts `RUNNER_CAPABILITIES` derives from its
fixture).

### 2. Orchestrator ingestion

- **Route** (`api/routes.py`): parse and validate every field up front and raise
  `DomainError` on anything malformed — unknown/missing fields, non-int tokens, negative
  values, `attempt` out of range, `cost_known` true with null numerics or false with
  non-null numerics. **No stdlib exception may escape** — only `DomainError` and
  `APIAuthenticationError` have handlers; a bare `ValueError`/`TypeError`/`IntegrityError`
  surfaces as an unhandled 500 (the WS-P2.3 invariant).
- **Service** (`services/cost_actuals.py`): append
  `Event(action="attempt.cost_recorded", subject_type="work_unit", subject_id=unit_id,
  payload={attempt, cost_known, llm_calls, num_turns, input_tokens, output_tokens,
  cost_usd}, idempotency_key=…)` and **`session.commit()`** — this is a request entry
  point that owns its transaction (the flush-vs-commit trap: a flush-only write is visible
  in-session and gone in production). Guard that `unit_id` exists.
- **Idempotency / write-once:** the `events.idempotency_key` unique column makes a re-emit
  a no-op — on a duplicate key, return the already-recorded event (first-wins). Each
  attempt has its own key, so attempts 1..N each get exactly one event; cumulative cost is
  the `SUM` over them.

### 3. SLO report wiring

`services/slo_report.py`:
- `_cost` → aggregate `cost_usd` from `attempt.cost_recorded` events in the window
  (`computed`); `no_data` when the window has no such events; `partial` when some events in
  the window are `cost_known: false`.
- `_tokens` → aggregate `input_tokens + output_tokens` the same way.
- **Replace the guard test deliberately.** `test_cost_and_tokens_are_not_instrumented`
  (`tests/services/test_slo_report.py:187`) is removed and replaced with: a computed-value
  test, a `no_data` test, and a `partial` (some unknown) test. Removing this guard is an
  intended, reviewed part of this increment — not a silent deletion.

### 4. factory-runner emit side

- Extract usage from the terminal `result` record already isolated in
  `coding_result.py` (count `assistant` records for `llm_calls`; read `usage.*`,
  `total_cost_usd`, `num_turns`). Handle a missing/partial transcript by emitting
  `cost_known: false`.
- Thread the `execution_file` path into **both** `finalize-run` and `fail-run`
  (the workflow currently passes it only to `classify-coding-result`).
- Add `OrchestratorClient.cost_actuals(...)` modeled on `pr_binding` (`client.py:172`),
  idempotency key `f"factory-runner:{unit}:cost:a{attempt}"`.
- Call it from the tail of `_finalize_workspace` (success) and from the failure path
  (`fail_run`/`report_failure`).
- This is the natural moment the runner finally *reads* `budgets.max_llm_calls` — but only
  to carry/compare in **Increment 2**; Increment 1 only records the actual.

## Testing & verification

- **Cross-repo contract test in both repos** with the shared `CONTRACT_SHA256`.
- **Ingestion tests (orchestrator):** happy path asserts persistence via
  `expire_all()` + re-read (never in-session); idempotent re-emit; `cost_known: false`
  path; a negative/validation test per rejected input proving a clean 4xx (`DomainError`),
  not a 500; commit actually happens.
- **SLO tests:** computed, `no_data`, and `partial` cases.
- **Public-surface drill:** drive the real endpoint end-to-end (POST actuals → event
  persisted → `slo-report` computes real `_cost`/`_tokens`) rather than only unit-testing
  the service — the WS-P2.1 reachability lesson (a service with only test callers is
  dead). Confirm the endpoint exists in production's `openapi.json` after deploy.
- **Runner tests:** usage extraction from a representative transcript fixture; the
  `cost_known: false` no-transcript case; the client POST shape matches the contract
  fixture.
- `make check` green **with the collected-test count read** (exit 0 alone proves nothing —
  code 5 = no tests collected is swallowed); `ruff format` (not just `ruff check`) before
  commit; `/code-review`; independent adversarial review before merge.
- **Deploy:** Devon-gated, amd64/multi-arch, migrate-first, verify running `RepoDigest`
  == pushed digest, then confirm the new route serves in production. MERGED ≠ DEPLOYED.

## What Increment 2 inherits (out of scope here, noted for continuity)

- Estimated cost/effort line at the decomposition-approval gate.
- Claim-path enforcement: `SUM` per-unit actuals vs the `max_llm_calls` ceiling, in the
  shape of the existing `attempt_count >= max_attempts` guard (`claims.py:71`).
- A fail-closed **budget-breach failure class** that halts the unit at its cap and feeds
  the SLO metrics; whether it also opens the *existing* failure-signature circuit breaker
  (`services/dispatch.py::circuit_open`) is an Increment-2 scope decision.
- The enforcement **policy for `cost_known: false`** attempts (conservative / fail-closed).
- Whether to add a distinct **token budget** field (a new `KNOWN_BUDGETS`/`KNOWN_FIELDS`
  entry — rewrites every authority fingerprint; cheap only while the ledger is near-empty).
- Budget **defaults** seeding (declared ceilings + headroom now, tightened once real
  actuals from *this* increment accrue).
- Exit criterion #12 demonstration (a drill on the public surface, dispatch stays off).
