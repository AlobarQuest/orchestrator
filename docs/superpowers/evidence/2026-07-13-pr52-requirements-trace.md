# PR #52 Requirements Trace

**Captured:** 2026-07-13, America/New_York
**Merge commit:** `1f0a2369a33d706673bec4ebe2dda87754b9dbe7`
**Diff range:** `1f0a236^1..1f0a236`
**Original production-proof statement:** Run five recovery drills against production without
private SQL or unbounded infrastructure authority.

This document classifies repository paths only. It does not select a keep, narrow, or revert
disposition, and it does not claim that repository implementation is deployed or
production-proven.

## Minimum Contract

| ID | Requirement |
|---|---|
| R1 | A browser-authenticated HUMAN authorizes and later closes one run against the exact approved `ws-p2.1-recovery-controls-drills` revision. |
| R2 | Production evidence binds to externally observed runtime identity and raw live OpenAPI bytes, never caller-supplied provenance strings. |
| R3 | Observer and drill SYSTEM credentials are distinct, least-privileged identities; neither can act as HUMAN. |
| R4 | The runner can invoke only five fixed scenarios against one fixed `sds.alobar.net` target and one HUMAN-created run. |
| R5 | Synthetic drill records are namespaced, retained, auditable, and excluded from ordinary operator projections. |
| R6 | The production runner uses public HTTP APIs only; it has no SQL, Docker socket, root SSH, generic executor, caller-selected host, or executable restart hook. |
| R7 | Crash recovery is a two-phase protocol with a separately approved operator restart between durable attempt one and reclaimed attempt two. |
| R8 | A run cannot be accepted until every fixed assertion and synthetic record has an auditable terminal state; runner failure cannot impersonate HUMAN closeout. |
| R9 | Deployment and rollback remain safe when migrations or new credential configuration are absent, distinct, invalid, or partially applied. |
| R10 | Program scorecard claims are checked against retained live production evidence; merge or route declarations cannot mark a criterion MET. |

## Classification Method

- `required`: directly implements at least one R1-R10 requirement.
- `defensive`: does not implement a requirement directly, but contains a bounded safeguard for
  the production-proof path.
- `accidental`: was changed to accommodate implementation mechanics but is not needed by the
  minimum contract or its bounded safeguards.
- `unrelated`: has no production-proof relationship.

Every production, migration, and runner path has exactly one classification below. `none` would
identify a path with no R1-R10 mapping; no such path is classified `required`.
Classification records why a path exists in the proposed contract; it does not by itself prove
that the requirement is fully implemented or production-proven.

## Complete PR #52 Inventory

The name-status inventory contains exactly **59 changed paths**. Added/deleted counts are from
`git diff --numstat 1f0a236^1..1f0a236`.

### Generated SDD Reports (4)

| Status | File | Added/deleted |
|---|---|---:|
| M | `.superpowers/sdd/task-2-report.md` | 202/64 |
| M | `.superpowers/sdd/task-3-report.md` | 46/102 |
| A | `.superpowers/sdd/task-4-report.md` | 71/0 |
| A | `.superpowers/sdd/task-5-report.md` | 41/0 |

### Repository Guidance (1)

| Status | File | Added/deleted |
|---|---|---:|
| M | `CLAUDE.md` | 4/0 |

### Operations, Plan, And Design Documents (4)

| Status | File | Added/deleted |
|---|---|---:|
| M | `docs/operations/recovery-drills.md` | 54/0 |
| A | `docs/operations/runtime-observations.md` | 42/0 |
| A | `docs/superpowers/plans/2026-07-12-production-drills.md` | 167/0 |
| A | `docs/superpowers/specs/2026-07-12-production-drills-design.md` | 118/0 |

### Migrations (3)

| Status | File | Added/deleted |
|---|---|---:|
| A | `migrations/versions/0015_production_drill_runs.py` | 77/0 |
| A | `migrations/versions/0016_production_drill_resources.py` | 56/0 |
| A | `migrations/versions/0017_runtime_observations.py` | 111/0 |

### Production Runner (2)

| Status | File | Added/deleted |
|---|---|---:|
| A | `scripts/production_drill_common.sh` | 335/0 |
| A | `scripts/run-production-drills.sh` | 7/0 |

### Production Application (23)

| Status | File | Added/deleted |
|---|---|---:|
| M | `src/orchestrator/api/dependencies.py` | 40/1 |
| M | `src/orchestrator/api/routes.py` | 172/1 |
| M | `src/orchestrator/api/schemas.py` | 134/0 |
| M | `src/orchestrator/config.py` | 1/0 |
| M | `src/orchestrator/kernel/leases.py` | 1/0 |
| M | `src/orchestrator/main.py` | 14/0 |
| M | `src/orchestrator/persistence/models.py` | 82/0 |
| M | `src/orchestrator/services/claims.py` | 9/6 |
| M | `src/orchestrator/services/dead_letter.py` | 88/24 |
| M | `src/orchestrator/services/deployment_observations.py` | 50/6 |
| M | `src/orchestrator/services/evidence.py` | 127/17 |
| M | `src/orchestrator/services/in_flight.py` | 26/9 |
| M | `src/orchestrator/services/lifecycle.py` | 82/1 |
| M | `src/orchestrator/services/observations.py` | 38/6 |
| M | `src/orchestrator/services/packages.py` | 160/4 |
| M | `src/orchestrator/services/pr_bindings.py` | 2/1 |
| A | `src/orchestrator/services/production_drill_resources.py` | 201/0 |
| A | `src/orchestrator/services/production_drills.py` | 1630/0 |
| M | `src/orchestrator/services/reconciliation.py` | 60/1 |
| M | `src/orchestrator/services/reconciliation_detection.py` | 46/1 |
| M | `src/orchestrator/services/release_artifacts.py` | 44/6 |
| A | `src/orchestrator/services/runtime_observations.py` | 198/0 |
| M | `src/orchestrator/web.py` | 8/1 |

### Tests And Fixtures (22)

| Status | File | Added/deleted |
|---|---|---:|
| M | `tests/api/conftest.py` | 26/0 |
| M | `tests/api/test_lifecycle_api.py` | 8/0 |
| A | `tests/api/test_production_drill_closeout_api.py` | 65/0 |
| A | `tests/api/test_production_drill_controls_api.py` | 73/0 |
| A | `tests/api/test_production_drill_scenarios_api.py` | 297/0 |
| A | `tests/api/test_production_drills_api.py` | 197/0 |
| M | `tests/architecture/test_container.py` | 18/1 |
| M | `tests/architecture/test_drill_scripts.py` | 6/0 |
| A | `tests/architecture/test_production_drill_runner.py` | 342/0 |
| M | `tests/architecture/test_scope_guards.py` | 7/0 |
| M | `tests/architecture/test_ws32_scope_guards.py` | 78/1 |
| M | `tests/architecture/test_ws34_scope_guards.py` | 11/0 |
| M | `tests/fixtures/registry-bundle.json` | 7/0 |
| M | `tests/idempotency/matrix.py` | 30/0 |
| M | `tests/persistence/test_migrations.py` | 53/0 |
| M | `tests/services/test_package_registration.py` | 11/2 |
| A | `tests/services/test_production_drill_closeout.py` | 247/0 |
| A | `tests/services/test_production_drill_controls.py` | 213/0 |
| A | `tests/services/test_production_drill_resources.py` | 620/0 |
| A | `tests/services/test_production_drill_scenarios.py` | 605/0 |
| A | `tests/services/test_production_drills.py` | 265/0 |
| A | `tests/services/test_runtime_observations.py` | 95/0 |

Inventory subtotal: 4 generated reports + 1 repository-guidance path + 4 operations/design
documents + 3 migrations + 2 runner paths + 23 production paths + 22 test/fixture paths =
**59 paths**.

## Production, Migration, And Runner Trace

| File | Lines added/deleted | Requirements | Classification | Production entry point | Required by later file |
|---|---:|---|---|---|---|
| migrations/versions/0015_production_drill_runs.py | 77/0 | R1, R5, R8 | required | Alembic `upgrade()` creates immutable run authorization/closeout storage. | `0016_production_drill_resources.py`; production-drill models and service |
| migrations/versions/0016_production_drill_resources.py | 56/0 | R5, R8 | required | Alembic `upgrade()` creates durable single-run resource ownership. | `0017_runtime_observations.py`; resource registry and scenario services |
| migrations/versions/0017_runtime_observations.py | 111/0 | R2, R9 | required | Alembic `upgrade()` creates append-only runtime observations and an additive nullable historical run link. | runtime-observation service; production-drill start service |
| scripts/production_drill_common.sh | 335/0 | R4, R6, R7, R8 | required | Invoked through `run-production-drills.sh`; performs fixed-target HTTP preflight and the two runner phases. | `scripts/run-production-drills.sh`; runner architecture tests; recovery runbook |
| scripts/run-production-drills.sh | 7/0 | R4, R6, R7 | required | Operator-facing production runner executable; delegates to the fixed shared runner. | production-drill operations procedure and architecture tests |
| src/orchestrator/api/dependencies.py | 40/1 | R3 | required | FastAPI dependencies authorize the exact drill and observer credential-key IDs. | production-drill and runtime-observation routes |
| src/orchestrator/api/routes.py | 172/1 | R1, R2, R3, R4, R7, R8 | required | Public `/runtime-observations` and `/production-drills` HTTP routes. | fixed runner; browser HUMAN flow; OpenAPI contract |
| src/orchestrator/api/schemas.py | 134/0 | R1, R2, R4, R6, R8 | required | Pydantic request/response models forbid caller-selected scenario inputs and expose auditable state. | production-drill routes and generated OpenAPI |
| src/orchestrator/config.py | 1/0 | none | defensive | Settings cap drill-specific deadlines but do not directly implement R1-R10. | production-drill deadline validation |
| src/orchestrator/kernel/leases.py | 1/0 | none | defensive | Kernel constant supplies a lower deadline bound but does not directly implement R1-R10. | production-drill deadline validation and lease control |
| src/orchestrator/main.py | 14/0 | R3 | required | Application startup requires two distinct configured SYSTEM credential-key IDs and fails closed on invalid configuration. | API authorization dependencies |
| src/orchestrator/persistence/models.py | 82/0 | R1, R2, R5, R8 | required | ORM maps retained runs, resource ownership, and immutable runtime observations. | all production-drill services and projections |
| src/orchestrator/services/claims.py | 9/6 | R4, R7 | required | Fixed scenarios claim, renew, and reclaim run-owned work with run-scoped lease duration. | crash- and evidence-recovery scenario orchestration |
| src/orchestrator/services/dead_letter.py | 88/24 | R4, R5, R8 | required | Ordinary dead-letter reads exclude synthetic work; run-scoped reads use the bounded reporting deadline. | stalled-approval scenario and ordinary operator projection |
| src/orchestrator/services/deployment_observations.py | 50/6 | R4, R5, R8 | required | Deploy-split-brain scenario records a run-owned deployment observation, evidence, and post-deploy unit atomically. | production-drill scenario service and closeout invariant |
| src/orchestrator/services/evidence.py | 127/17 | R4, R5, R8 | required | Evidence-recovery scenario writes and supersedes only run-owned retained evidence. | production-drill evidence scenario and run-state projection |
| src/orchestrator/services/in_flight.py | 26/9 | R5 | required | Ordinary in-flight snapshots exclude run-owned units and release bindings by default. | operator in-flight API projection |
| src/orchestrator/services/lifecycle.py | 82/1 | R3, R4, R5, R8 | required | Scenario and HUMAN closeout wrappers restrict lifecycle changes to run-owned units; actor context carries credential identity. | fixed scenarios, closeout, and credential authorization |
| src/orchestrator/services/observations.py | 38/6 | R4, R5 | required | External-conflict scenario records observations only through a run-owned writer. | production-drill scenario service and run-state projection |
| src/orchestrator/services/packages.py | 160/4 | R1, R4, R5 | required | Internal fixed-template registration binds namespaced units to the authorized revision and HUMAN delegation. | production-drill fixed-scenario service |
| src/orchestrator/services/pr_bindings.py | 2/1 | none | defensive | Suppresses the existing writer's commit inside a scenario transaction; this is transaction support, not a direct R1-R10 implementation. | production-drill external-conflict scenario |
| src/orchestrator/services/production_drill_resources.py | 201/0 | R4, R5, R8 | required | Drill writers register concrete created resources and reject cross-run or ordinary control. | scenario writers, projections, state view, and closeout |
| src/orchestrator/services/production_drills.py | 1630/0 | R1, R2, R3, R4, R5, R7, R8 | required | Core run start/state/scenario/fail/close functions called by public routes. | API routes; runner; claims/dead-letter/reconciliation deadline adapters |
| src/orchestrator/services/reconciliation.py | 60/1 | R4, R5, R8 | required | Fixed scenarios create and HUMAN closeout resolves only run-owned conditions. | external-conflict and deploy-split-brain scenarios; closeout |
| src/orchestrator/services/reconciliation_detection.py | 46/1 | R4, R5 | required | Run-scoped detection filters to owned facts and uses the bounded reporting deadline. | deploy-split-brain scenario |
| src/orchestrator/services/release_artifacts.py | 44/6 | R4, R5 | required | Deploy-split-brain scenario records a run-owned release binding and generated evidence. | deployment-observation writer and production-drill scenario |
| src/orchestrator/services/runtime_observations.py | 198/0 | R2, R3 | required | Dedicated observer route records fixed-target, bounded, immutable external runtime facts. | HUMAN production-drill start validation |
| src/orchestrator/web.py | 8/1 | R5 | required | Browser operator queue excludes synthetic work by default. | ordinary HUMAN operator projection |

Trace subtotal: **28 rows**, matching all 3 migration + 2 runner + 23 production application
paths. Each path appears once and has one classification.

## Requirement Coverage Observations

- R1-R8 each trace to at least one production, migration, or runner path. R2 and R3 are only
  partial implementation traces, and path coverage is not contract satisfaction; the gaps below
  remain.
- R9 traces only to migration `0017`'s nullable runtime-observation link for immutable historical
  runs. It does not trace to a complete deployment, partial-migration, or rollback-safe path.
- R10 traces to no production, migration, or runner path in PR #52. The design document explicitly
  assigns the executable exit-criteria guard to separate prospective Phase 0 work; no executable
  guard appears in this diff. PR merge state, passing checks, and route presence therefore remain
  insufficient to mark program criteria MET.
- Three traced production paths have `none` mappings and are classified `defensive`. No path is
  classified `accidental` or `unrelated` under the method above.

### Contract Gaps Exposed By The Trace

- **R2 is not end-to-end available.** `docs/operations/runtime-observations.md` states that the
  constrained read-only observer capability does not yet exist. PR #52 supplies an immutable
  ingestion and binding surface whose API accepts container, image, and OpenAPI digest strings,
  but no repository path obtains the external runtime identity or hashes raw live OpenAPI bytes.
  Production evidence cannot satisfy R2 until that bounded prerequisite exists and is exercised.
- **R3 is only endpoint-local, not credential-wide least privilege.** Startup proves the observer
  and drill credential-key IDs are distinct SYSTEM identities, and the two dedicated route
  dependencies enforce their specific keys. Generic authentication still returns either identity
  as an ordinary SYSTEM actor on other SYSTEM-authorized surfaces, so the diff does not prove that
  either credential is globally least-privileged.
- **R8 does not require all five fixed assertions before HUMAN closeout.**
  `_close_production_drill()` terminalizes whatever registered resources exist and
  `_assert_closeout_invariant()` checks only active claims, nonterminal owned units, and unresolved
  owned conditions. It does not require one successful terminal event for each of the five fixed
  scenarios. `test_close_ignores_ordinary_unit_and_emits_explicit_audit_event` demonstrates that a
  newly opened run with no drill-owned resources can close successfully; the parameterized
  `test_every_successful_scenario_remains_human_closeable` demonstrates that any single scenario is
  sufficient for closeout. The runner's local assertion JSON is not retained as server-side
  assertion completeness.
- **R9 lacks an absent-configuration and partial-rollout path.** `load_auth_config()` requires both
  new credential-key environment variables whenever runtime authentication is enabled and raises
  during startup when either is absent, invalid, non-SYSTEM, or identical. PR #52 adds no feature
  gate or compatibility route for a deployment whose configuration or migrations have not landed
  together. The migration downgrades also remove retained drill tables or links. No deployment or
  rollback artifact proves that ordering safe.
- **R10 has no executable implementation in PR #52.** No retained evidence-to-scorecard guard is
  present in the 28 traced production, migration, and runner paths.

## Aggregate Complexity And Boundary Smells

Exact current line counts from the required `wc -l` command:

| File | Lines |
|---|---:|
| `src/orchestrator/services/production_drills.py` | 1630 |
| `src/orchestrator/services/production_drill_resources.py` | 201 |
| `src/orchestrator/services/runtime_observations.py` | 198 |
| `src/orchestrator/api/routes.py` | 1742 |
| `src/orchestrator/api/schemas.py` | 1107 |
| **Total** | **4878** |

`src/orchestrator/services/production_drills.py` contains 49 module-level functions and 5
module-level classes. Twelve function spans exceed 50 lines when measured to the next module-level
declaration: `production_drill_state`, `_start_production_drill`,
`_run_production_drill_scenario`, `_execute_fixed_crash_recovery`,
`_execute_fixed_external_pr_conflict`, `_execute_fixed_stalled_approval`,
`_execute_fixed_evidence_recovery`, `_execute_fixed_deploy_split_brain`,
`_complete_fixed_unit`, `_fail_production_drill`, `_close_production_drill`, and
`_assert_closeout_invariant`. The 1630-line service combines authorization, provenance, five
scenario implementations, timing, state projection, failure, and closeout, which is a material
boundary and reviewability smell even though each path is requirements-related. The 335-line
runner helper is also above the portfolio's approximate 300-line file smell threshold.

The PR #52 diff adds eight suppression comments, all in
`tests/architecture/test_production_drill_runner.py`. Every suppression is `# noqa: E501` and each
has an inline justification that the shell mock JSON or payload boundary must remain one line.
No added `# type: ignore` or `eslint-disable` suppression was found. Therefore the suppression
scan produces no blocking finding.

## Trace Conclusions

### Required

- `migrations/versions/0015_production_drill_runs.py`
- `migrations/versions/0016_production_drill_resources.py`
- `migrations/versions/0017_runtime_observations.py`
- `scripts/production_drill_common.sh`
- `scripts/run-production-drills.sh`
- `src/orchestrator/api/dependencies.py`
- `src/orchestrator/api/routes.py`
- `src/orchestrator/api/schemas.py`
- `src/orchestrator/main.py`
- `src/orchestrator/persistence/models.py`
- `src/orchestrator/services/claims.py`
- `src/orchestrator/services/dead_letter.py`
- `src/orchestrator/services/deployment_observations.py`
- `src/orchestrator/services/evidence.py`
- `src/orchestrator/services/in_flight.py`
- `src/orchestrator/services/lifecycle.py`
- `src/orchestrator/services/observations.py`
- `src/orchestrator/services/packages.py`
- `src/orchestrator/services/production_drill_resources.py`
- `src/orchestrator/services/production_drills.py`
- `src/orchestrator/services/reconciliation.py`
- `src/orchestrator/services/reconciliation_detection.py`
- `src/orchestrator/services/release_artifacts.py`
- `src/orchestrator/services/runtime_observations.py`
- `src/orchestrator/web.py`

### Defensive

- `src/orchestrator/config.py`
- `src/orchestrator/kernel/leases.py`
- `src/orchestrator/services/pr_bindings.py`

### Accidental

None identified.

### Unrelated

None identified.
