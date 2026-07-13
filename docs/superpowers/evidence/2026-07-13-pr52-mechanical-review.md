# PR #52 Mechanical Review

**Captured:** 2026-07-13, America/New_York

**Review base:** `5f01151f0e4622872c541b9f1fae35559db77e67`

**PR #52 merge commit:** `1f0a2369a33d706673bec4ebe2dda87754b9dbe7`

**PR #52 diff:** `1f0a236^1..1f0a236`

This is a repository and local-fixture review. It does not claim that PR #52 is deployed or that
any production drill has run. No production, Coolify, BWS, credential, or external-infrastructure
mutation was performed.

## Scope And Commits

The review consumed the 59-path PR #52 inventory and R1-R10 trace in
`docs/superpowers/evidence/2026-07-13-pr52-requirements-trace.md`. It inspected the three new
migrations, the production runner, the 23 production application paths, the representative
architecture controls, and the affected operational documentation. All database-backed commands
ran sequentially against one disposable `postgres:16-alpine` container on local port 55432. The
container reported ready, and the EXIT trap removed it after verification.

## Full Quality Gate

Command:

```bash
PATH="$PWD/.venv/bin:$PATH" make check 2>&1 | tee /tmp/pr52-make-check.log
```

Exit status: `0`.

- Ruff check: passed (`All checks passed!`).
- Ruff format check: passed (`237 files already formatted`).
- Pyright: passed (`0 errors, 0 warnings, 0 informations`).
- Pytest: collected 1,375 tests; **1,374 passed and 1 skipped** in 315.15 seconds.

The follow-up count command found the terminal result at line 151 of the log. The required
zero-test predicate was rerun without a pipeline:

```bash
! rg -n 'no tests ran|collected 0 items' /tmp/pr52-make-check.log
```

Exit status: `0`; neither zero-test phrase was present. An earlier wrapper piped `rg` to `tee`
without `pipefail`, so it reported the status of `tee` instead of `rg`. That wrapper result was
discarded as a verification-harness defect, not treated as a product failure.

## Migration Upgrade/Downgrade

On an empty PostgreSQL 16 database:

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

Exit statuses: `0`, `0`, `0`. Both `current` and `heads` reported
`0017_runtime_observations (head)`.

The checkpoint's prescribed downgrade command was also run exactly:

```bash
uv run alembic downgrade 0014_ws21_recovery_controls
```

Exit status: `255`; Alembic reported that it could not locate that revision. The actual revision
identifier is `0014_wsp21_recovery_controls` (with `wsp`, not `ws`). After the failed lookup,
`alembic current` still reported `0017_runtime_observations (head)`.

The product rollback path was then exercised with the real revision:

```bash
uv run alembic downgrade 0014_wsp21_recovery_controls
uv run alembic current
uv run alembic upgrade head
uv run alembic current
```

Exit statuses: `0`, `0`, `0`, `0`. The intermediate current revision was
`0014_wsp21_recovery_controls`; the final revision was `0017_runtime_observations (head)`.

This proves mechanical downgrade and re-upgrade only on an empty fixture. The downgrade functions
drop `runtime_observations`, `production_drill_resources`, and `production_drill_runs`; they are not
a retention-safe rollback after live observations or drill evidence exist.

## Planted-Defect Controls

The three controls ran sequentially against the same local environment.

1. Command:
   `uv run pytest tests/architecture/test_unreachable_guards.py::test_the_guard_flags_a_service_whose_only_production_caller_was_removed -q`
   — exit `0`, `1 passed in 0.02s`. The test first creates a service with one reachable route,
   then overwrites the route to remove its only production caller. The import-resolved call graph
   changes the service from reachable to unreachable. This is a real planted defect and proves the
   guard rejects the reconstructed WS-P2.1 dead-service shape without name-collision laundering.
2. Command:
   `uv run pytest tests/architecture/test_production_drill_runner.py::test_runner_rejects_a_missing_openapi_operation_before_authenticated_mutation -q`
   — exit `0`, `1 passed in 0.46s`. The test supplies an OpenAPI document in which required scenario
   and failure operations have `GET` instead of `POST`, and makes credential lookup fail if reached.
   The runner exits on the unauthenticated OpenAPI preflight after exactly one `/openapi.json`
   request. This is a real planted contract defect and proves no authenticated mutation precedes
   operation verification.
3. Command:
   `uv run pytest tests/architecture/test_container.py::test_runtime_auth_loads_embedded_registry_and_fails_closed -q`
   — exit `0`, `1 passed in 0.44s`. After a valid embedded-registry load, the test aliases the
   observer credential ID to the drill credential ID and separately removes the CSRF secret;
   startup raises `RuntimeError` for both. These are real planted configuration defects and prove
   distinct credential enforcement plus fail-closed required-auth configuration.

No control is a pass-only decoration.

## Portfolio Code Review

Command:

```bash
/Users/devon/Developer/code-standards/.venv/bin/code-standards check --repo .
```

Exit status: `0`.

The exact PR #52 suppression scan found eight additions, all in
`tests/architecture/test_production_drill_runner.py`. Every addition is `# noqa: E501` with a
same-line reason explaining why a shell mock response or payload boundary must remain one line.
No added `# type: ignore` or `eslint-disable` suppression was found.

```bash
git diff 1f0a236^1..1f0a236 -- '*.py' '*.sh' | \
  rg '^\+.*(# noqa|# type: ignore|eslint-disable)' || true
```

Exit status: `0`; eight justified lines were printed.

Manual standards review still identifies a material reviewability smell:
`src/orchestrator/services/production_drills.py` is 1,630 lines and combines authorization,
provenance, five scenario implementations, state projection, failure, and closeout. The runner
helper is 335 lines. These exceed the portfolio's approximate 300-line file threshold. This is not
an independent test failure, but it raises the cost and risk of proving the contract correct.

## Security Scan

The prescribed pipeline and follow-up assertion were run exactly:

```bash
PYTHONPATH="$HOME/Projects/security-standards/src" \
  python3 -m security_scan.cli . --category security 2>&1 | \
  tee /tmp/pr52-security-scan.log
rg -n '0 BLOCK' /tmp/pr52-security-scan.log
```

The pipeline's aggregate status was not captured independently; without `pipefail`, it represents
`tee`, not the scanner. The scanner component status was captured immediately from zsh
`pipestatus[1]` as `1`. Its log shows that bare `python3` resolved to Apple's Python 3.9 and failed
importing standard-library `tomllib` before any scan ran. The subsequent literal `rg` command exited
`1` because the failure log contained no BLOCK summary. The security-standards package requires
Python 3.12 or newer; this shell's bare `python3` does not satisfy that requirement, so the
repository virtual environment was used for the supported rerun.

The supported equivalent was then run:

```bash
PYTHONPATH="$HOME/Projects/security-standards/src" \
  /Users/devon/Projects/security-standards/.venv/bin/python \
  -m security_scan.cli . --category security 2>&1 | \
  tee /tmp/pr52-security-scan-supported.log
```

The supported scanner component status, again captured from `pipestatus[1]`, was `0`. Its JSON
summary reported **0 BLOCK, 0 WARN, and 1 INFO**. The INFO is the scanner's judgment-only reminder
to verify BWS machine-account scope; this review did not retrieve credentials or expand into
infrastructure inspection.

Running the checkpoint's literal `rg -n '0 BLOCK'` guard against the supported JSON log also exited
`1`, because the current scanner emits `"BLOCK": 0`, not the phrase `0 BLOCK`. The supported
scanner component status and JSON counts are clean; both the bare-interpreter invocation and
text-format assertion are stale harness assumptions.

## Startup And Rollback

Command:

```bash
rg -n 'ORCHESTRATOR_(PRODUCTION_DRILL|RUNTIME_OBSERVER)_CREDENTIAL_KEY_ID' src tests docs
```

Exit status: `0`; application, test, and handoff references were found. The migration rollback
commands and statuses are recorded in `Migration Upgrade/Downgrade` above.

Application startup is fail-closed when runtime authentication is configured: it requires both
`ORCHESTRATOR_PRODUCTION_DRILL_CREDENTIAL_KEY_ID` and
`ORCHESTRATOR_RUNTIME_OBSERVER_CREDENTIAL_KEY_ID`, requires both to map to SYSTEM, and rejects an
identical pair. This is sound local validation but creates an unsafe partial-rollout dependency:
deploying the code before both new configuration values exist prevents application startup. There
is no feature gate or compatibility path for configuration and migrations landing separately.

The empty-database migration cycle is mechanically reversible. It is not a safe live rollback:
the three downgrade functions delete the retained runtime observation, drill-resource ownership,
and drill-run tables. A deployment or rollback plan must explicitly preserve or accept loss of
those audit records; PR #52 contains no such plan or compatibility path.

## Authority Separation

Command:

```bash
rg -n 'ActorRole\.HUMAN|require_production_drill_actor|require_runtime_observer_actor' src/orchestrator
```

Exit status: `0`; HUMAN-role checks were found. The two `require_...` names themselves were absent,
so `get_production_drill_actor` and `get_runtime_observer_actor` were located and inspected in
`api/dependencies.py` and their route call sites.

- HUMAN-only start and close are reachable through the general actor dependency and enforced again
  by `_require_human`; SYSTEM actors cannot start or close a run.
- Scenario and failure routes use `get_production_drill_actor`, which requires the configured drill
  credential ID and SYSTEM role. Runtime-observation ingestion uses `get_runtime_observer_actor`,
  which requires the distinct configured observer credential ID and SYSTEM role.
- The checkpoint search names `require_production_drill_actor` and
  `require_runtime_observer_actor`, but the implemented dependency names are `get_...`; the exact
  name search is empty even though follow-up inspection proves the endpoint guards are reachable.
- Separation is endpoint-local, not credential-wide. `get_actor` still authenticates either key as
  a general SYSTEM actor on other SYSTEM-authorized routes. Therefore PR #52 does not prove R3's
  least-privilege requirement across the API, although neither credential can become HUMAN.

## Transaction And Idempotency Boundaries

Command:

```bash
rg -n 'session\.(commit|flush|rollback)' \
  src/orchestrator/services/production_drills.py \
  src/orchestrator/services/production_drill_resources.py \
  src/orchestrator/services/runtime_observations.py
```

Exit status: `0`; commit, rollback, and flush boundaries were found and inspected. A follow-up
search for `production_drill_scenario_atomic` found every service wrapper participating in the
scenario-wide commit-suppression convention.

- Runtime observations validate, acquire a PostgreSQL advisory transaction lock keyed by the
  idempotency key, compare replay payloads, append the event and immutable row, and commit once;
  domain and integrity failures roll back.
- Drill start, close, scenario, and failure wrappers commit once and roll back domain or unexpected
  failures. Scenario execution sets `production_drill_scenario_atomic` in `session.info`; affected
  service writers suppress their ordinary internal commits so the scenario wrapper owns the
  transaction. A scenario `DomainError` rolls back synthetic mutation before recording a terminal
  failure event.
- Resource binding locks ownership queries, rejects cross-run ownership, and flushes without
  independently committing. Run rows are selected `FOR UPDATE` before mutation. Advisory locks
  serialize reused idempotency keys, and replay payload comparisons reject key reuse for a
  different operation.

These boundaries are mechanically exercised by the passing suite. The session-info commit
suppression convention spans several services, which increases coupling and review cost, but no
specific atomicity failure was reproduced in this gate.

## Synthetic Data Isolation

Command:

```bash
rg -n 'include_production_drill_resources' src/orchestrator tests
```

Exit status: `0`; default-false production definitions and two test-only true opt-ins were found.

Production-drill resources are explicitly bound to a run and namespaced by fixed synthetic
templates. Ordinary dead-letter and in-flight projections default
`include_production_drill_resources` to `False`; tests are the only callers in the exact search
that opt into `True`. Web queue and related projections exclude run-owned resources by default.
The opt-ins are explicit rather than caller-controlled API query parameters.

## Adapter Reality

Command:

```bash
rg -n 'docker|ssh|exec|subprocess|os\.system|shell=True|restart' \
  src/orchestrator/services/runtime_observations.py scripts/production_drill_common.sh
```

Exit status: `0`; the hits were the SQLAlchemy advisory-lock call and bounded operator-handoff
restart language described below.

The exact executor search found no Docker, SSH, subprocess, `os.system`, `shell=True`, generic
host executor, caller-selected target, or executable restart hook in the runtime-observation
service or production runner. Its only `exec` substring hit was SQLAlchemy `session.execute` for
an advisory lock. Restart references describe the explicit two-phase operator handoff: the runner
persists attempt one, exits for separately approved Coolify work, and later resumes to reclaim.

The constrained read-only runtime observer required by R2 does not exist in this repository.
`docs/operations/runtime-observations.md` states that prerequisite explicitly. The ingestion API
validates the shape of caller-submitted container, image, and OpenAPI digest fields, but no PR #52
adapter obtains the external container identity or hashes the raw live OpenAPI bytes. The public
HTTP-only runner is bounded to five scenarios and the fixed `https://sds.alobar.net` target, but
production provenance cannot yet be collected end to end.

## Blocking Findings

1. **R2 cannot be satisfied end to end:** the constrained external runtime observer and raw-live-
   OpenAPI hashing adapter are absent. Caller-submitted provenance shapes are not independent
   production observation.
2. **R3 least privilege is incomplete:** the two keys are distinct and endpoint-restricted on the
   new routes, but each remains a general SYSTEM identity on other SYSTEM-authorized surfaces.
3. **R8 closeout does not require all five assertions:** a newly opened run with no drill resources
   can close, and the test suite explicitly proves any one successful scenario remains HUMAN-
   closeable. The server retains no five-scenario completeness predicate; runner-local JSON is not
   sufficient retained authority.
4. **R9 rollout and rollback are unsafe:** authenticated startup cannot tolerate either new key
   being absent during partial rollout, and downgrade deletes retained drill/observation data.
   Empty-fixture reversibility is not a live rollback plan.
5. **R10 is absent:** no executable retained-evidence-to-scorecard guard exists. Merge state,
   route presence, and green tests therefore cannot mark the production-proof criteria MET.

These findings block deploying PR #52 as a complete production-proof implementation. They do not
invalidate the local mechanical successes recorded above.

## Non-blocking Findings

1. The checkpoint uses the nonexistent Alembic label `0014_ws21_recovery_controls`; the repository
   revision is `0014_wsp21_recovery_controls`.
2. The prescribed security command selects an unsupported Python 3.9 on this machine, and its
   `0 BLOCK` text assertion does not match the scanner's current JSON format. The supported scan is
   clean at BLOCK/WARN severity.
3. The 1,630-line production-drill service and 335-line runner helper exceed portfolio structure
   guidance and make a safety-critical path unnecessarily difficult to review.
4. The authority search in the checkpoint uses obsolete `require_...` dependency names; the
   implemented and reachable names are `get_production_drill_actor` and
   `get_runtime_observer_actor`.
