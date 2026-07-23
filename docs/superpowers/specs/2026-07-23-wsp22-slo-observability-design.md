# WS-P2.2 — Factory SLOs + observability (design)

Date: 2026-07-23. Status: approved for planning. Repo: `AlobarQuest/orchestrator`.
Program: Phase-2 post-MVP, Wave 1 ([C#7]). Predecessors WS-P2.15, WS-P2.16 shipped + deployed.

## 1. Goal

A **derived, read-only report** projecting factory health from the existing event store — the
on-demand (and later nightly) SLO/observability surface. It is the same shape as the four existing
point-in-time projections (`status_ledger`, `in_flight`, `dead_letter`, `consistency`) but adds the
repo's **first time-windowed aggregation** (rates, latencies, counts over a window).

Wave-1 exit requires "the SLO report runs."

### The one hard rule

**Do not report a metric you cannot compute.** A green report that fabricates or silently
zero-fills a metric with no source is worse than one that says "not instrumented." Every gap is
stated in the report's own output, and a deleted guard must red a named test.

## 2. Non-goals (YAGNI boundaries)

- **No new collectors** beyond the single, Devon-approved first-class improvisation signal (§7).
  The report reads what the system already records.
- Not a metrics pipeline, not Prometheus, not a dashboard, not a published fact. It is a read
  projection: service function → API route → CLI command, mirroring the four existing projections.
- No cost/token instrumentation (§6) — that is a separate, deferred increment.
- Nightly scheduling is a thin wrapper, not this workstream. Ship the on-demand report first.

## 3. Data sources (verified 2026-07-23 against the current tree)

All timing derives from `events.occurred_at` (stamped by `TransactionClock` =
`transaction_timestamp()`, `clock.py:12-14`) and from claim/adjudication timestamps. **State timing
is derived from `work_unit.transitioned` events (from_state/to_state/occurred_at), NEVER from
`work_units.updated_at`** — migration `0001_ws31_core.py:371-384` installs a `BEFORE UPDATE` trigger
`set_work_unit_updated_at()` that rewrites `updated_at = now()` on every row mutation, so it is
"time of last touch," not "time of entering a state."

The event store is `Event` (`persistence/models.py:460-474`): `occurred_at, actor_id, action,
subject_type, subject_id, from_state, to_state, payload (JSONB), correlation_id, idempotency_key`.
It has **no `actor_role` column** and **no revision/unit FK** — the subject is a polymorphic
`(subject_type, subject_id)`.

| Metric | Source (verified) |
|---|---|
| intake → first-work latency | intake ts = `work_package_revisions.registered_at` (`models.py:163`); first claim = `MIN(Claim.acquired_at)` (`models.py:280`). No event→revision FK — join claims → work_units → revision. |
| queue age (time in `ready`) | `ready`-entry = latest `work_unit.transitioned` with `to_state='ready'`; current-in-ready = `WorkUnit.state='ready'`. |
| claim-expiry rate | `Claim` (`models.py:260-286`): total `COUNT(*)`; expired = `COUNT(terminal_reason='lease_expired')` (set `claims.py:272,347`). No dedicated expiry event. |
| waiver frequency | `Adjudication.outcome='waived'` (`models.py:428-444`); also `adjudication.recorded` events (`evidence.py:272`). |
| revert rate | `work_unit.transitioned` with `to_state IN ('revision_required','failed')` after submit/verify. Deploy-revert is **partial only** — reconciliation is divergence-detection, not an explicit revert record; the report marks the deploy-revert dimension `partial`. |
| evidence-completeness % | required ACs = `lifecycle.required_ac_ids(session, revision, unit)` (`lifecycle.py:436`); satisfied predicate = reuse `_SATISFIED_ACS` SQL from `consistency.py:72-93` — do not reinvent. |

## 4. Architecture

Mirror **`status_ledger`** (the only one of the four projections with all four legs; `in_flight`
notably has no CLI command).

- `src/orchestrator/services/slo_report.py` — pure read function
  `slo_report(session, filters: SloReportFilters | None = None) -> SloReport`. Frozen dataclasses
  throughout, one private helper per metric, no writes.
- `api/routes.py` — `GET /api/v1/slo-report` returning a Pydantic `SloReportResponse`, modeled on
  `status_ledger_route` (`routes.py:993`). Read endpoints require the SYSTEM M2M bearer.
- `cli.py` — `@app.command("slo-report")` HTTP client command, modeled on `status-ledger`
  (`cli.py:330`). **`cli.py` is a pure HTTP client that imports zero services** — the CLI calls the
  API, it does not call `slo_report()` directly.
- Tests: `tests/services/test_slo_report.py`, `tests/api/test_slo_report_api.py`,
  `tests/cli/test_slo_report_cli.py`.

### Data shape

```python
@dataclass(frozen=True)
class SloReportFilters:
    since: datetime | None = None   # window start; default = until - default_window
    until: datetime | None = None   # window end; default = now

@dataclass(frozen=True)
class MetricValue:
    status: str            # "computed" | "no_data" | "not_instrumented" | "partial"
    value: float | None    # None unless status == "computed"/"partial"
    basis: str             # short human string naming exactly what was counted
    # optional per-metric detail (counts, numerator/denominator) as needed

@dataclass(frozen=True)
class SloReport:
    since: datetime
    until: datetime
    intake_to_first_work: MetricValue
    queue_age: MetricValue
    claim_expiry_rate: MetricValue
    waiver_frequency: MetricValue
    revert_rate: MetricValue           # basis notes deploy-revert is partial
    evidence_completeness: MetricValue
    cost_per_unit: MetricValue         # always not_instrumented (§6)
    token_consumption: MetricValue     # always not_instrumented (§6)
    improvisation: MetricValue         # first-class count (§7), self-describing coverage
```

### Status semantics (the honesty contract)

- **`computed`** — the window contained source data and a value was derived.
- **`no_data`** — the window was empty for this metric. Never `0`, never a divide-by-zero. A rate
  with a zero denominator is `no_data`, not `0.0`.
- **`not_instrumented`** — no source data exists in the store at all (cost/tokens). The `value` is
  `None` and `basis` states why.
- **`partial`** — computable but with a named blind spot (deploy-revert).

`basis` is mandatory and names exactly what was counted (e.g. `"claims acquired in window: 12;
lease_expired: 3"`). It is the report's self-documentation.

## 5. Tier-1 metrics — definitions

Each is computed over `[since, until)`. Empty window → `no_data`.

- **intake_to_first_work** — for revisions registered in the window, latency from
  `registered_at` to the first `Claim.acquired_at` over that revision's units. Report the **median**
  (skew-resistant; a single stuck revision must not distort the SLO) with `basis` naming the sample
  size. Revisions with no claim yet are excluded from the latency and named in `basis`.
- **queue_age** — for units currently in `ready`, age since the latest `to_state='ready'`
  transition event. Aggregate = **median** age + count. (Point-in-time slice; window bounds the
  "current" set trivially since `ready` units are current.)
- **claim_expiry_rate** — `lease_expired / total claims` acquired in window. Zero claims → `no_data`.
- **waiver_frequency** — count (and rate over total adjudications) of `outcome='waived'` in window.
- **revert_rate** — `work_unit.transitioned` events with `to_state IN ('revision_required','failed')`
  following a submit/verify, over dispatched-or-submitted units in window. `basis` states the
  deploy-revert dimension is `partial` (no explicit deploy-revert record exists).
- **evidence_completeness** — across units active in window, `satisfied required ACs / total
  required ACs` using `required_ac_ids` + `_SATISFIED_ACS`. Units with no required ACs → excluded,
  named in `basis`.

## 6. Cost / tokens → `not_instrumented`

Verified: the store records **no actual token or cost value** anywhere — the only related field is
the declared ceiling `WorkUnit.authority.budgets.max_llm_calls` (`kernel/authority.py:23,29`), never
compared against an actual. `cost_per_unit` and `token_consumption` are therefore hard-coded
`not_instrumented` with a `basis` explaining the absence.

**Guard test:** a named test asserts these render `not_instrumented`. The intent is that if a future
edit ever silently zero-fills them, the test reds. (Concretely: the test asserts `status ==
"not_instrumented"` and `value is None`.)

Deliverables:
- A backlog item (P2) for the actuals-capture increment.
- A one-paragraph scoped proposal in the plan describing that increment: the runner and/or
  orchestrator persists per-attempt actual `llm_calls`/tokens (new table or event payload), enabling
  both this cost SLO and **WS-P2.4 budget enforcement** (shared prerequisite). Explicitly out of
  scope for WS-P2.2.

## 7. Improvisation → first-class signal (`events.improvisation`)

**Decision (Devon, 2026-07-23):** do NOT ship the fragile heuristic union. Add a first-class signal
so the count is true. **Mechanism: a dedicated, typed boolean column** (recommended over a payload
flag or a broad `actor_role` column — see rationale below).

### Definition (Devon, 2026-07-23: "overrides only")

Improvisation = **a HUMAN actor driving a lifecycle transition that is NOT one of the contract's
designed human gates** — i.e. an operator *override*. Verified against `HUMAN_EDGES`
(`kernel/transitions.py:51`), the human-drivable transitions split into:

- **Designed human gates (NOT improvisation):** `(AWAITING_APPROVAL → READY)` (the approval-resume,
  guarded by `approval_recorded`), `(AWAITING_REVIEW → COMPLETED)` and
  `(AWAITING_REVIEW → REVISION_REQUIRED)` (the sanctioned human-review verdict). These *are* the
  declared contract's human decision points; counting them would swamp the signal with healthy
  human-in-the-loop activity.
- **Operator overrides (improvisation):** all `* → CANCELLED` edges (from CLAIMED / EXECUTING /
  AWAITING_APPROVAL / FAILED) and the verifier-bypassing `(SUBMITTED → COMPLETED)` /
  `(VERIFYING → COMPLETED)` edges (a human completing a unit outside the verifier-owned path).

Encoded as: `role is HUMAN AND (source, target) NOT IN DESIGNED_HUMAN_GATES`. New human override
edges (if any are ever added) count by default — the metric fails toward visibility.

### Mechanism (edge-based; no route or command change needed)

The classification is a pure function of `(actor.role, source, target)`, all of which are in hand
inside `_transition_event`. So the stamp lives there — no `TransitionCommand` field, no route change.
Because `EDGE_ROLES` already forbids a HUMAN from driving a non-`HUMAN_EDGE`, and the designed gates
are excluded explicitly, this counts exactly the operator overrides regardless of entry path.

1. **Migration** (`0016_...`): add `events.improvisation BOOLEAN NOT NULL DEFAULT false`. `events` is
   append-only; an additive defaulted boolean is low-risk. Every existing `Event(...)` construction
   (~30 sites) is unaffected — the DB default handles them.
2. **Model:** add the mapped column to `Event` (`persistence/models.py`), `default=False,
   server_default="false"`.
3. **`DESIGNED_HUMAN_GATES`** — a small named `frozenset[Edge]` in `kernel/transitions.py`, beside
   `HUMAN_EDGES`, holding the three designed-gate edges above.
4. **Stamp in `_transition_event`** (`lifecycle.py:176-200`): compute
   `improvisation = command.actor.role is ActorRole.HUMAN and (source, command.target) not in
   DESIGNED_HUMAN_GATES` and pass `improvisation=improvisation` into the `Event(...)`.
5. **Report:** `improvisation` = `COUNT(events WHERE improvisation IS TRUE)` in window. A true count,
   not a scrape. `basis` **names its own coverage**: e.g. `"human operator overrides (cancels +
   verifier-bypass completes): N; designed human gates excluded"` — so a reader knows precisely what
   is and isn't counted.

### Why a column, not the alternatives

- **vs. payload flag:** the column bakes the judgment at write time where role is known, is typed and
  indexable, and avoids joining the *inconsistent* `actor_role`-in-payload pattern (verified present
  for some actions only). Devon explicitly rejected fragility.
- **vs. broad `actor_role` column:** that touches all ~30 emit sites (no choke point), a far larger
  blast radius, and still leaves improvisation = `role=human ∩ {enumerated action set}` at read time
  — reintroducing the "which actions count" fragility. The marker is both more correct *and* lower
  impact.

### Coverage & honesty

v1 instruments exactly one juncture. The column extends without schema change to later junctures
(out-of-band waiver on `adjudication.recorded`, `legacy_manual` activation, HUMAN reconciliation
close) as follow-ups. The report's `basis` always states current coverage, so the metric never
implies more than it measures. Historical events predating the migration are `false` — the report's
window bounds make this honest (the metric measures from instrumentation onward).

## 8. Windowing

- `SloReportFilters(since, until)`; `until` defaults to now, `since` defaults to `until -
  default_window`, where `default_window` is a module constant of **7 days**. The API/CLI accept
  explicit ISO bounds to override.
- All metric queries bound on the appropriate timestamp column within `[since, until)`.

## 9. Testing strategy (TDD)

- **Every computed number gets a test with a hand-constructed event fixture whose expected value is
  hand-verifiable** — a window of known transitions/claims → a known latency/rate. A report that
  agrees with itself proves nothing.
- **Negative controls:** empty window → `no_data` (not `0`, not a crash) for every rate; a
  zero-denominator rate → `no_data`. Cost → `not_instrumented`, provably (delete guard → named test
  reds).
- **`updated_at` trap:** no latency may read `updated_at`. A test that "ages" a row by writing
  `updated_at` silently tests nothing (the trigger overwrites it) — exercise timing by constructing
  `work_unit.transitioned` events with explicit `occurred_at`, or by shrinking a threshold, never by
  ageing a row.
- **Improvisation:** a HUMAN `commands/{command}` call sets the flag and increments the count; a
  SYSTEM/WORKER command does not; the report's `basis` lists its coverage.
- Build on the `migrated_session` fixture (`tests/services/conftest.py:14-39`, drops+recreates schema
  + `alembic upgrade head`). Requires `SECURITY_STANDARDS_DIR`, Postgres on `127.0.0.1:5432`. **Read
  the collected-test count — `make check` exit 0 / exit-5 does not prove tests ran.**

## 10. Deployment note

**MERGED ≠ DEPLOYED.** `sds.alobar.net` runs `4cfa0c8-wsp216-amd64` today; `GET /api/v1/slo-report`
will 404 in production until the image is rebuilt (amd64/multi-arch) and redeployed. The report can
be run read-only against the live event store with the SYSTEM M2M bearer to sanity-check real numbers
before claiming it "works."

**Scope guard:** `tests/architecture/test_ws32_scope_guards.py` scans runtime string literals
(including docstrings) under `src/orchestrator/` for the bare words `dispatch`/`deploy`. The new
`slo_report.py` module prose must avoid those bare words (use synonyms for the revert/deploy-revert
discussion), or add the module to the appropriate allowlist.

## 11. Deliverables checklist

- [ ] `services/slo_report.py` computing Tier-1 metrics, each fixture-tested against a hand-verified
      value; empty-window + single-unit edges covered.
- [ ] `GET /api/v1/slo-report` + `orchestrator slo-report` CLI, with service/API/CLI tests.
- [ ] Cost/tokens `not_instrumented` with guard test + P2 backlog item + scoped actuals-capture
      proposal (shared prerequisite with WS-P2.4).
- [ ] `events.improvisation` migration + model + `DESIGNED_HUMAN_GATES` + `_transition_event` stamp +
      true count in report, coverage self-described in `basis`.
- [ ] `make check` green (collected count read); `/code-review`; independent adversarial review.
- [ ] Wave-1 progress note in `~/docs/software-delivery-system/`.

## 12. Risks / open questions (resolve in plan)

- Confirm no `Event` serialization/contract test breaks on the added column (the outbox
  `_factory_action` mapper keys on `action`, not columns — expected safe; verify).
- Confirm the `AWAITING_APPROVAL → READY` approval-resume and the `AWAITING_REVIEW` review verdicts
  actually flow through `_transition_event` (so `DESIGNED_HUMAN_GATES` genuinely excludes them);
  verify in plan with a test that a recorded approval-resume yields `improvisation=false`.
