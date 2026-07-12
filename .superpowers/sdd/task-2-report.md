# Task 2 Report: Bind Synthetic Resources To A Run

## Implementation

- Added `ProductionDrillResource` and migration `0016_production_drill_resources`.
- Enforced one owner per `(resource_type, resource_id)` and restricted resource types.
- Added the internal production-drill resource registry, including open-run, existence,
  revision, and ownership validation.
- Added dedicated production-drill writer helpers for units, evidence, observations,
  reconciliation conditions, release artifacts, and post-deploy units. Ordinary lifecycle
  command signatures do not accept a run ID.
- Excluded drill-tagged work from the ordinary web queue, dead-letter projection, and
  in-flight projection. The service projections retain an internal
  `include_production_drill_resources=True` opt-in.

## Files

- `src/orchestrator/persistence/models.py`
- `migrations/versions/0016_production_drill_resources.py`
- `src/orchestrator/services/production_drill_resources.py`
- `src/orchestrator/services/packages.py`
- `src/orchestrator/services/evidence.py`
- `src/orchestrator/services/observations.py`
- `src/orchestrator/services/reconciliation.py`
- `src/orchestrator/services/dead_letter.py`
- `src/orchestrator/services/in_flight.py`
- `src/orchestrator/web.py`
- `tests/services/test_production_drill_resources.py`

## Verification

- Observed red test: collection failed because `production_drill_resources` did not exist.
- `uv run ruff check` on all Task 2 files: passed.
- Focused pytest suite: 67 passed.
  - `test_production_drill_resources.py`
  - lifecycle guards, evidence, observations, reconciliation, release artifacts,
    in-flight, and dead-letter suites
- `git diff --check`: passed.

- Reviewed the diff against `~/Developer/code-standards/STANDARDS.md`; no suppression
  comments or unresolved review findings remain.

## Commit

- `feat: bind synthetic resources to production drill runs`

## Concerns

- No public drill-control endpoints, deadlines, runner logic, or closeout behavior were
  implemented; those remain Tasks 3-5.

## Review Fix Pass

### Findings Addressed

- Prevented direct capture of ordinary work units and observations; root resources are bound
  only by production-drill creation paths.
- Preserved drill observation idempotency while rejecting an ordinary observation replay unless
  the observation is already owned by the same open run.
- Required run ownership and open status before drill lifecycle transitions and before a
  reconciliation condition can reference an observation or deployment observation.
- Required post-deploy units to trace to a run-owned deployment observation before binding.

### Red Evidence

- Added regressions for direct ordinary-unit capture, ordinary-observation idempotency replay,
  ordinary observation and deployment-observation condition references, and ordinary-unit
  lifecycle control.
- Before the fix, `uv run pytest tests/services/test_production_drill_resources.py -q` failed
  the first three cases because the service bound existing ordinary rows and accepted their
  references.

### Green Evidence

- `uv run pytest tests/services/test_production_drill_resources.py tests/services/test_lifecycle_events.py tests/services/test_lifecycle_guards.py tests/services/test_lifecycle_rollback.py tests/services/test_evidence.py tests/services/test_evidence_recovery.py tests/services/test_observations.py tests/services/test_reconciliation.py tests/services/test_reconciliation_detect_pass.py tests/services/test_reconciliation_detection_check.py tests/services/test_reconciliation_detection_pr.py tests/services/test_deployment_observations.py tests/services/test_package_registration.py -q` -> `123 passed`.
- `uv run ruff check` on all changed Task 2 service and regression-test files: passed.
- `git diff --check`: passed.

## Re-review P1 Fix Pass

### Findings Addressed

- Removed the public generic registry binding functions. Resource ownership is now registered
  only from drill-specific creation writers.
- Evidence and reconciliation condition writers now distinguish a newly created row from a
  replay and require existing ownership by the same open run for replays.
- Added drill-specific release-artifact and deployment-observation writers. They register only
  records created by that call; replayed release artifacts and deployment observations must
  already belong to the same run. A newly created deployment observation also binds its derived
  post-deploy work unit and evidence rows in the same transaction.

### Red Evidence

- Added regressions for removed generic binding, ordinary evidence and reconciliation-condition
  replay, and ordinary release-artifact and deployment-observation replay.
- Before implementation, `uv run pytest tests/services/test_production_drill_resources.py -q`
  failed at collection because the drill-specific release/deployment writer APIs did not exist.

### Green Evidence

- `uv run pytest tests/services/test_production_drill_resources.py tests/services/test_lifecycle_events.py tests/services/test_lifecycle_guards.py tests/services/test_lifecycle_rollback.py tests/services/test_evidence.py tests/services/test_evidence_recovery.py tests/services/test_observations.py tests/services/test_reconciliation.py tests/services/test_reconciliation_detect_pass.py tests/services/test_reconciliation_detection_check.py tests/services/test_reconciliation_detection_pr.py tests/services/test_deployment_observations.py tests/services/test_package_registration.py tests/services/test_release_artifacts.py -q` -> `134 passed`.
- `uv run ruff check` on all changed Task 2 service and regression-test files: passed.
- `git diff --check`: passed.

## Final P1 Fix Pass

### Findings Addressed

- Ordinary `transition_unit` now rejects a work unit owned by any production drill; the
  run-scoped `transition_production_drill_unit` remains the only lifecycle path for it.
- Replaced the generic `(resource_type, resource_id)` creation binder with concrete-resource
  registration APIs, so each drill writer supplies its actual ORM resource rather than a
  caller-selected type/id pair.
- Drill release-artifact creation now registers both the binding and its generated evidence.
  A replay verifies ownership of both records before it succeeds.

### Green Evidence

- Added inverse regressions for ordinary lifecycle rejection with a successful drill-wrapper
  transition, and for release-artifact evidence registration plus missing-evidence replay
  rejection.
- `uv run pytest tests/services/test_production_drill_resources.py -q` -> `14 passed`.
- Focused Task 2 service suite, including lifecycle, evidence, observations, reconciliation,
  deployment observations, package registration, and release artifacts: passed.
- `uv run ruff check` on all changed service and regression-test files: passed.
- `git diff --check`: passed.

## Registration Provenance Race Fix

### Finding Addressed

- `register_production_drill_unit` previously used an unlocked pre-read to decide whether to
  bind a work unit. An ordinary registration could commit after that read and before the drill
  writer's registration call, allowing the drill writer to capture ordinary work.

### Resolution

- The locked unit-registration path now returns private creation provenance with the work unit.
  The drill writer binds only a row created by that same registration transaction; all existing
  and idempotent replay rows must already belong to the open drill run.
- Added a two-session regression that commits ordinary registration in the former interleaving
  window and verifies the drill writer rejects it as unowned.

### Verification

- Red: the regression initially failed because the drill writer returned successfully and bound
  the ordinary unit.
- Green: `.venv/bin/pytest tests/services/test_production_drill_resources.py tests/services/test_package_registration.py -q` -> `26 passed`.
- `.venv/bin/ruff check src/orchestrator/services/packages.py tests/services/test_production_drill_resources.py`: passed.
- `git diff --check`: passed.

## Concurrency Regression Test Quality Fix

### Finding Addressed

- The original registration-race regression started and committed the ordinary registration
  before the drill writer entered its locked registration path. It verified the final ownership
  error but did not prove that the revision lock prevented a competing registration from
  committing in the critical transaction window.

### Resolution

- Replaced the sequential setup with two SQLAlchemy sessions coordinated by a barrier. The drill
  writer pauses immediately after acquiring its revision `FOR UPDATE` lock; the ordinary writer
  then crosses the barrier and attempts registration.
- The regression asserts that the ordinary writer cannot commit until the drill transaction is
  released, then confirms both writers resolve to the same work unit and that unit is owned by
  the drill run. Production code remains unchanged.

### Verification

- The prior sequential regression passed but did not exercise a blocked competing transaction.
- `.venv/bin/pytest tests/services/test_production_drill_resources.py::test_concurrent_ordinary_registration_cannot_be_captured_as_drill_work tests/services/test_package_registration.py -q` -> `12 passed`.
- `.venv/bin/ruff check tests/services/test_production_drill_resources.py`: passed.
- `.venv/bin/pyright tests/services/test_production_drill_resources.py`: `0 errors, 0 warnings, 0 informations`.
