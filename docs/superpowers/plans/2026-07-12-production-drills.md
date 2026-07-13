# Production Drills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all five recovery drills create bounded, auditable production evidence against `sds.alobar.net` without private SQL or untracked cleanup.

**Architecture:** Add a production-drill run aggregate that owns explicitly namespaced synthetic work. Run-scoped public controls provide bounded timing and cleanup without changing global settings; the existing local shell drills remain unchanged as component tests. A separate production runner drives only the public drill contract and Coolify's approved restart surface.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, Typer/shell, pytest, httpx, Coolify.

## Global Constraints

- Every drill record is append-only and tied to an immutable approved recovery-drills package revision.
- Production runner reads and writes only public API surfaces; it executes no production SQL.
- A browser-authenticated HUMAN creates and closes a run; its creation event is the immutable authorization record bound to the revision, and the dedicated SYSTEM credential may operate only on that authorized open run.
- All resource controls reject non-drill and cross-run identifiers.
- The live restart occurs only in drill 1 and requires separate runtime approval.
- A run cannot pass until its synthetic records are closed with audited reasons.

---

### Task 1: Persist And Authorize Production Drill Runs [complete]

**Files:**
- Modify: `src/orchestrator/persistence/models.py`
- Create: `migrations/versions/0015_production_drill_runs.py`
- Modify: `src/orchestrator/api/schemas.py`
- Create: `src/orchestrator/services/production_drills.py`
- Modify: `src/orchestrator/api/routes.py`
- Test: `tests/services/test_production_drills.py`
- Test: `tests/api/test_production_drills_api.py`

**Interfaces:**
- Produces `ProductionDrillRun` with `id`, `revision_id`, `owner_actor_id`, `opened_at`, `closed_at`, `status`, `image_ref`, `image_digest`, `openapi_digest`, and `closure_reason`.
- Produces `POST /api/v1/production-drills` and `GET /api/v1/production-drills/{run_id}`.

- [ ] Write failing service tests proving a worker actor, SYSTEM-only actor, and an unapproved revision each raise a domain error; assert the successful HUMAN creation event is the authorization record.
- [ ] Add the model and migration. Use a foreign key to `work_package_revisions`, non-null owner and immutable provenance fields, and a check limiting status to `open`, `asserting`, `closed`, or `failed`.
- [ ] Add `StartProductionDrillCommand` and `CloseProductionDrillCommand` Pydantic models. A start request includes `revision_id`, idempotency key, image reference/digest, and OpenAPI digest; it cannot supply arbitrary owner or status values.
- [ ] Implement `start_production_drill()` to require a HUMAN actor and an approved package revision. Write the immutable authorization event before returning the persisted run.
- [ ] Implement `close_production_drill()` as a HUMAN-only terminal, idempotent transition that requires an explicit closure reason and rejects closure while synthetic records remain open.
- [ ] Add route/auth tests for successful HUMAN start, replay, rejection cases, and OpenAPI declaration.
- [ ] Run `uv run pytest tests/services/test_production_drills.py tests/api/test_production_drills_api.py -q`.

### Task 2: Bind Synthetic Resources To A Run [complete]

**Files:**
- Modify: `src/orchestrator/persistence/models.py`
- Create: `migrations/versions/0016_production_drill_resources.py`
- Modify: `src/orchestrator/services/packages.py`
- Modify: `src/orchestrator/services/evidence.py`
- Modify: `src/orchestrator/services/observations.py`
- Modify: `src/orchestrator/services/reconciliation.py`
- Test: `tests/services/test_production_drill_resources.py`

**Interfaces:**
- Produces `ProductionDrillResource(run_id, resource_type, resource_id, created_at, closed_at)`.
- Consumes a run ID only from production-drill service commands; ordinary lifecycle commands cannot self-tag as drill resources.

- [ ] Write failing tests that ordinary units cannot acquire a drill tag and that a resource cannot belong to two runs.
- [ ] Add the resource table and uniqueness constraints in the migration.
- [ ] Add service helpers to register units, evidence, observations, reconciliation conditions, release artifacts, and post-deploy units created by a run.
- [ ] Make all helpers validate that the run is open and that every resource belongs to the run before a control action can use it.
- [ ] Ensure ordinary queue/dead-letter/in-flight projections exclude drill-tagged records by default, with an explicit internal opt-in for run-scoped views.
- [ ] Run the focused service tests and the existing lifecycle, evidence, observation, reconciliation, and release-artifact suites.

### Task 3: Add Run-Scoped Public Assertions And Bounded Time Controls [complete]

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/services/production_drills.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/kernel/leases.py`
- Modify: `src/orchestrator/services/dead_letter.py`
- Modify: `src/orchestrator/services/reconciliation_detection.py`
- Test: `tests/services/test_production_drill_controls.py`
- Test: `tests/api/test_production_drill_controls_api.py`

**Interfaces:**
- Produces `GET /api/v1/production-drills/{run_id}/state` containing unit state/version, active claim expiry, evidence heads/supersession, observations, conditions, and closure status.
- Produces run-scoped lease and reporting deadlines with a minimum of 60 seconds and a fixed maximum defined in configuration.

- [ ] Write failing tests proving a non-drill unit, cross-run resource, worker actor, missing human authorization, zero/negative deadline, and deadline beyond the configured maximum are rejected.
- [ ] Add a run-scoped deadline object persisted at start; do not add a request parameter to ordinary claim, dead-letter, or reconciliation APIs.
- [ ] Make claims for registered drill units use the run lease duration while all other claims keep `LEASE_DURATION` unchanged.
- [ ] Make dead-letter and reconciliation detection read the run deadline only for registered drill resources; global settings continue to govern all ordinary records.
- [ ] Implement the run-state projection using ORM reads, returning the precise evidence-head and condition predicates currently asserted through local SQL.
- [ ] Add API tests that prove state projection cannot leak a different run and that global thresholds are unchanged after a run-control request.
- [ ] Run focused tests plus `tests/architecture/test_drill_scripts.py` to prove local drills retain their isolation contract.

### Task 4: Close Synthetic Work Without Deletion [complete]

**Files:**
- Modify: `src/orchestrator/services/production_drills.py`
- Modify: `src/orchestrator/services/lifecycle.py`
- Modify: `src/orchestrator/services/reconciliation.py`
- Modify: `src/orchestrator/api/routes.py`
- Test: `tests/services/test_production_drill_closeout.py`
- Test: `tests/api/test_production_drill_closeout_api.py`

**Interfaces:**
- Produces `POST /api/v1/production-drills/{run_id}/close`.
- Consumes only registered resources for an open run and emits `production_drill_closed` events.

- [ ] Write failing tests that close rejects an ordinary unit, incomplete assertions, and a second distinct close reason.
- [ ] Implement closeout to resolve only run-owned reconciliation conditions and complete/cancel only run-owned synthetic units through existing lifecycle transitions.
- [ ] Preserve all evidence, claims, observations, and events; write explicit closure events rather than deleting rows.
- [ ] Add a final invariant that a closed run has no active claim, unresolved run-owned condition, or nonterminal run-owned unit.
- [ ] Run the closeout tests and persistence append-only tests.

### Task 5: Add Fixed Run-Scoped Scenario Controls

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/services/production_drills.py`
- Modify: `src/orchestrator/api/routes.py`
- Test: `tests/services/test_production_drill_scenarios.py`
- Test: `tests/api/test_production_drill_scenarios_api.py`

**Interfaces:**
- Produces fixed SYSTEM-only scenario commands for `crash_recovery`, `evidence_recovery`, `external_pr_conflict`, `deploy_split_brain`, and `stalled_approval`.
- Produces a SYSTEM-only `POST /api/v1/production-drills/{run_id}/fail` operation that records an immutable failure event.

- [ ] Write failing tests for non-drill, cross-run, worker, and arbitrary-payload rejection for every command.
- [ ] Implement each command as a fixed orchestration over existing lifecycle, evidence, observation, reconciliation, release-artifact, and run-resource services. The HUMAN start event delegates SYSTEM authority only for these five fixed templates; command inputs cannot select arbitrary resource IDs, URLs, shell commands, repositories, authority, or deadlines.
- [ ] Add `fail_production_drill()` that accepts only an enumerated failure code and redacted diagnostic reference, records an event, and terminally marks the run failed without closing or deleting resources.
- [ ] Add authenticated API routes and exact OpenAPI tests; all scenario responses use the run-scoped state projection.
- [ ] Run the focused scenario/API suites and affected lifecycle, reconciliation, and append-only tests.

### Task 6: Create The Production Drill Runner

**Files:**
- Create: `scripts/run-production-drills.sh`
- Create: `scripts/production_drill_common.sh`
- Modify: `docs/operations/recovery-drills.md`
- Modify: `tests/architecture/test_drill_scripts.py`
- Test: `tests/architecture/test_production_drill_runner.py`

**Interfaces:**
- Consumes `ORCHESTRATOR_PRODUCTION_DRILL_TOKEN` and its credential-key ID only from BWS at runtime.
- Consumes a human-started run ID; it does not mint authority or impersonate a human.
- Produces a machine-readable evidence file with no secret material.

- [ ] Write architecture tests that the runner targets only `https://sds.alobar.net`, has no SQL/docker/process-kill command, requires a run ID, records a unique idempotency prefix, and refuses to run without a production OpenAPI preflight.
- [ ] Implement shared HTTP/auth helpers that source the dedicated drill credential at runtime and redact authorization headers from logs.
- [ ] Implement per-drill functions using only the five fixed run-scoped scenario endpoints and their returned assertions.
- [ ] Make drill 1 stop before the fixed Coolify restart integration unless `--approve-live-restart` is supplied; preflight `health/ready` before and after that restart, and never accept an executable path.
- [ ] Make runner failures call the audited SYSTEM `fail` endpoint with an enumerated failure code; it must never attempt HUMAN closeout.
- [ ] Update operations documentation with preflight, approval, expected availability interruption, cleanup, and evidence locations.
- [ ] Run architecture tests and a dry-run against a mock HTTP server.

### Task 6: Deploy And Prove The Production Drill Contract

**Files:**
- Modify: `docs/evidence/production-drills-evidence.md`

**Interfaces:**
- Consumes a deployed production image with the new migration and a human-approved recovery-drills package.
- Produces immutable evidence for all five drill outcomes and the final closed run state.

- [ ] Build and deploy the reviewed amd64 image through the approved Coolify flow.
- [ ] Verify migration, live readiness, running image digest, and production OpenAPI routes before creating a run.
- [ ] Obtain explicit approval immediately before drill 1 restarts the live application.
- [ ] Run all five drills sequentially; capture only redacted assertions and IDs in the evidence document.
- [ ] Verify the run-scoped state reports every assertion passed and all synthetic resources closed.
- [ ] Re-run normal dead-letter, in-flight, and consistency views to prove synthetic data is excluded by default.
- [ ] Run `make check`, inspect collected-test count, run the security scan, and perform the required code review before requesting a PR.
