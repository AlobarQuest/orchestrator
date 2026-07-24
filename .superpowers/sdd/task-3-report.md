# Task 3 Report: `slo_report` service skeleton + shared test builders (WS-P2.2)

## Status

DONE_WITH_CONCERNS (see "Architecture-suite finding" below — the skeleton itself is
complete and green per the brief's acceptance criteria).

Branch: `ws-p2.2-slo-observability` (no new branch created, per instructions).

## Files Changed

- `src/orchestrator/services/slo_report.py` (new)
- `tests/services/test_slo_report.py` (new)

## Implementation

Followed `task-3-brief.md` verbatim for the module and the three skeleton tests:

- `SloReportFilters`, `MetricValue`, `SloReport` — frozen dataclasses as specified.
- Status constants `STATUS_COMPUTED`, `STATUS_NO_DATA`, `STATUS_NOT_INSTRUMENTED`,
  `STATUS_PARTIAL`; `DEFAULT_WINDOW = timedelta(days=7)`.
- `slo_report(session, filters=None)` computes `since`/`until` from
  `TransactionClock().now(session)` when not given explicitly, then delegates to seven
  `(session, since, until, now) -> MetricValue` private helpers, all currently
  `STATUS_NO_DATA` stubs, **except** `_cost` and `_tokens`, which return
  `STATUS_NOT_INSTRUMENTED` from the start (per the brief — these have no source data
  anywhere in the store and must never be silently zero-filled).
- Shared test builders `_build_unit`, `_add_event`, `_add_claim` reused verbatim from
  the brief, for Tasks 4-7 to import/copy.

**REQUIRED ADDITION (authorized by the controller, per task instructions):** added
`test_shared_builders_smoke`, beyond the brief's three skeleton tests. It calls
`_build_unit(migrated_session, "smoke")`, commits, asserts the revision and unit
persisted with real ids, then calls `_add_event` and `_add_claim` against that unit,
commits again, and re-queries each row back via `select(...)` to prove it actually
persisted (not just an in-session object echo — see the repo's own "flush is not
commit" invariant). This is the only test that exercises the shared builders in this
task; Tasks 4-7 would otherwise have been the first callers to discover a builder
defect.

I did not add `registered_at` control to `_build_unit` (YAGNI per instructions — Task 5
handles that by overriding after build).

## TDD Evidence

### RED

```
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -v
```
```
ImportError while importing test module '.../tests/services/test_slo_report.py'.
tests/services/test_slo_report.py:11: in <module>
    from orchestrator.services.slo_report import (
E   ModuleNotFoundError: No module named 'orchestrator.services.slo_report'
=========================== short test summary info ============================
ERROR tests/services/test_slo_report.py
=============================== 1 error in 0.11s ===============================
```

### GREEN

```
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/test_slo_report.py -v
```
```
collected 4 items

tests/services/test_slo_report.py::test_empty_store_reports_no_data_and_not_instrumented PASSED [ 25%]
tests/services/test_slo_report.py::test_cost_and_tokens_are_not_instrumented PASSED [ 50%]
tests/services/test_slo_report.py::test_explicit_window_is_respected PASSED [ 75%]
tests/services/test_slo_report.py::test_shared_builders_smoke PASSED     [100%]

============================== 4 passed in 1.22s ===============================
```

### Scope guard (bare `dispatch`/`deploy` words under `src/orchestrator/`)

```
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/architecture/test_ws32_scope_guards.py -v
```
```
collected 4 items
... 4 passed in 0.18s
```
`slo_report.py` uses "hand-off to the runner" / "release-revert" phrasing where the
design doc used the forbidden words; grepped the file afterward for `dispatch`/`deploy`
to confirm zero occurrences.

### Lint / type-check on the changed files

```
.venv/bin/ruff check src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
.venv/bin/pyright src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
```
- Ruff first pass found 2 issues: an unsorted import block in the test file (fixed via
  `ruff check --fix`, which reordered `STATUS_NO_DATA`/`STATUS_NOT_INSTRUMENTED`) and one
  `E501` line-too-long in `_cost`'s basis string (wrapped across an extra line, same
  content). Both are formatting-only; no logic changed from the brief's verbatim code.
- Final: `All checks passed!` (ruff), `0 errors, 0 warnings, 0 informations` (pyright).

### Broader regression check

```
SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards" .venv/bin/pytest tests/services/ tests/architecture/ -q
```
```
1 failed, 743 passed, 1 skipped in 184.09s (0:03:04)
```
The one failure is `test_every_public_kernel_and_service_function_is_reachable` flagging
`orchestrator.services.slo_report.slo_report` as unreachable — see below. Re-ran
`tests/services/test_slo_report.py tests/architecture/test_ws32_scope_guards.py` after
the ruff --fix edits: `8 passed`.

## Architecture-suite finding (concern for the reviewer)

Adding `slo_report()` as a new public service function with no production caller trips
`tests/architecture/test_unreachable_guards.py::test_every_public_kernel_and_service_function_is_reachable`
(the WS-P2.15 reachability guard documented in this repo's CLAUDE.md invariants). It is
real: nothing outside this task's tests calls `slo_report`, `SloReportFilters`, or
`SloReport` yet.

I deliberately did **not** work around it:
- **Did not add an `ALLOWLIST` entry.** Every existing entry documents a symbol that is
  unreachable *by design, permanently* (e.g. the `github_app.reset_token_providers`
  test-isolation seam). `slo_report` is not that — it is a report generator that is
  *supposed* to get a caller (CLI/API) once the metric-filling tasks are done. Writing
  an allowlist justification here would be exactly the "in fact it is called" shape of
  wrong-predicate reasoning the guard's own docstring warns against, just inverted ("in
  fact it will be called soon" is equally not "unreachable ON PURPOSE").
- **Did not add a CLI/API caller.** `task-3-brief.md` scopes this task to the module
  skeleton and shared builders only; `task-4-brief.md` (confirmed real WS-P2.2 content —
  it imports `_build_unit`/`_add_claim` from this task and fills in
  `_claim_expiry_rate`/`_waiver_frequency`) also only touches `slo_report.py` and its
  test file, with no caller either. That means this is a known, staged multi-task build
  where the CLI/API wiring is deferred to a task beyond what I have visibility into
  (no `task-6`/`task-7`/`task-8` brief exists yet in `.superpowers/sdd/`). Adding a
  caller now would be scope creep beyond this task's brief and untested by anything in
  this task.

**Recommendation:** confirm the plan has a later task that wires `slo_report` to a
CLI command or API route before the workstream is considered done — until then, expect
`test_every_public_kernel_and_service_function_is_reachable` to keep failing on this
branch through Tasks 4-7. This is not something to silently allowlist away.

## Other observations

- `.superpowers/sdd/task-3-report.md` (this file) previously held an unrelated, stale
  report from a different workstream ("Context Preflight Service for WS-3.3" /
  `codex/ws33-design`). Overwritten per this task's explicit instruction to write the
  report to this exact path — flagging in case the old content was still needed
  elsewhere.
- `.superpowers/sdd/task-5-brief.md` in this same directory is likewise unrelated to
  WS-P2.2 (it describes a Production Drills workstream); did not touch it.
- `git status` shows `.superpowers/sdd/task-2-report.md` modified in the working tree.
  I did not make this change — it was already present when I started (untouched by me,
  not staged/committed by me). Likely a concurrent session in the same working
  directory; flagging so the controller isn't surprised by it in a later diff.

## Commit

```
git add src/orchestrator/services/slo_report.py tests/services/test_slo_report.py
git commit -m "feat(slo): slo_report skeleton with status-typed metrics + cost guard (WS-P2.2)"
```

Only these two files were staged and committed — `task-2-report.md`'s pre-existing
working-tree modification was deliberately left out of the commit.
