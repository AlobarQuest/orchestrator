# WS-P2.7 Increment 2 — Inbound Tracker Reconciliation (design spec)

**Date:** 2026-07-27
**Workstream:** WS-P2.7 Increment 2 — Program Phase 2, Wave 2 (LEGIBLE)
**Program exit criterion:** #9 ("no tracker is treated as canonical")
**Builds on:** Increment 1 (outbound projection) — ADR-0003, and the
`src/tracker_projection_adapter/` package.
**Precedent:** ADR-0002 (reconciliation via a separate report-only runner) and its
AC-012 amendment (append-only `reconciliation_conditions`/`resolutions`, operator resolves,
detection never auto-transitions, never inside the ingest transaction).
**Design predecessor:** `docs/superpowers/specs/2026-07-26-wsp27-tracker-projection-adapter-design.md`

## Problem

Increment 1 made the orchestrator project canonical work-unit state *out* onto Todoist as a
read-only mirror (structurally unable to change canonical state). Increment 2 opens the
**inbound** half: a human edits a tracker item (checks off a card), and that edit must flow
back to the orchestrator **without ever making the tracker canonical and without ever applying
a lifecycle transition blindly**. Inbound is where the danger lives — a tracker edit that
silently moved canonical state would violate exit #9.

## Locked decisions (brainstormed with Devon 2026-07-27)

1. **Authority shape (b): a tracker edit becomes an append-only reconciliation condition, never
   a transition.** The adapter never drives a lifecycle transition. It surfaces that a tracker
   item disagrees with canonical state; a human operator, through the orchestrator's own gated
   `/review` surface, is the only one who ever moves lifecycle state. This is the ADR-0002
   pattern verbatim and requires no new "can a machine relay a human's authority?" reasoning.
   (Shape (a) — tracker edit as a still-human-gated *requested transition* — is deferred; it
   would need the adapter to gain the generic `commands/{command}` endpoint and buys little
   while the human still acts twice.)

2. **Transport: a dedicated tracker-reconciliation detect route (Option 2), not the observation
   path.** The adapter pushes observed tracker state to a new SYSTEM-only route; a new detection
   function records conditions. This keeps trackers entirely out of the observation vocabulary,
   leaving `docs/operations/observation-ingestion.md`'s deliberate tracker exclusion (its
   non-goals name "canonicalize trackers"; its do-not-store list names "tracker text… Todoist";
   line 21 forbids gaining "tracker… authority through this route") untouched. Rejected
   alternative (Option 1, ride `/api/v1/observations` with a new `source_system="todoist"`) would
   have added no new route but reopened that injection-containment wall and drawn adversarial
   review; in this codebase mechanical surface (a route) is cheaper than re-litigating a wall.

3. **Dumb adapter.** The adapter *reports* observed per-binding tracker state; the orchestrator's
   detection function owns the divergence *rule*. The tracker's interpretation never enters
   authority — the adapter never decides what counts as a divergence.

4. **Detection method: poll + diff, no new adapter state.** The adapter fetches each bound item's
   current Todoist state via the v1 API and reports it; the orchestrator diffs against canonical
   state. No Todoist sync/activity API, no adapter-side state store.

5. **Scope: completion-divergence only.** Reopened and deleted cards are out of scope for
   Increment 2.

6. **Pilot-queue disposition: nothing further owed.** ADR-0003 already closed the D8 interim
   documentarily; real non-software work units are WS-P2.13's job.

## The single detection rule

The orchestrator records exactly one `tracker_state_divergence` condition when, and only when:

> a bound tracker item is **observed completed** while its work unit's canonical state is
> **not one of the outbound "card-closed" states `{completed, cancelled}`**.

Rationale — the predicate must be the mirror of the outbound projection. Increment 1's
`plan_actions` (`src/tracker_projection_adapter/projection.py`) closes a card (emits a
`complete` action) exactly when `unit.state ∈ TERMINAL_STATES = {completed, cancelled}`;
`failed` is **deliberately excluded** (a failed unit can return to `READY`, so its card stays
open). Therefore a card observed completed is *projection agreement* iff the unit is `completed`
or `cancelled` — the projection itself closed it. A card observed completed in **any other state**
(`ready`, `claimed`, …, **and `failed`**) is a human asserting completion the projection would
never have made — surface it for an operator. Open cards and every non-completion observation are
agreement or out of scope.

**Critical vocabulary coupling.** This closed set (`{completed, cancelled}`) is the *same* set the
adapter's outbound `TERMINAL_STATES` uses, and the two must not drift: if outbound later begins
closing cards for `failed` too, inbound must stop flagging `failed`, or it false-fires on every
failed unit whose card outbound legitimately closed. The kernel exposes no named terminal set
(`kernel/states.py`), so the orchestrator-side detector defines this set explicitly and a **sync
guard test couples it to the outbound closed-set semantics** — the same discipline as the adapter's
existing `test_vocabulary_sync.py`. Using "not `COMPLETED`" instead would false-fire on every
`CANCELLED` unit; using the full terminal-with-`failed` set would miss the failed-unit divergence.

The decision is grounded in the unit's **live canonical state**, never the binding's
`projected_state` snapshot (that snapshot is stored on the condition as context, not used as the
authority). The tracker is structurally incapable of being canonical here: it can only raise a
flag a human resolves.

## Architecture

Two independent halves, exactly the Increment-1 boundary: the orchestrator process does **zero**
Todoist I/O (the `todoist` import ban under `src/orchestrator/` still holds); all Todoist I/O
lives in the out-of-process adapter, which imports nothing from `orchestrator.*`.

### A. Orchestrator-side (in-process, no Todoist I/O)

**A1. Vocabulary (migration `0019_…` + `persistence/models.py`).**
- `RECONCILIATION_OBSERVATION_KINDS += "tracker"` → `("github_pr", "github_check", "deployment", "tracker")`.
- `RECONCILIATION_CONDITION_TYPES += "tracker_state_divergence"`.
- The existing CHECK construction is `f"col IN {TUPLE!r}"` (`models.py:1093,1097`). Both tuples
  remain ≥2 members after these additions, so the single-element trailing-comma `!r` footgun
  does not apply; keep the existing `!r` form. Update the CHECK in **both** the model
  `__table_args__` and the migration's frozen inline copy (migrations do not import the model
  constants).

**A2. Detection function** `services/reconciliation_detection.py`:
`detect_tracker_conditions(session, actor, *, observed_states) -> DetectionCounters`.
- SYSTEM-gated. Fail-open, never raises: each observed item wrapped in
  `try/except Exception: session.rollback(); counters += SKIPPED`, matching the existing
  detectors' contract. A malformed/unknown item is skipped and **counted**
  (`skipped_correlations`), never silent.
- For each observed state it resolves the binding by `(tracker_system, external_item_id)`, loads
  the unit, applies the rule (`observed_completed ∧ unit.state ∉ {completed, cancelled}`, the set
  held by the sync guard), and records via the existing
  `reconciliation.record_reconciliation_condition` — which commits, dedups by
  `UNIQUE(work_unit_id, observation_kind, normalized_divergence_hash)`, writes **no**
  `work_units` row and performs **no** transition. `observation_id` and
  `deployment_observation_id` stay null (both nullable). Follows the existing
  `resolution_generation` / `divergence_hash(lineage, generation)` pattern so a
  divergence that persists after an operator resolution is handled exactly as the PR/check
  detectors handle theirs.
- `condition.observation_kind = "tracker"`, `condition_type = "tracker_state_divergence"`,
  `stored_state` = `{projected_state, canonical_state}`, `observed_state` = `{completed: true}`.
- Never touches `work_units.state`; a service test proves it.

**A3. New route** `POST /api/v1/reconciliation/tracker-detect` (batch):
- SYSTEM-only via an explicit route-layer role check mirroring `/reconciliation/detect`
  (`routes.py:916`); `expected_version == 0` via `_require_zero_expected_version`.
- Request schema: `{observed_states: [{tracker_system, external_item_id, observed_completed}], …}`
  + `CommandBase` envelope.
- Response schema: the `DetectionCounters` dict
  (`conditions_recorded`, `skipped_correlations`, `suppressed_duplicates`) — reuse/parallel
  `ReconciliationDetectResponse`.
- Calls `detect_tracker_conditions`.
- Guard-family obligations (whole-repo scans run only in a full `make check`): add the path to
  the `test_production_post_route_inventory_is_explicit` set literal
  (`tests/architecture/test_scope_guards.py`); add request/response schemas to `schemas.py`;
  satisfy the every-success-response-has-a-JSON-schema invariant (it returns JSON); add an entry
  to the idempotency coverage matrix matching `/reconciliation/detect`'s treatment. The route
  name contains none of the banned bare words (`deploy`/`dispatch`/`merges`); `routes.py`/
  `schemas.py` are already allowlisted for the ws32 word guard regardless.

**A4. Operator surface: reuse, no new route.** `GET /review/units/{id}` (`web.py:355`) already
renders `open_conditions(...)` for any condition with a per-condition CSRF token, and
`POST /review/reconciliation/conditions/{id}/resolution` (`web.py:709`, HUMAN-only + CSRF)
resolves any condition with `accepted`/`corrected`/`dismissed`. Verify the unit template renders
a `tracker_state_divergence` condition legibly; expect at most a one-line label touch, nothing
structural. No new `/api` or `/review` route.

### B. Adapter-side (`src/tracker_projection_adapter/`, all Todoist I/O)

**B1. New `reconcile` typer command** (`cli.py`, with `--dry-run`):
- Reads bindings via `GET /api/v1/tracker-bindings`.
- Reads each bound item's live Todoist state via the v1 API — a new `TrackerProjector` /
  `TodoistProjector` read method that resolves the item's completion state (a completed Todoist
  task leaves the active list; a missing/completed item is reported as `observed_completed=true`).
- Builds the observed-states batch and POSTs `/api/v1/reconciliation/tracker-detect`.
- `--dry-run` prints the batch it would push without posting.
- **Dumb:** it reports observed state; it never decides a divergence.

**B2. Client allowlist widens to two report-only endpoints.**
`ALLOWED_WRITE_PATTERN` (`orchestrator_client.py:20`, currently the single tracker-binding path)
gains the `/api/v1/reconciliation/tracker-detect` path. Both permitted writes are **report-only**
(they produce only append-only conditions / a projection binding); every
lifecycle/command/adjudication/observation path stays structurally unreachable. This is a
deliberate, documented widening of the ADR-0003 "single write endpoint" guarantee to "**two write
endpoints, both provably non-canonical**."

**B3. No new egress file.** The new methods land on the existing `tracker.py` /
`orchestrator_client.py`, already registered in `OUTBOUND_ALLOWLIST`
(`tests/architecture/test_wsp21_invariant_scan.py`); the adapter isolation test
(`test_tracker_projection_adapter_isolation.py`) still holds — the adapter imports nothing from
`orchestrator.*` and confines third-party deps to `{httpx, typer}`.

**B4. CLI tested through its entrypoint** (`CliRunner`), per the lone-Typer-command-collapse
lesson that shipped a broken launcher in Increment 1 — not just the core function.

### C. Docs / ADR

**New ADR-0004** records the inbound decision (shape b; Option 2 transport; dumb adapter;
completion-only scope) and **updates the exit-#9 guarantee list** from ADR-0003's "single write
endpoint" to "two write endpoints, both provably non-canonical." A test proves the
`tracker-detect` route never changes `work_units.state` — the mechanical backstop that the
tracker stays non-canonical inbound.

## Tests

- **Detection** (`detect_tracker_conditions`): records exactly one condition on
  completion + state ∉ `{completed, cancelled}` (include an explicit **`failed`-unit fires**
  case); records **nothing** when the unit is `completed` **or `cancelled`** (projection
  agreement — the false-fire the predicate exists to avoid); idempotent (re-run suppresses the
  duplicate); unknown `external_item_id` → `SKIPPED`, no raise (fail-open); never mutates
  `work_units.state`.
- **Vocabulary-coupling sync guard**: a test asserting the detector's card-closed set equals the
  outbound `TERMINAL_STATES = {completed, cancelled}`, so the inbound and outbound halves cannot
  drift into a false-fire (mirrors the adapter's `test_vocabulary_sync.py`).
- **Route**: SYSTEM-only (WORKER/HUMAN forbidden); `expected_version == 0` enforced; returns the
  counters JSON with a schema; present in the POST route inventory.
- **Operator**: resolving a `tracker_state_divergence` condition through the existing `/review`
  resolution handler (accept / correct / dismiss).
- **Adapter**: pure reconcile-plan unit tests; `CliRunner` on the `reconcile` command through its
  real invocation; the client allowlist permits `tracker-detect` and still forbids
  commands/adjudication/observation; the isolation test remains green.

### Folded-in deferred Increment-1 minors

1. `test_write_to_a_transition_path_is_forbidden` also asserts the mock transport was never
   reached (`seen == []`).
2. The two `projection.py` skip-tests assert the full `Action` tuple, not only `.kind`.
3. `TodoistProjector.update_item` url-fallback + non-2xx paths get tests; `httpx.Client` is
   context-managed.
4. A WORKER-credential case on the `GET /api/v1/tracker-bindings` test (proves "auth-only", not
   just "SYSTEM").
5. The create-then-record non-atomicity stays a documented ADR cost (tracker-tidiness only,
   never a canonical-state one), no code change.

## Boundary (unchanged, mechanically enforced)

- **Tracker never canonical** (exit #9 + the YAGNI ledger). Inbound edits are signals surfaced
  for a human, never applied blindly, never authority.
- **No Todoist call inside `src/orchestrator/`** — the `todoist` import ban and
  `test_application_has_no_external_mutation_integrations` still hold. All Todoist I/O stays in
  `src/tracker_projection_adapter/`.
- **Conditions append-only, operator decides.** Detection never auto-transitions, never raises,
  never runs inside an ingest transaction.
- **Never store tracker text** as facts/instructions — store normalized state
  (`observed_completed`), not card titles/descriptions (injection containment).
- Closed vocabularies extended via migration + CHECK, never loosened. New route → route-inventory
  + JSON-schema invariant + idempotency matrix. New `src/orchestrator/` prose → the ws32/ws33
  word bans + the `test_unreachable_guards` reachability guard (the new service function must be
  reached by the new route). New adapter egress → `OUTBOUND_ALLOWLIST` (no new file expected).
- `ORCHESTRATOR_DISPATCH_ENABLED=false` in production stays false; this workstream does not touch
  dispatch.

## Deploy (Devon-gated)

Orchestrator-side additions (vocab migration, detector, route) need a prod image rebuild via the
paved-road `Release image` GitHub Actions workflow, then a **migrate-first** Coolify swap
(`alembic upgrade head` in the new container) and a running-digest verification — the Increment-1
closeout is the worked example. Adapter-only changes need no redeploy (the adapter runs from local
operator code). MERGED ≠ DEPLOYED: verify the new route against `sds.alobar.net`'s live
`openapi.json` after the swap, not against `main`.

## Definition of done

Inbound authority shape recorded (ADR-0004); a human's tracker completion becomes an append-only
`tracker_state_divergence` condition (never a blind transition — a test asserts the tracker stays
non-canonical); new tracker reconciliation vocabulary via migration + CHECK; the existing operator
surface resolves tracker conditions; the deferred Increment-1 minors closed; TDD + per-task
reviews + a final Opus whole-branch review; `make check` green on a clean tree (Postgres +
`SECURITY_STANDARDS_DIR`; read the collected count); `/code-review`; Devon merges;
orchestrator-side additions deployed migrate-first and verified in prod; a Wave-2 closeout note.
After WS-P2.7 Increment 2, only WS-P2.8 remains in Wave 2.
