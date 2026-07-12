# Task 3 Report: Run-Scoped Assertions and Timing Controls

## Scope

Implemented the Task 3 production-drill control contract only. No runner or closeout behavior
was added.

## Red/Green Evidence

- Red: `test_deadline_controls_are_bounded_without_mutating_global_thresholds` initially expected
  validation-style status codes; the API correctly surfaced the service-domain rejections as 409.
  The test was corrected to assert both the 409 response and the precise error codes.
- Green: `uv run pytest tests/services/test_production_drill_controls.py
  tests/api/test_production_drill_controls_api.py tests/services/test_production_drills.py
  tests/services/test_production_drill_resources.py tests/api/test_production_drills_api.py
  tests/architecture/test_drill_scripts.py -q` completed with `54 passed`.
- Green: focused Ruff check completed with no findings and `git diff --check` completed cleanly.

## Tests Added

- Service deadline floor and configured-ceiling rejection.
- Drill-unit lease duration selection with ordinary `LEASE_DURATION` unchanged.
- State projection run isolation.
- API worker rejection, deadline rejection/error codes, and unchanged global reporting thresholds.

## Commit

`feat: add production drill timing controls`

## Concerns

- Deadline values are immutable facts in the existing `production_drill.started` authorization
  event. This avoids global settings mutation and does not require a mutable run column.
- The state projection uses ORM reads and computes evidence heads as terminal supersession rows
  (no later evidence references the row); conditions remain open only when no resolution exists.

## Review Fix Pass

- Removed the caller-controlled deadline maximum from `StartProductionDrill`; validation now
  reads `production_drill_max_deadline_seconds` from the service configuration.
- Run-scoped reconciliation now requires every deployment input it processes to belong to the
  requested run: units and deployment observations for stalled verification, and observations
  plus release artifacts for unreported deployments.
- Added regression coverage for a forged deadline ceiling and a deployment report owned by a
  different drill run.

Verification:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/services/test_production_drill_controls.py tests/api/test_production_drill_controls_api.py tests/services/test_production_drills.py tests/services/test_production_drill_resources.py tests/api/test_production_drills_api.py tests/services/test_reconciliation_detect_pass.py tests/architecture/test_drill_scripts.py -q
PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/api/routes.py src/orchestrator/services/production_drills.py src/orchestrator/services/reconciliation_detection.py tests/services/test_production_drill_controls.py
PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/services/production_drills.py src/orchestrator/services/reconciliation_detection.py tests/services/test_production_drill_controls.py
```

Results: `62 passed`; Ruff and Pyright reported no findings.
