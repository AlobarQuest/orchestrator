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
