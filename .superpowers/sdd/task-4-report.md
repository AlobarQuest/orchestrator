# Task 4 Report: Close Synthetic Work Without Deletion

## Scope

Implemented the production-drill closeout endpoint and its registry-scoped final invariant. No
shell runner behavior was added.

## Red/Green Evidence

- Red: the new focused closeout suite initially failed at collection because
  `CloseProductionDrill` and `close_production_drill()` did not exist.
- Green: `uv run pytest tests/services/test_production_drill_closeout.py
  tests/api/test_production_drill_closeout_api.py -q` completed with `8 passed`.
- Green: the broader drill and append-only verification completed with `50 passed`.
- Green: Ruff, Pyright, and `git diff --check` completed without findings.

## Behavior

- `POST /api/v1/production-drills/{run_id}/close` is HUMAN-only and records an explicit
  `production_drill_closed` event.
- Closeout examines only resources registered to the requested run. It rejects active claims,
  nonterminal synthetic units, and unresolved run-owned reconciliation conditions; ordinary
  units are not read or changed.
- Successful closeout sets the run and registered-resource closure timestamps without deleting
  evidence, claims, observations, reconciliation rows, or events.
- Replays require the exact close command; a later close with a different closure reason is
  rejected.

## Verification

```bash
uv run pytest tests/services/test_production_drill_closeout.py tests/api/test_production_drill_closeout_api.py tests/services/test_production_drills.py tests/services/test_production_drill_resources.py tests/services/test_production_drill_controls.py tests/api/test_production_drills_api.py tests/api/test_production_drill_controls_api.py tests/persistence/test_append_only.py -q
uv run ruff check src/orchestrator/services/production_drills.py src/orchestrator/api/routes.py tests/services/test_production_drill_closeout.py tests/api/test_production_drill_closeout_api.py
uv run pyright src/orchestrator/services/production_drills.py src/orchestrator/api/routes.py tests/services/test_production_drill_closeout.py tests/api/test_production_drill_closeout_api.py
git diff --check
```

Results: `50 passed`; Ruff and Pyright reported no issues.

## Commit

`694313e feat: close production drill runs`

## Concerns

- Closeout deliberately does not transition or resolve synthetic state. It is the auditable
  HUMAN acceptance point and rejects until all registered assertions are already terminal and
  reconciled through their existing public controls.

## P1 Closeout Correction

- Closeout now performs the terminal work rather than rejecting an open run: it cancels only
  registered nonterminal synthetic work units, releases only their active claims with
  `production_drill_closed`, and resolves only registered reconciliation conditions as
  `dismissed` with a `production_drill_closed: <closure reason>` rationale.
- The lifecycle closeout primitive is HUMAN-only and registry-scoped, emits a normal
  `work_unit.transitioned` audit event, and is unavailable for ordinary work units. All state
  changes, reconciliation resolutions, resource closure timestamps, and the final
  `production_drill_closed` event commit atomically after the final invariant succeeds.
- Focused tests prove run-owned cancellation and claim release, run-owned condition resolution,
  and that an ordinary unit and ordinary reconciliation condition remain untouched.

### P1 Verification

`uv run pytest tests/services/test_production_drill_closeout.py
tests/api/test_production_drill_closeout_api.py tests/services/test_production_drills.py
tests/services/test_production_drill_resources.py tests/services/test_production_drill_controls.py
tests/api/test_production_drills_api.py tests/api/test_production_drill_controls_api.py
tests/persistence/test_append_only.py -q` completed with `50 passed`.

Ruff, Pyright, and `git diff --check` completed without findings.
