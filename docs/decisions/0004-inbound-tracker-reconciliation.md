# ADR 0004 — Inbound tracker reconciliation surfaces divergences as append-only conditions, never transitions

**Date:** 2026-07-27
**Status:** Accepted (decisions confirmed with Devon 2026-07-27)
**Workstream:** WS-P2.7 Increment 2 (inbound tracker reconciliation) — Program Phase 2, Wave 2 (LEGIBLE)
**Companion:** `~/docs/software-delivery-system/2026-07-02-software-factory-master-plan.md` (D7/D8),
program exit criterion #9 ("no tracker is treated as canonical")
**Builds on:** ADR-0003 (tracker projection is outbound-only, out-of-process, Todoist-first)
**Precedent:** ADR-0002 (reconciliation via a separate report-only runner) and its AC-012 amendment
(append-only `reconciliation_conditions`/`resolutions`, operator resolves, detection never
auto-transitions, never inside an ingest transaction)
**Design:** `docs/superpowers/specs/2026-07-27-wsp27-inc2-inbound-reconciliation-design.md`

## Context

ADR-0003 shipped the outbound half: the orchestrator projects canonical work-unit state onto
Todoist as a read-only mirror, structurally unable to change canonical state. It deferred the
**inbound** half — a human's tracker edit flowing back — as the judgment-dense part, carrying the
"what actor role does a tracker-originated transition carry?" question.

Two program invariants continue to bound any solution:

1. **No tracker may ever be canonical** (exit #9 + the YAGNI ledger: no issue-move-as-lock, no
   comments-as-audit, no issue-content-as-authority — ever). The tracker is a projection.
2. **The orchestrator process must not call external mutation integrations.** The `todoist`
   import ban under `src/orchestrator/` and
   `test_application_has_no_external_mutation_integrations` still hold; all Todoist I/O stays in
   the out-of-process adapter.

The danger lives inbound: a human checks off a Todoist card, and that edit must reach the
orchestrator **without ever being applied as a blind lifecycle transition and without making the
tracker canonical**.

## Decision

Increment 2 ships inbound reconciliation as **divergence-as-append-only-condition** — the
ADR-0002 report-only pattern verbatim. The tracker never drives a transition; a human operator,
through the orchestrator's own gated `/review` surface, is the only actor who moves lifecycle
state.

Concretely:

- **Authority shape (b): a tracker edit becomes a reconciliation condition, never a requested
  transition.** The rejected alternative (shape (a) — a tracker edit as a still-human-gated
  *requested transition*) would have required the adapter to gain the generic `commands/{command}`
  endpoint and opened a new "can a machine relay a human's authority?" question, while the human
  still acts twice. Deferred, possibly permanently.

- **Transport: a dedicated SYSTEM-only `POST /api/v1/reconciliation/tracker-detect` route
  (Option 2), not the observation path.** The adapter reports observed per-binding tracker state;
  a new detection function records `tracker_state_divergence` conditions. This keeps trackers
  entirely out of the observation vocabulary, leaving
  `docs/operations/observation-ingestion.md`'s deliberate tracker exclusion (its non-goals name
  "canonicalize trackers"; its do-not-store list names "tracker text… Todoist"; it forbids
  gaining "tracker… authority through this route") untouched. Riding the observation path would
  have added no new route but reopened that injection-containment wall; in this codebase
  mechanical surface (a route) is cheaper than re-litigating a wall.

- **Dumb adapter.** The adapter *reports* observed tracker state (`observed_completed`); the
  orchestrator's detection function owns the divergence *rule*. The tracker's interpretation
  never enters authority.

- **Detection by poll + diff, no new adapter state.** The adapter fetches each bound item's
  current Todoist state via the v1 API (`item_completed`) and reports it; the orchestrator diffs
  against canonical state.

- **Scope: completion-divergence only.** Reopened and deleted cards are out of scope for
  Increment 2.

- **The operator surface is reused unchanged.** The generic `/review` unit page renders any open
  condition and `POST /review/reconciliation/conditions/{id}/resolution` (HUMAN-only, CSRF)
  resolves any condition with accept/correct/dismiss. No new operator code.

## The detection rule and its cross-boundary coupling

The orchestrator records exactly one `tracker_state_divergence` condition when, and only when:

> a bound tracker item is **observed completed** while its work unit's canonical state is **not
> one of the outbound "card-closed" states `{completed, cancelled}`**.

This predicate is the exact mirror of the outbound projection: Increment 1's `plan_actions`
closes a card iff `unit.state ∈ TERMINAL_STATES = {completed, cancelled}` (`failed` is
deliberately excluded — a failed unit can return to `READY`, so its card stays open). Therefore a
completed card is *projection agreement* iff the unit is `completed` or `cancelled` (the
projection itself closed it); a completed card in any other state (including `failed`) is a human
assertion the projection would never have made — surfaced for an operator. Using "not
`COMPLETED`" would false-fire on every `CANCELLED` unit; using the full terminal-with-`failed`
set would miss the failed-unit divergence.

Because this closed set is two copies (`TRACKER_CLOSED_STATES` in the detector,
`TERMINAL_STATES` in the adapter) and the kernel exposes no named terminal set, they are coupled
by a **sync-guard test** (`tests/architecture/test_tracker_closed_states_sync.py`) that asserts
set-equality, and `TRACKER_CLOSED_STATES` is registered in the WS-P2.16 cross-boundary
vocabulary guard. If outbound ever begins closing `failed` cards, inbound must stop flagging
`failed` in lockstep, or it false-fires — the guard forces that.

## Exit-criterion-#9 guarantees (the tracker can never set canonical state)

ADR-0003 stated the adapter's HTTP client permitted a **single** write endpoint. Increment 2
**updates that guarantee to two write endpoints, both provably non-canonical**:

1. The `todoist` import ban in `src/orchestrator/` (unchanged).
2. The adapter client (`orchestrator_client.py`) now permits exactly **two** report-only writes
   via `_is_allowed_write`: the tracker-binding path (`TRACKER_BINDING_PATTERN`, anchored
   `^…\Z`) and the fixed `POST /api/v1/reconciliation/tracker-detect` endpoint (exact string
   equality, immune to substring/path-injection). Both are report-only — a projection binding and
   an append-only reconciliation report. Every lifecycle/command/adjudication/observation path
   stays structurally unreachable, and every write routes through the gated `post()`.
   `test_tracker_projection_adapter_isolation.py` proves the gate allows exactly these two and
   rejects `commands/ready`, `evidence`, `/observations`, `adjudications` (and that a forbidden
   write never reaches the transport).
3. The detection function records only append-only `reconciliation_conditions`; a service test
   proves it never writes `work_units` and never transitions. Detection is fail-open (an unknown
   item is skipped and counted, never raised) and never runs inside an ingest transaction.
4. The adapter isolation test: it imports nothing from `orchestrator.*` and confines third-party
   deps to `{httpx, typer}`.

## Alternatives considered

- **Shape (a): tracker edit as a still-human-gated requested transition.** Rejected for
  Increment 2: needs the adapter to gain `commands/{command}`, opens the machine-relays-authority
  question, and the human still acts twice.
- **Option 1: ride the existing `/api/v1/observations` path.** Rejected: no new route, but it
  reopens the observation layer's deliberate tracker exclusion and would draw adversarial review;
  the dedicated route is the cheaper total cost.
- **Smart adapter (adapter decides the divergence).** Rejected: it would leak the tracker's
  interpretation into authority. The orchestrator owns the rule.

## Deferred (not built in Increment 2)

- Reopened-card and deleted-card divergences. A deleted card also 404s on the read → reported as
  completed → surfaces a condition the operator can dismiss (out of scope, tolerable, fail-safe).
- A scheduler. The inbound pass is operator-invoked
  (`scripts/run-tracker-reconciliation.sh`), mirroring ADR-0002/0003.

## Cost / trade-off accepted

One more operator-invoked pass to run, and the same create-then-record non-atomicity ADR-0003
already accepts on the outbound side (a tracker-tidiness cost only, never a canonical-state one).
The Todoist v1 completion semantics on `GET /tasks/{id}` (404-once-closed vs a completion flag)
were not verified against the live API during the build; `item_completed` handles both branches
defensively and the first live pass will confirm which the API exercises. All of this was judged
well worth a legible inbound path that provably cannot corrupt canonical state.
