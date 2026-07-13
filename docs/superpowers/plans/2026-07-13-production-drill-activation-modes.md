# Production Drill Activation Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `off`, `standby`, and `enabled` activation states so PR #52 cannot disrupt ordinary factory operation before its schema and prerequisites are deliberately activated.

**Architecture:** One process-level `ProductionDrillMode` controls auth requirements, route availability, readiness, ordinary-path ownership queries, and synthetic isolation. `off` is compatible with schema 0014 and never references drill tables; `standby` and `enabled` require schema 0017 and retain ownership filters, while only `enabled` permits new drill mutations. Populated migrations refuse destructive downgrade, making the repaired standby-capable commit the permanent rollback floor.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest, Ruff, Pyright.

## Global Constraints

- Default `ORCHESTRATOR_PRODUCTION_DRILL_MODE` is exactly `off`; accepted values are exactly `off`, `standby`, and `enabled`.
- `off` must operate against `0014_wsp21_recovery_controls` without issuing SQL against migrations 0015-0017.
- `standby` and `enabled` require `0017_runtime_observations` and activate every synthetic ownership exclusion.
- `standby` permits GET run/state and HUMAN close only; it blocks runtime observation, start, scenario, and fail mutations with HTTP 503 `production_drill_unavailable`.
- `enabled` requires two distinct registered SYSTEM credential IDs; `off` and `standby` allow both IDs absent; every mode rejects a one-ID partial pair.
- Do not catch `UndefinedTable` or use database exceptions as schema detection.
- Populated downgrades refuse before mutation; empty downgrade to 0014 and re-upgrade to 0017 remain supported.
- No credentials, BWS, Coolify, production database, production deployment, or other infrastructure mutation.
- Do not stage or commit `docs/decisions/0003-production-drill-disposition.md`; it remains a proposed, unaccepted ADR.
- Every production behavior change follows an observed RED test, minimal GREEN implementation, and focused regression run before commit.

---

### Task 1: Define Activation State And Startup Compatibility

**Files:**
- Modify: `src/orchestrator/config.py`
- Create: `src/orchestrator/services/production_drill_compatibility.py`
- Modify: `src/orchestrator/main.py`
- Modify: `src/orchestrator/api/dependencies.py`
- Modify: `tests/test_config.py`
- Modify: `tests/architecture/test_container.py`
- Modify: `tests/conftest.py`
- Modify: `tests/api/conftest.py`

**Interfaces:**
- Produces: `ProductionDrillMode(StrEnum)` in config; `production_drill_schema_active(mode: ProductionDrillMode | None = None) -> bool`, `production_drill_enabled(mode: ProductionDrillMode | None = None) -> bool`, `PRE_DRILL_REVISION`, and `DRILL_REVISION` in the focused compatibility module; `create_app(auth_config: AuthConfig | None = None, production_drill_mode: ProductionDrillMode | None = None) -> FastAPI`; and `load_auth_config(production_drill_mode: ProductionDrillMode | None = None) -> AuthConfig | None`.
- Consumers: Tasks 2-5 use the enum, app state, and schema-active predicate.

- [ ] **Step 1: Add failing settings tests**

  Extend `tests/test_config.py` with separate tests proving the default is `ProductionDrillMode.OFF`, the three exact environment values parse, and another value raises Pydantic validation error. Construct `Settings` directly with `database_url=DB_URL`; do not use the cached accessor.

- [ ] **Step 2: Add failing startup compatibility tests**

  Split `test_runtime_auth_loads_embedded_registry_and_fails_closed` into focused cases in `tests/architecture/test_container.py`:

  - the pre-PR authenticated environment with neither new ID loads in `off`;
  - `standby` loads with neither ID;
  - all modes reject exactly one ID;
  - `enabled` rejects neither ID;
  - both distinct SYSTEM IDs load in every mode;
  - identical IDs and non-SYSTEM mappings fail without logging secret material;
  - `enabled` with no registry bundle fails startup while `off` and `standby` preserve no-auth development mode.

  Pass the mode explicitly to `load_auth_config()` so tests do not depend on ambient settings.

- [ ] **Step 3: Run RED tests**

  Run:

  ```bash
  uv run pytest tests/test_config.py tests/architecture/test_container.py -q
  ```

  Expected: new tests fail because the enum, setting, mode parameter, and optional credential behavior do not exist.

- [ ] **Step 4: Implement the mode and focused compatibility predicates**

  In `src/orchestrator/config.py`, add:

  ```python
  from enum import StrEnum

  class ProductionDrillMode(StrEnum):
      OFF = "off"
      STANDBY = "standby"
      ENABLED = "enabled"

  ```

  Add `production_drill_mode: ProductionDrillMode = ProductionDrillMode.OFF` to `Settings`.

  In new `src/orchestrator/services/production_drill_compatibility.py`, define the exact revision
  constants and named mode predicates. This module is the only service-level place that reads the
  cached mode; callers must not duplicate raw environment parsing or enum comparisons.

- [ ] **Step 5: Implement startup validation**

  Update `load_auth_config()` so mode is explicit, both IDs are read with an optional helper, one-ID pairs always fail, present pairs retain distinct SYSTEM validation, and `enabled` requires both. If there is no registry bundle, raise the existing generic runtime-auth error only for `enabled`.

  Update `create_app()` to store `production_drill_mode` in `application.state`. At module initialization, parse settings once and pass the same enum to `load_auth_config()` and `create_app()`.

- [ ] **Step 6: Preserve existing API fixture behavior explicitly**

  Add an autouse fixture in `tests/conftest.py` that sets the test process to `enabled` and clears
  `get_settings()` before and after each test. This preserves the intent of the existing PR #52
  service tests without changing the production default. New mode-specific tests override the
  environment and clear the same cache explicitly.

  Change `tests/api/conftest.py` fixtures that exercise PR #52 routes to call:

  ```python
  create_app(auth_config, ProductionDrillMode.ENABLED)
  ```

  Do not change production defaults merely to keep old tests green.

- [ ] **Step 7: Run GREEN and regression tests**

  Run:

  ```bash
  uv run pytest tests/test_config.py tests/architecture/test_container.py tests/api/test_production_drills_api.py -q
  uv run ruff check src/orchestrator/config.py src/orchestrator/services/production_drill_compatibility.py src/orchestrator/main.py src/orchestrator/api/dependencies.py tests/test_config.py tests/architecture/test_container.py tests/conftest.py tests/api/conftest.py
  uv run pyright src/orchestrator/config.py src/orchestrator/services/production_drill_compatibility.py src/orchestrator/main.py src/orchestrator/api/dependencies.py
  ```

  Expected: all selected checks pass.

- [ ] **Step 8: Commit Task 1**

  ```bash
  git add src/orchestrator/config.py src/orchestrator/services/production_drill_compatibility.py src/orchestrator/main.py src/orchestrator/api/dependencies.py tests/test_config.py tests/architecture/test_container.py tests/conftest.py tests/api/conftest.py
  git commit -m "feat: add production drill activation modes"
  ```

---

### Task 2: Enforce Route Availability And Mode-Aware Readiness

**Files:**
- Modify: `src/orchestrator/api/dependencies.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/api/health.py`
- Modify: `src/orchestrator/main.py`
- Create: `tests/api/test_production_drill_modes_api.py`
- Create: `tests/api/test_health_modes.py`

**Interfaces:**
- Consumes: `ProductionDrillMode` and `app.state.production_drill_mode` from Task 1.
- Produces: `get_production_drill_read_actor`, `get_production_drill_start_actor`, `get_production_drill_close_actor`, and mode-gated existing SYSTEM dependencies; `expected_database_head(mode) -> str` for readiness.

- [ ] **Step 1: Write the failing seven-operation route matrix**

  In `tests/api/test_production_drill_modes_api.py`, create explicit-mode TestClient fixtures and parameterize these expectations:

  | Operation | off | standby | enabled |
  |---|---:|---:|---:|
  | POST runtime observation | 503 | 503 | not 503 |
  | POST start | 503 | 503 | not 503 |
  | GET run | 503 | not 503 | not 503 |
  | GET state | 503 | not 503 | not 503 |
  | POST scenario | 503 | 503 | not 503 |
  | POST fail | 503 | 503 | not 503 |
  | POST HUMAN close | 503 | not 503 | not 503 |

  For unavailable operations, override `get_session` with a function that fails if entered and use missing/invalid drill credentials to prove the mode gate returns 503 first. Assert the stable JSON error code `production_drill_unavailable`.

- [ ] **Step 2: Write failing readiness tests**

  In `tests/api/test_health_modes.py`, fake `MigrationContext` and `ScriptDirectory` results to prove:

  - `(off, 0014_wsp21_recovery_controls)` is ready;
  - `(standby, 0017_runtime_observations)` and `(enabled, 0017_runtime_observations)` are ready;
  - off at 0017, standby/enabled at 0014, multiple heads, and unknown heads return 503 `migration_drift`.

- [ ] **Step 3: Run RED tests**

  ```bash
  uv run pytest tests/api/test_production_drill_modes_api.py tests/api/test_health_modes.py -q
  ```

  Expected: failures show routes are not mode-gated and readiness accepts only the script head.

- [ ] **Step 4: Implement composite actor dependencies**

  Add one private mode reader from `request.app.state`, one unavailable-error helper, and composite dependencies that check mode before calling `get_actor()`:

  - read and close require `standby` or `enabled`;
  - start, observer, scenario, and fail require `enabled`.

  Keep exact-key SYSTEM checks inside the existing observer/drill dependencies after the enabled check. Use composite dependencies on all seven route operations so dependency order is explicit rather than relying on FastAPI parameter ordering.

- [ ] **Step 5: Map unavailable to HTTP 503**

  Extend the DomainError handler in `src/orchestrator/main.py` so `production_drill_unavailable` maps to 503. Do not change other conflict mappings.

- [ ] **Step 6: Implement mode-aware readiness**

  Read the mode from application state in `ready()`. Compare the single database head to the exact expected revision for that mode rather than always equating it with the packaged script head. Still require the packaged script head to be the repository head and fail configuration errors closed.

- [ ] **Step 7: Run GREEN and route regressions**

  ```bash
  uv run pytest tests/api/test_production_drill_modes_api.py tests/api/test_health_modes.py tests/api/test_production_drills_api.py tests/api/test_production_drill_controls_api.py tests/api/test_production_drill_closeout_api.py tests/api/test_production_drill_scenarios_api.py -q
  uv run ruff check src/orchestrator/api/dependencies.py src/orchestrator/api/routes.py src/orchestrator/api/health.py src/orchestrator/main.py tests/api/test_production_drill_modes_api.py tests/api/test_health_modes.py
  uv run pyright src/orchestrator/api/dependencies.py src/orchestrator/api/routes.py src/orchestrator/api/health.py
  ```

- [ ] **Step 8: Commit Task 2**

  ```bash
  git add src/orchestrator/api/dependencies.py src/orchestrator/api/routes.py src/orchestrator/api/health.py src/orchestrator/main.py tests/api/test_production_drill_modes_api.py tests/api/test_health_modes.py
  git commit -m "feat: gate production drill routes and readiness"
  ```

---

### Task 3: Keep Ordinary Work Independent Of Drill Schema In Off Mode

**Files:**
- Modify: `src/orchestrator/services/production_drill_resources.py`
- Modify: `src/orchestrator/services/production_drills.py`
- Modify: `src/orchestrator/services/lifecycle.py`
- Modify: `src/orchestrator/services/dead_letter.py`
- Modify: `src/orchestrator/services/in_flight.py`
- Modify: `src/orchestrator/web.py`
- Create: `tests/services/test_production_drill_mode_compatibility.py`

**Interfaces:**
- Consumes: `production_drill_schema_active()` from Task 1.
- Produces: schema-free `is_not_production_drill_resource()` and `reject_production_drill_resource()` behavior in off; ordinary lease fallback in off.

- [ ] **Step 1: Write failing SQL-shape unit tests**

  In `tests/services/test_production_drill_mode_compatibility.py`, set cached settings to off and prove:

  - `is_not_production_drill_resource(...)` compiles to a SQL true predicate with no `production_drill_resources` table name;
  - `reject_production_drill_resource()` performs no `Session.scalar` call;
  - `lease_duration_for_work_unit()` returns `LEASE_DURATION` without a session query.

  Repeat in standby and prove the ownership subquery/query remains present.

- [ ] **Step 2: Write failing schema-0014 integration tests**

  Use one migrated PostgreSQL fixture downgraded to `0014_wsp21_recovery_controls`, mode off, and ordinary rows created through the 0014-compatible writers. Exercise claim, renew, reclaim, ordinary lifecycle transition, dead-letter, in-flight, and HUMAN queue. Assert no `UndefinedTable` and the expected ordinary result for each operation. Restore head in `finally`.

- [ ] **Step 3: Run RED tests sequentially**

  ```bash
  uv run pytest tests/services/test_production_drill_mode_compatibility.py -q
  ```

  Expected: failures identify unconditional ownership and lease queries.

- [ ] **Step 4: Centralize off-mode compatibility**

  In `production_drill_resources.py`, return SQLAlchemy `true()` from `is_not_production_drill_resource()` when schema is inactive and return immediately from `reject_production_drill_resource()`. In `production_drills.py`, return ordinary `LEASE_DURATION` before selecting ownership. Keep standby/enabled SQL unchanged.

  Audit direct ordinary `ProductionDrillResource` references. Any ordinary path not using these central helpers must branch on `production_drill_schema_active()` before constructing its statement. Drill-run-scoped internal writers remain schema-dependent and are protected by Task 2 route gates.

- [ ] **Step 5: Run GREEN and affected service suites**

  ```bash
  uv run pytest tests/services/test_production_drill_mode_compatibility.py tests/services/test_claims.py tests/services/test_lifecycle_events.py tests/services/test_lifecycle_guards.py tests/services/test_dead_letter.py tests/services/test_in_flight.py tests/web/test_queue.py -q
  uv run ruff check src/orchestrator/services/production_drill_resources.py src/orchestrator/services/production_drills.py src/orchestrator/services/lifecycle.py src/orchestrator/services/dead_letter.py src/orchestrator/services/in_flight.py src/orchestrator/web.py tests/services/test_production_drill_mode_compatibility.py
  uv run pyright src/orchestrator/services/production_drill_resources.py src/orchestrator/services/production_drills.py
  ```

- [ ] **Step 6: Commit Task 3**

  ```bash
  git add src/orchestrator/services/production_drill_resources.py src/orchestrator/services/production_drills.py src/orchestrator/services/lifecycle.py src/orchestrator/services/dead_letter.py src/orchestrator/services/in_flight.py src/orchestrator/web.py tests/services/test_production_drill_mode_compatibility.py
  git commit -m "fix: preserve ordinary work in production drill off mode"
  ```

---

### Task 4: Complete Standby Projection Isolation

**Files:**
- Modify: `src/orchestrator/services/status_ledger.py`
- Modify: `src/orchestrator/services/evidence.py`
- Modify: `src/orchestrator/web.py`
- Modify: `tests/services/test_status_ledger.py`
- Modify: `tests/api/test_status_ledger_api.py`
- Create: `tests/api/test_evidence_visibility_api.py`
- Modify: `tests/web/test_human_actions.py`
- Modify: `tests/web/test_evidence_pack.py`
- Modify: `tests/services/test_production_drill_resources.py`

**Interfaces:**
- Consumes: mode-aware `is_not_production_drill_resource()` from Task 3.
- Produces: exclusion of drill work from status ledger, direct HUMAN detail, and evidence pack in standby/enabled while off remains schema-free.

- [ ] **Step 1: Write failing isolation tests**

  Create drill-owned work using existing test helpers. In standby and enabled, assert:

  - default and direct-ID status-ledger queries return no row;
  - `/review/units/{id}`, `/review/units/{id}/evidence-pack`, and the ordinary API evidence-list surface return the existing work-unit not-found response;
  - the ordinary queue, dead-letter, and in-flight exclusions remain intact.

  Add off-mode SQL compilation coverage proving status ledger and `_projection()` do not reference the drill table.

- [ ] **Step 2: Run RED tests**

  ```bash
  uv run pytest tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/api/test_evidence_visibility_api.py tests/web/test_human_actions.py tests/web/test_evidence_pack.py tests/services/test_production_drill_resources.py -q
  ```

  Expected: status and direct projections expose the synthetic unit before implementation.

- [ ] **Step 3: Implement projection filters**

  Add the mode-aware ownership predicate to `status_ledger()` before caller filters. In
  `web._projection()` and the ordinary evidence-list service, reject a drill-owned work unit before
  loading evidence. Reuse the existing `work_unit_not_found` error so synthetic existence is not
  disclosed through ordinary API or HUMAN surfaces.

- [ ] **Step 4: Run GREEN and web regressions**

  ```bash
  uv run pytest tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/api/test_evidence_visibility_api.py tests/web/test_human_actions.py tests/web/test_evidence_pack.py tests/web/test_queue.py tests/services/test_production_drill_resources.py -q
  uv run ruff check src/orchestrator/services/status_ledger.py src/orchestrator/services/evidence.py src/orchestrator/web.py tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/api/test_evidence_visibility_api.py tests/web/test_human_actions.py tests/web/test_evidence_pack.py tests/services/test_production_drill_resources.py
  uv run pyright src/orchestrator/services/status_ledger.py src/orchestrator/services/evidence.py src/orchestrator/web.py
  ```

- [ ] **Step 5: Commit Task 4**

  ```bash
  git add src/orchestrator/services/status_ledger.py src/orchestrator/services/evidence.py src/orchestrator/web.py tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/api/test_evidence_visibility_api.py tests/web/test_human_actions.py tests/web/test_evidence_pack.py tests/services/test_production_drill_resources.py
  git commit -m "fix: isolate drill work from operator projections"
  ```

---

### Task 5: Exclude Synthetic Facts From Event Publication

**Files:**
- Modify: `src/orchestrator/services/production_drill_resources.py`
- Modify: `src/orchestrator/services/event_publications.py`
- Modify: `tests/services/test_event_publications.py`
- Modify: `tests/services/test_production_drill_resources.py`

**Interfaces:**
- Produces: `is_production_drill_event(session: Session, event: Event) -> bool` and stable skip reason `production_drill_resource`.
- Consumes: resource ownership rows and mode predicate from Tasks 1 and 3.

- [ ] **Step 1: Write failing publish/skip tests**

  Use the real deploy-split-brain drill writer to create drill-owned release artifact, deployment observation, post-deploy work unit, and evidence events. Also queue a drill-owned evidence row directly with `source_kind="evidence"`. In standby/enabled, assert:

  - queue creates `skipped` publications with `skip_reason == "production_drill_resource"` and `factory_event is None`;
  - retry remains skipped;
  - export JSONL omits all drill facts;
  - equivalent ordinary release/deployment facts still map and export;
  - off mode does not query the ownership table while mapping an ordinary event.

- [ ] **Step 2: Run RED tests**

  ```bash
  uv run pytest tests/services/test_event_publications.py tests/services/test_production_drill_resources.py -q
  ```

  Expected: drill release/deployment events currently become publishable factory events.

- [ ] **Step 3: Implement typed event ownership detection**

  Add an explicit subject-type map:

  ```python
  EVENT_RESOURCE_TYPES = {
      "work_unit": "work_unit",
      "evidence": "evidence",
      "observation": "observation",
      "reconciliation_condition": "reconciliation_condition",
      "release_artifact_binding": "release_artifact",
      "deployment_observation": "deployment_observation",
  }
  ```

  `is_production_drill_event()` returns false without querying in off. In standby/enabled it checks the mapped concrete subject ID against the ownership registry. Add an equivalent typed source helper for directly queued evidence rows. Unknown subject types are ordinary unless another explicit lineage rule proves ownership; `runtime.observed` remains unmapped and is not reclassified as drill-owned merely because a later run references it.

- [ ] **Step 4: Integrate the stable mapping result**

  Before normal `_factory_action()` mapping, return the existing skipped `MappingResult` shape with reason `production_drill_resource` for owned events. Ensure `_queue_one()`, retry, and export use that single mapping path; do not add separate export-time URL or prefix filtering.

- [ ] **Step 5: Run GREEN and full publication regressions**

  ```bash
  uv run pytest tests/services/test_event_publications.py tests/api/test_event_publications_api.py tests/services/test_production_drill_resources.py -q
  uv run ruff check src/orchestrator/services/production_drill_resources.py src/orchestrator/services/event_publications.py tests/services/test_event_publications.py tests/services/test_production_drill_resources.py
  uv run pyright src/orchestrator/services/production_drill_resources.py src/orchestrator/services/event_publications.py
  ```

- [ ] **Step 6: Commit Task 5**

  ```bash
  git add src/orchestrator/services/production_drill_resources.py src/orchestrator/services/event_publications.py tests/services/test_event_publications.py tests/services/test_production_drill_resources.py
  git commit -m "fix: exclude drill facts from event publication"
  ```

---

### Task 6: Refuse Destructive Populated Downgrades

**Files:**
- Modify: `migrations/versions/0015_production_drill_runs.py`
- Modify: `migrations/versions/0016_production_drill_resources.py`
- Modify: `migrations/versions/0017_runtime_observations.py`
- Modify: `tests/persistence/test_migrations.py`

**Interfaces:**
- Produces: data-preserving downgrade guards at all three PR #52 revision boundaries.
- Preserves: empty downgrade/re-upgrade behavior required by the mechanical gate.

- [ ] **Step 1: Replace the populated-downgrade expectation with failing refusal tests**

  Replace `test_runtime_observations_downgrade_restores_prior_provenance_trigger` because the approved policy no longer permits dropping populated audit storage. Add three independent tests:

  - a runtime observation or linked run prevents 0017 downgrade;
  - a resource binding prevents 0016 downgrade;
  - a production-drill run prevents 0015 downgrade.

  For each test, assert the Alembic command raises before mutation, current revision remains unchanged, and the table, row, foreign-key link, and relevant immutability trigger remain present.

- [ ] **Step 2: Keep an explicit empty-chain control**

  Add or retain a test that resets to an empty head schema, downgrades to `0014_wsp21_recovery_controls`, verifies current revision, upgrades to head, and verifies `0017_runtime_observations`.

- [ ] **Step 3: Run RED migration tests sequentially**

  ```bash
  uv run pytest tests/persistence/test_migrations.py -q
  ```

  Expected: populated downgrades succeed or mutate before the guards exist, causing the new tests to fail.

- [ ] **Step 4: Add pre-mutation Alembic guards**

  At the first line of each downgrade, query the relevant table with `op.get_bind()` and raise a stable `RuntimeError` naming the retained data when a row exists. The guard must run before replacing triggers, dropping columns, or dropping tables. Do not delete or terminalize data inside a migration.

- [ ] **Step 5: Run GREEN and migration regressions**

  ```bash
  uv run pytest tests/persistence/test_migrations.py -q
  uv run ruff check migrations/versions/0015_production_drill_runs.py migrations/versions/0016_production_drill_resources.py migrations/versions/0017_runtime_observations.py tests/persistence/test_migrations.py
  uv run pyright migrations/versions/0015_production_drill_runs.py migrations/versions/0016_production_drill_resources.py migrations/versions/0017_runtime_observations.py
  ```

- [ ] **Step 6: Commit Task 6**

  ```bash
  git add migrations/versions/0015_production_drill_runs.py migrations/versions/0016_production_drill_resources.py migrations/versions/0017_runtime_observations.py tests/persistence/test_migrations.py
  git commit -m "fix: preserve retained drill data on downgrade"
  ```

---

### Task 7: Document The Compatibility Floor And Run Full Verification

**Files:**
- Modify: `docs/operations/recovery-drills.md`
- Modify: `docs/operations/runtime-observations.md`
- Create: `docs/superpowers/evidence/2026-07-13-production-drill-activation-modes.md`

**Interfaces:**
- Consumes: all implemented and reviewed Task 1-6 behavior.
- Produces: operator-facing state/rollout/rollback contract and retained verification evidence; no deployment authorization.

- [ ] **Step 1: Update runbooks**

  Document the exact state matrix, pre-drill and drill schema heads, route availability, credential requirements, `enabled -> standby` drain behavior, and the rule that application rollback may not pass the compatibility-floor commit after synthetic data exists. State explicitly that switching modes or provisioning credentials is not authorized by the document.

- [ ] **Step 2: Start one disposable PostgreSQL 16 fixture**

  Use one uniquely named container, a guaranteed EXIT cleanup trap, and these environment variables for every DB-backed command:

  ```bash
  export ORCHESTRATOR_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55432/orchestrator_test'
  export TEST_DATABASE_URL="$ORCHESTRATOR_DATABASE_URL"
  export SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards"
  ```

  Do not run a second DB-backed pytest process concurrently.

- [ ] **Step 3: Run the full deterministic gate**

  ```bash
  PATH="$PWD/.venv/bin:$PATH" make check 2>&1 | tee /tmp/production-drill-modes-make-check.log
  ! rg -n 'no tests ran|collected 0 items' /tmp/production-drill-modes-make-check.log
  ```

  Record the exact Ruff, formatting, Pyright, passed, skipped, and duration output. Capture the scanner component status rather than `tee` status.

- [ ] **Step 4: Run exact migration and planted compatibility controls**

  Verify empty upgrade/current/head, empty downgrade to 0014 and re-upgrade, populated refusal at each boundary, off-mode ordinary operations on schema 0014, and standby synthetic isolation. For every control, record the exact command and exit status.

- [ ] **Step 5: Run portfolio and security checks**

  ```bash
  /Users/devon/Developer/code-standards/.venv/bin/code-standards check --repo .
  git diff 57e9d87463015e8b130680608f7123ca14509f69..HEAD -- '*.py' '*.sh' | \
    rg '^\+.*(# noqa|# type: ignore|eslint-disable)' || true
  PYTHONPATH="$HOME/Projects/security-standards/src" \
    /Users/devon/Projects/security-standards/.venv/bin/python \
    -m security_scan.cli . --category security
  ```

  Require zero BLOCK findings and manually review every added suppression reason.

- [ ] **Step 6: Run `/code-review` equivalent against portfolio standards**

  Review the complete implementation diff against `/Users/devon/Developer/code-standards/STANDARDS.md`. Flag and fix wrong abstractions, duplicated mode checks, exception-based schema detection, weak tests, comments that restate code, and new suppression comments. Run an independent whole-diff reviewer after local fixes.

- [ ] **Step 7: Write verification evidence**

  Record base/head commits, test counts, migration results, mode/route matrix, schema-0014 ordinary controls, isolation controls, standards/security results, unresolved non-goals, and the compatibility-floor rollback rule. Do not claim deployed or production-proven state.

- [ ] **Step 8: Verify and commit docs/evidence**

  ```bash
  git diff --check
  git add docs/operations/recovery-drills.md docs/operations/runtime-observations.md docs/superpowers/evidence/2026-07-13-production-drill-activation-modes.md
  git commit -m "docs: record production drill activation contract"
  ```

  Expected: only the planned documentation/evidence paths are staged; the proposed Revert ADR remains untracked.

## Final Review And Handoff

After Task 7:

1. Generate one review package from base `57e9d87463015e8b130680608f7123ca14509f69` to HEAD.
2. Dispatch an independent whole-branch code reviewer with the approved spec, this plan, implementation reports, verification evidence, and portfolio standards.
3. Fix every Critical or Important finding in one consolidated pass and re-run covering tests.
4. Re-run the complete verification gate after the final fix commit.
5. Use `superpowers:finishing-a-development-branch` to present integration options.
6. Do not deploy, create credentials, accept the Revert ADR, or begin the remaining R1-R10 contract work in this implementation session.
