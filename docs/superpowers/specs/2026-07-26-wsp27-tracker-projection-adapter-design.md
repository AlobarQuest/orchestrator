# WS-P2.7 — Tracker Projection Adapter (Increment 1: outbound-only) — Design

**Date:** 2026-07-26
**Status:** Approved for planning (design approved by Devon 2026-07-26)
**Workstream:** WS-P2.7 — Program Phase 2, Wave 2 (LEGIBLE)
**Program:** `~/docs/software-delivery-system/2026-07-02-software-factory-master-plan.md` (D7/D8), exit criterion #9 ("no tracker is treated as canonical")
**Handoff:** `~/docs/software-delivery-system/2026-07-26-wsp27-handoff-prompt.md`
**Precedent:** ADR-0002 (`docs/decisions/0002-reconciliation-via-report-only-runner.md`)

---

## 1. Context & the resolved blocker

WS-P2.7 was specified to decide Linear vs Todoist on the WS-0.6 "Open Engine"
pilot's protocol-learnings note. **That note was never produced** — verified on
disk 2026-07-26: `~/docs/software-delivery-system/` contains only the pilot
*setup kit* (`2026-07-02-ws06-open-engine-pilot-kit.md`, an input), no
`*-closeout-evidence.md` for WS-0.6, and `~/.config/open-engine/` holds only the
two static setup files (no ledger export, no run log). The pilot never ran enough
throughput to generate evidence.

**Decisions taken with Devon (2026-07-26), the evidence being absent:**

1. **Todoist first**, behind a tracker-agnostic interface. Todoist is usable today
   (deeply integrated in Devon's world; Linear was never wired — browser-auth MCP).
   Linear remains a possible second concrete adapter behind the same seam.
2. **Outbound-only (Increment 1)** this session. Inbound (tracker edits → validated
   requested transitions + reconciliation) is a deliberate fast-follow (Increment 2).
3. **Standalone entry point + REST token** — the ADR-0002 shape: a real
   console-script process shipped from this repo, operator-invoked first, scheduler
   deferred. One new BWS secret (a Todoist REST API token).
4. **Documentary close of the D8 interim** — retire the Linear "Agent Queue" pilot as
   the interim non-canonical surface and record the orchestrator-canonical +
   Todoist-projection model. Do **not** re-home the real pilot items ALO-50/ALO-51;
   creating real non-software work units is WS-P2.13's job (it needs an approved
   non-software intent package).

The rationale for the Todoist-first decision — made on first principles because the
intended pilot evidence did not exist — is recorded in **ADR-0003** (this workstream).

## 2. What this workstream is

Two data flows are named in the program plan; **Increment 1 builds only the first.**

- **Outbound (this increment):** canonical work-unit state is projected onto tracker
  items — the orchestrator creates/updates one Todoist task per work unit to mirror
  its lifecycle state. A read-only view for humans.
- **Inbound (deferred to Increment 2):** a human editing a tracker item becomes a
  *requested transition* the orchestrator validates against its own lifecycle guards,
  never applied blindly; unappliable divergences flow back as append-only
  reconciliation conditions for an operator.

The orchestrator is **always canonical**. The tracker is **always projection**
(program exit #9 + YAGNI ledger: no issue-move-as-lock, no comments-as-audit, no
issue-content-as-authority — ever).

## 3. Architecture & boundaries

The shape mirrors ADR-0002's reconciliation runner exactly (verified:
`src/reconciliation_runner/` is a top-level package under `src/`, sibling to
`orchestrator/`, console script `reconciliation-runner`, httpx client with a
report-only endpoint allowlist, isolation test enforcing no cross-imports and deps
limited to httpx/pydantic/typer; no Dockerfile/Coolify — operator-invoked).

Two pieces:

1. **Orchestrator-side** (`src/orchestrator/`, **tracker-agnostic**): a canonical
   `unit_tracker_bindings` table + a SYSTEM-only upsert/list API. The orchestrator
   learns only *that* a unit is mapped to some external item id in some tracker
   system; it imports nothing tracker-specific. (The scope guard
   `test_application_has_no_external_mutation_integrations` already bans the `todoist`
   import prefix inside `src/orchestrator/`.)
2. **Adapter** (`src/tracker_projection_adapter/`, a **new sibling package outside
   `src/orchestrator/`**): a console-script process that reads canonical state via the
   public API, upserts one Todoist task per unit via the Todoist REST API, and writes
   the binding back. All tracker I/O lives here, invisible to every `src/orchestrator/`
   guard (ws32/ws33/no-external-import only scan `src/orchestrator/`).

**Data flow — one direction only:**
orchestrator (read `status-ledger`) → adapter → Todoist (create/update task) →
adapter → orchestrator (write `tracker-binding`). The adapter never calls any
lifecycle/transition route; its client structurally forbids it (§7).

## 4. Orchestrator-side additions (`src/orchestrator/`)

### 4.1 Table `unit_tracker_bindings`

Mirrors `UnitPrBinding` (`persistence/models.py:1167`): a `Base` (mutable,
**not** append-only, so **no** `reject_append_only_mutation` trigger), primary key on
`work_unit_id` → exactly one row per unit.

| Column | Type | Null | Notes |
|---|---|---|---|
| `work_unit_id` | `UUID` FK→`work_units.id` | no | **PRIMARY KEY** (one row/unit) |
| `tracker_system` | `String` | no | CHECK `IN TRACKER_SYSTEMS` |
| `external_item_id` | `String` | no | CHECK `<> ''` (the Todoist task id) |
| `external_url` | `String` | yes | link to the tracker item |
| `projected_state` | `String` | no | the work-unit lifecycle state last mirrored |
| `updated_at` | `DateTime(tz)` server_default `now()` | — | |

`projected_state` lets the adapter skip unchanged units and lets a human see what was
last mirrored. (No `created_at`, matching `UnitPrBinding`.)

### 4.2 Closed vocabulary

In `persistence/models.py`, alongside the reconciliation tuples (models.py:1063):

```python
TRACKER_SYSTEMS = ("todoist",)
```

Becomes a DB CHECK via the established `!r` f-string interpolation in
`__table_args__`: `CheckConstraint(f"tracker_system IN {TRACKER_SYSTEMS!r}",
name="ck_unit_tracker_bindings_tracker_system")`. Adding `"linear"` later is a
one-line migration.

### 4.3 Migration

`migrations/versions/0018_wsp27_tracker_bindings.py`, `revises="0017_wsp23_waiver_risk_class"`
(current head). Inlines a frozen copy of `TRACKER_SYSTEMS` (migrations do not import
model constants — established convention, see `0014_wsp21_recovery_controls.py`).
`op.create_table("unit_tracker_bindings", ...)` with the two CHECKs. Mutable table →
no append-only trigger.

### 4.4 Service (`services/tracker_bindings.py`)

Modeled on `services/pr_bindings.py`:

- `upsert_tracker_binding(session, *, actor, work_unit_id, tracker_system,
  external_item_id, external_url, projected_state) -> UnitTrackerBinding` —
  **commits** (a request entry point owns its transaction; this is the FLUSH-but-
  never-COMMIT lesson applied correctly).
- `get_tracker_binding(session, work_unit_id)` and
  `list_tracker_bindings(session, *, tracker_system=None)` readers.
- Auth `_authorize_write(actor)`: **SYSTEM only** for Increment 1 (the adapter is a
  SYSTEM actor; there is no worker write path, unlike PR-binding). Any other role →
  `role_forbidden` (`DomainError`).
- The service performs **no state transition** and touches only the binding table —
  writing a binding must leave `work_units.state` unchanged (asserted by test, §7).

### 4.5 Routes (`api/routes.py` + `api/schemas.py`)

- `POST /api/v1/work-units/{unit_id}/tracker-binding` — upsert. Body
  `TrackerBindingCommand(tracker_system, external_item_id, external_url,
  projected_state)` inheriting `CommandBase` (so `idempotency_key` +
  `expected_version`, the latter must be 0 via `_require_zero_expected_version`, as
  `pr-binding` does). Response `TrackerBindingResponse`. `ActorDep` (role gate in the
  service).
- `GET /api/v1/tracker-bindings` — list (optional `?tracker_system=` filter), so the
  **stateless** adapter learns the full unit→item map in one call. `ActorDep`
  (auth-only, like `status-ledger`). Response `list[TrackerBindingResponse]`.

Both return JSON → **no** `NON_JSON_SUCCESS_PATHS` entry needed. Both **must** be
added to the explicit route-inventory set literals in
`tests/architecture/test_scope_guards.py`
(`test_production_post_route_inventory_is_explicit` and
`test_production_get_route_inventory_is_explicit`) in the **same** change, or CI fails.

### 4.6 Event publication — none (Decision A, 2026-07-26)

**The binding write emits no event and publishes nothing** — exactly matching
`upsert_pr_binding`, which (verified) contains no event/publish code at all. The
binding row itself is the record; its audit surface is the row, its `updated_at`, and
`GET /api/v1/tracker-bindings`.

Rationale: a tracker binding is bookkeeping about a *read-only projection mirror*,
structurally identical to a PR binding, which deliberately emits nothing. It carries no
authority and does not belong in the tamper-evident `factory-event/v1` chain (which
would also re-engage the `factory_events` runtime dependency that 500'd prod in
WS-6.1). A projection adapter is designed to run repeatedly, so an event-per-upsert
would also spam unit history. If projection *timeline* visibility is ever wanted, a
local `tracker_binding.recorded` event is a clean additive change then (was the
rejected Option B); external publication (Option C) was rejected as over-weighted for
this record.

## 5. The adapter package (`src/tracker_projection_adapter/`)

Console script `tracker-projection-adapter` (new `[project.scripts]` entry in
`pyproject.toml`, beside `reconciliation-runner`). Third-party deps limited to
**`httpx`, `pydantic`, `typer`** — Todoist is called over **raw REST via httpx**, so
**no `todoist` package is added at all**.

- `orchestrator_client.py` — httpx client mirroring `reconciliation_runner/client.py`.
  Reads: `GET /api/v1/status-ledger`, `GET /api/v1/tracker-bindings`. Writes: a
  **single-endpoint allowlist** `{"/api/v1/work-units/{unit_id}/tracker-binding"}`
  enforced by a `ForbiddenEndpointError` (mirroring `ALLOWED_WRITE_ENDPOINTS`); any
  other write path or any non-GET/POST method raises. Two-header auth
  (`Authorization: Bearer <token>`, `X-Credential-Key-Id: <key-id>`), key-id default
  `orchestrator-system`.
- `tracker.py` — the **tracker-agnostic seam**: a `TrackerProjector` protocol
  (`upsert_item(unit) -> ItemRef`, `complete_item(item_ref)`), plus a concrete
  `TodoistProjector` that calls the Todoist REST API. Linear later = a second class,
  zero orchestrator change.
- `projection.py` — **pure** logic: `(units, existing_bindings) -> [Action]` (create
  vs update vs complete, skip-if-`projected_state`-unchanged). Fully unit-testable with
  a fake projector and no network.
- `cli.py` — `typer` app, command `project`. Reads bearer token from env
  `TRACKER_PROJECTION_TOKEN` and Todoist token from env `TODOIST_API_TOKEN` (no BWS
  code in the runner itself, matching the reconciliation/factory runners). Flags:
  `--orchestrator-url` (default `https://sds.alobar.net`), `--credential-key-id`
  (default `orchestrator-system`), `--todoist-project-id`, `--dry-run` (prints the
  action plan, makes no writes).
- Isolation test `tests/architecture/test_tracker_projection_adapter_isolation.py`
  mirroring `test_reconciliation_runner_isolation.py`: the adapter imports nothing from
  `orchestrator.*`, the orchestrator imports nothing from
  `tracker_projection_adapter`, adapter third-party deps ⊆ {httpx, pydantic, typer}.

## 6. Projection semantics

- **Read source:** `GET /api/v1/status-ledger` (auth-only; carries `unit_id`,
  `unit_key`, `unit_title`, `unit_state` per unit — everything needed to render a task
  in one call). This is the enumeration set for Increment 1.
- **Per unit:** if a binding exists → update that Todoist task; else → create the task,
  then upsert the binding. **The binding is the dedup key** — the unit↔task mapping
  lives canonical-side, never in Todoist content (keeping issue-content-as-authority off
  the table).
- **Task representation:** content `[{unit_key}] {unit_title}`; lifecycle state as a
  Todoist label (e.g. `sds:<state>`); description carries a link to the `/review` GUI
  for that unit. On terminal `completed`, complete the Todoist task. Non-intrusive,
  read-only-for-humans.
- **Idempotency:** re-running the adapter is safe — bindings dedup task creation, and
  `projected_state` lets unchanged units be skipped. The binding upsert carries an
  `idempotency_key`.
- Target Todoist project is supplied via `--todoist-project-id` (Devon creates a
  dedicated project, e.g. "SDS Orchestrator").

## 7. Exit-criterion-#9 guarantee ("the tracker can never be canonical")

Belt-and-suspenders, all mechanical:

1. **Import ban (existing):** `test_application_has_no_external_mutation_integrations`
   bans the `todoist` prefix in `src/orchestrator/`.
2. **Client write-allowlist (new):** the adapter's `orchestrator_client` permits
   exactly one write endpoint (the binding POST) and only GET/POST — it *cannot*
   structurally POST a transition/command.
3. **Binding-has-no-authority test (new):** `upsert_tracker_binding` leaves
   `work_units.state` unchanged (assert state before == after across an upsert); the
   binding table has no transition path.
4. **Adapter isolation test (new):** no cross-imports (§5).
5. **ADR-0003 (new):** records projection-only / out-of-process / orchestrator-
   canonical, and the Todoist-first-on-first-principles rationale (evidence absent).

## 8. Deploy shape & secrets

- Operator-invoked console script; **no Dockerfile, no Coolify service, scheduler
  deferred** (exactly ADR-0002; production scheduling is a later, separate decision).
- A small `scripts/run-tracker-projection.sh` launcher sources
  `~/Projects/vps-backup/bws-token.sh`, runs two `bws secret get` calls (SYSTEM bearer
  UUID `221a48d5-3f29-4898-b300-b4820140c880` + the Todoist token UUID
  `ff396349-aec1-4250-b2f0-b493015188da`), exports them into `TRACKER_PROJECTION_TOKEN`
  / `TODOIST_API_TOKEN`, and execs the console script — **never echoing any value**.
- **BWS manifest:** add the Todoist token (`ff396349-aec1-4250-b2f0-b493015188da`) to
  `.bws-secrets.toml` (repo root) as a tracked `[[secret]]` block so the drift scanner
  watches it (an untracked live credential is exactly what that manifest exists to
  catch).
- **Human prerequisite (Devon):** the Todoist API token already exists in BWS
  (`ff396349-aec1-4250-b2f0-b493015188da`, provided 2026-07-26). The only remaining
  human step is choosing/creating the target Todoist project a live run projects into
  (passed via `--todoist-project-id`); the adapter can otherwise be pointed at an
  existing project.
- **The build and the entire test suite mock the tracker** (fake projector, no
  network) — none of the above blocks development. Only a real run against live Todoist
  needs the token + project.

## 9. Documentary close of the D8 interim

- **ADR-0003** and a Wave-2 closeout note (`~/docs/software-delivery-system/`) formally
  retire the Linear "Agent Queue" as the interim non-canonical surface and establish
  the orchestrator-canonical + Todoist-projection model as the standing design.
- The Linear workspace is **not** touched programmatically (its MCP isn't connected);
  Devon may archive the project at his leisure. The *close* is the recorded decision.
- **No real pilot items (ALO-50/ALO-51) are re-homed** — creating real non-software
  work units is WS-P2.13 (it needs an approved non-software intent package).

## 10. CI / guard obligations (must all stay green)

- New POST + GET routes added to both route-inventory set literals in
  `test_scope_guards.py` (same change).
- Every `/api/v1` success response has an explicit JSON schema (satisfied by
  `response_model=`); every mutation body inherits `CommandBase`
  (`idempotency_key` + `expected_version`).
- New orchestrator-side code (route/service/schema/model docstrings) must avoid the
  bare banned tokens `dispatch`, `deploy` (ws32) and `merges` (ws33) in prose — use
  "projection"/"tracker binding"/"writes to git" as needed. The adapter package is
  outside all three guards and unconstrained.
- New adapter isolation test green; adapter deps ⊆ {httpx, pydantic, typer}.
- `make check` green **on a clean tree** — needs Postgres, `SECURITY_STANDARDS_DIR`,
  a migrated DB; read the collected-test count, not just exit 0; run `ruff format`
  (never on `.json`/`.toml`) before commit; run against a clean clone to control for
  environment before blaming any change.

## 11. Explicitly deferred to Increment 2 (inbound) — NOT built now

- Requested transitions originating from tracker edits.
- The actor-role decision (what role a tracker-originated transition carries).
- New reconciliation `observation_kind` / `condition_type` vocabulary for tracker
  divergences, and any operator reconciliation surface for them.
- Any scheduled/automatic invocation of the adapter.

## 12. Definition of done (Increment 1)

- ADR-0003 recorded (projection-only, out-of-process, Todoist-first rationale).
- `unit_tracker_bindings` table + migration `0018` + `TRACKER_SYSTEMS` vocabulary.
- SYSTEM-only `POST /api/v1/work-units/{unit_id}/tracker-binding` +
  `GET /api/v1/tracker-bindings`, both in the route-inventory literals.
- `src/tracker_projection_adapter/` console script that mirrors canonical state onto
  Todoist and writes bindings back, tracker-agnostic seam in place, single-endpoint
  write-allowlist, isolation test.
- Exit-#9 tests (binding-has-no-authority + adapter isolation + existing import ban).
- Launcher + `.bws-secrets.toml` manifest entry (token creation is Devon's one-time
  human step).
- Documentary D8 close (ADR-0003 + Wave-2 closeout note).
- TDD throughout, per-task reviews, a final adversarial whole-branch review on Opus
  (budget for kills — this is two-way-sync-adjacent code), `make check` green on a
  clean tree, `/code-review`. Devon merges. Deploy (adapter + orchestrator API
  additions; migrate-first) is a separate Devon-gated step.

After WS-P2.7 (Inc 1), the inbound Increment 2 and WS-P2.8 (follow-up scheduling)
remain in Wave 2.
