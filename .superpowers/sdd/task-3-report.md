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
