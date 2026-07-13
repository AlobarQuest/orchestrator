# PR #52 Adversarial Review

**Captured:** 2026-07-13, America/New_York

**PR #52 diff:** `1f0a236^1..1f0a236`

This review combines two independent, read-only adversarial reviews. Each reviewer first read the
stabilization status, requirements trace, and mechanical review. The synthesis then verified every
cited path, symbol, and behavioral premise against the merged repository. It did not access or
mutate production, credentials, BWS, Coolify, or external infrastructure. Priorities are preserved
from the independent reports; this document does not select or recommend a keep, narrow, or revert
disposition.

## Reviewer A — Halt And Rollback

Reviewer A inspected all 59 paths in the PR #52 diff, reported a clean read-only worktree, and made
no disposition recommendation.

### P0

No findings.

### P1

#### A-P1-1 — Authenticated deployments require two new credentials and role mappings atomically

`load_auth_config()` enters authenticated mode when the registry bundle is configured, requires
both new credential-key environment variables, requires both keys to map to distinct SYSTEM
identities, and is called during module import
(`src/orchestrator/main.py:58-61`, `src/orchestrator/main.py:75-86`,
`src/orchestrator/main.py:183`).

- **Triggering sequence:** deploy the PR #52 image before both variables, credential entries, and
  role mappings have landed.
- **Observable failure:** importing the application raises `RuntimeError("invalid runtime
  authentication configuration")`; application startup and readiness fail. The reviewer
  reproduced this with a synthetic import using the pre-PR authenticated environment and an
  omitted new key ID; the process exited 1.
- **Smallest proving test:** import the application with an otherwise valid pre-PR authenticated
  environment while omitting either new credential-key ID.

#### A-P1-2 — Code deployed before migration 0016 can break ordinary claims and projections

Ordinary claim and renewal paths call `lease_duration_for_work_unit()`
(`src/orchestrator/services/claims.py:85`, `src/orchestrator/services/claims.py:152`,
`src/orchestrator/services/claims.py:677`). That helper unconditionally queries
`ProductionDrillResource` (`src/orchestrator/services/production_drills.py:258-264`), whose table is
created only by migration 0016 (`migrations/versions/0016_production_drill_resources.py:26-48`).
Ordinary lifecycle transition, web queue, and in-flight paths independently query the same table
(`src/orchestrator/services/lifecycle.py:108`, `src/orchestrator/web.py:164-169`,
`src/orchestrator/services/in_flight.py:88-98`, `src/orchestrator/services/in_flight.py:117-138`).

- **Triggering sequence:** run PR #52 code against a schema at 0015 or earlier.
- **Observable failure:** PostgreSQL raises `UndefinedTable` during ordinary claim, lifecycle,
  queue, or in-flight operations if traffic reaches the application. The readiness endpoint does
  compare the database revision with the code head and returns 503 for the 0015/0017 mismatch
  (`src/orchestrator/api/health.py:27-52`); the review therefore rejects the narrower premise that
  readiness can remain green in this exact revision-mismatch sequence.
- **Smallest proving test:** migrate a disposable database to 0015, create an ordinary READY unit,
  assert readiness returns 503, then directly exercise claim, review queue, and in-flight reads with
  PR #52 code and assert each query of the absent table fails.

#### A-P1-3 — Downgrade removes synthetic ownership markers but leaves ordinary-domain rows

Fixed templates create namespaced ordinary `WorkUnit` rows and then bind them as drill resources
(`src/orchestrator/services/packages.py:437-456`, `src/orchestrator/services/packages.py:501-518`).
Crash-recovery attempt one leaves such a unit CLAIMED
(`src/orchestrator/services/production_drills.py:559-585`). Migration 0016 downgrade drops the
ownership table (`migrations/versions/0016_production_drill_resources.py:54-56`) and migration 0015
downgrade drops the run table (`migrations/versions/0015_production_drill_runs.py:71-77`), but neither
removes the synthetic `WorkUnit` or its `Claim`. Before PR #52, `_in_flight_units()` returned every
active work unit
(`1f0a236^1:src/orchestrator/services/in_flight.py:84-106`).

- **Triggering sequence:** persist crash-recovery attempt one, then downgrade to 0014 and run the
  pre-PR application.
- **Observable failure:** the synthetic work unit and claim survive without their ownership marker,
  and the pre-PR in-flight projection exposes the synthetic unit as ordinary active work.
- **Smallest proving test:** persist attempt one, downgrade to 0014, prove the work unit and claim
  remain while their ownership marker is gone, then prove the pre-PR in-flight query returns the
  synthetic unit.

#### A-P1-4 — Drill release and deployment facts enter the ordinary factory-event export

The deploy-split-brain scenario creates synthetic release and deployment facts with invalid test
targets (`src/orchestrator/services/production_drills.py:941-999`). Their normal writers create
`release_artifact.bound`, `deployment.observed`, and post-deploy events
(`src/orchestrator/services/release_artifacts.py:171-188`,
`src/orchestrator/services/deployment_observations.py:198-243`). The ordinary publication queue
enumerates every event, maps those actions, and exports every mapped pending, failed, or previously
exported publication without a drill-resource exclusion
(`src/orchestrator/services/event_publications.py:266-309`,
`src/orchestrator/services/event_publications.py:354-364`).

- **Triggering sequence:** execute deploy-split-brain, then run the ordinary publication queue and
  export.
- **Observable failure:** synthetic `example.invalid` and `production-drill.invalid` lineage can be
  emitted to downstream factory-event consumers as ordinary release and deployment facts.
- **Smallest proving test:** run the scenario, queue and export publications, and assert its release
  and deployment event IDs and JSONL payloads are absent. The current code supplies no exclusion,
  so that assertion should fail for a registry-authorized drill actor.

### P2

#### A-P2-1 — Mandatory provenance depends on infrastructure that the repository says is absent

Start requires an existing, fresh runtime observation
(`src/orchestrator/services/production_drills.py:401-430`), while the runbook says the constrained
read-only observer needed to collect that observation does not yet exist and describes it as a
future prerequisite (`docs/operations/runtime-observations.md:7-40`).

- **Triggering sequence:** attempt the compliant production-drill procedure with the currently
  documented capabilities.
- **Observable failure:** there is no approved observation producer; attempting start without a
  retained observation returns `runtime_observation_not_found`.
- **Smallest proving test:** inventory executable observer entry points and attempt start without a
  runtime observation.

## Reviewer B — Predicate And Delivery

Reviewer B made no disposition recommendation.

### P0

No findings.

### P1

#### B-P1-1 — The required browser-HUMAN start and close path is unreachable at the documented production boundary

The repository records that production `/api` is M2M-only and strips the human proxy headers
needed by `get_actor()` (`CLAUDE.md:85-90`). Runtime authentication rejects HUMAN M2M roles
(`src/orchestrator/main.py:71-74`). Start and close exist only as `/api` routes using the general
actor dependency (`src/orchestrator/api/routes.py:316-339`,
`src/orchestrator/api/routes.py:362-380`), and there is no `/review` start or close flow.

- **False-success mode:** TestClient supplies HUMAN proxy headers directly and proves an
  application-local path that the documented production proxy removes.
- **Smallest counterexample:** make the same browser POST through the documented production proxy;
  `_require_human` cannot receive a HUMAN actor.

#### B-P1-2 — Runtime provenance is caller-attested and vulnerable to deployment TOCTOU

The runtime-observation endpoint accepts caller-provided container, image, digest, OpenAPI hash,
and timestamp fields; service validation checks formats and timestamps, not the external facts
(`src/orchestrator/api/routes.py:294-312`,
`src/orchestrator/services/runtime_observations.py:129-155`). The constrained producer is explicitly
absent (`docs/operations/runtime-observations.md:7-40`). Runner preflight verifies only that OpenAPI
operations exist (`scripts/production_drill_common.sh:84-100`), and the run-scoped state projection
does not return the bound image or OpenAPI digests
(`src/orchestrator/services/production_drills.py:273-345`).

- **False-success mode:** an observation of deployment A is shape-valid and fresh, deployment B
  replaces A, and the drill against B succeeds while retaining A's digests.
- **Smallest counterexample:** observe A with digest X, start a run, deploy B with the same OpenAPI
  operation shapes, then complete the drill without any step re-hashing or binding B.

#### B-P1-3 — Crash recovery proves lease expiry and reclaim, not a restart

Resume checks for the prepared active claim and then calls phase two
(`scripts/production_drill_common.sh:317-322`). The service waits for lease expiry and reclaims it
without consuming a runtime identity or restart record
(`src/orchestrator/services/production_drills.py:587-610`,
`src/orchestrator/services/production_drills.py:1145-1149`). The service test advances a mocked
clock and replaces sleep (`tests/services/test_production_drill_scenarios.py:51-63`,
`tests/services/test_production_drill_scenarios.py:94-115`).

- **False-success mode:** a two-invocation lease-expiry test is reported as proof of restart
  recovery.
- **Smallest counterexample:** prepare attempt one, wait 60 seconds without restarting anything,
  then resume; the assertion passes.

#### B-P1-4 — HUMAN close accepts zero or incomplete scenarios and manufactures terminality

Close cancels or releases whatever run-owned work exists before checking for remaining active
claims, nonterminal units, or unresolved conditions
(`src/orchestrator/services/production_drills.py:1284-1404`). It never requires one successful
terminal assertion for each fixed scenario. Tests explicitly prove both incomplete-unit
cancellation and successful close of a run with no drill resources
(`tests/services/test_production_drill_closeout.py:64-88`,
`tests/services/test_production_drill_closeout.py:91-104`).

- **False-success mode:** server-side close is treated as evidence that all five assertions
  completed.
- **Smallest counterexample:** start a run and immediately close it; it becomes `closed` without any
  scenario success.

#### B-P1-5 — Synthetic isolation omits ordinary status and direct HUMAN projections

The status ledger starts from an unfiltered `select(WorkUnit)`
(`src/orchestrator/services/status_ledger.py:83-96`). The web queue excludes drill work, but the
direct unit projection, detail route, and evidence-pack route accept a synthetic UUID without an
ownership exclusion (`src/orchestrator/web.py:160-205`, `src/orchestrator/web.py:415-447`,
`src/orchestrator/web.py:474-479`). Existing isolation coverage concentrates on dead-letter and
in-flight behavior.

- **False-success mode:** passing dead-letter, in-flight, and queue checks is generalized to all
  ordinary operator projections.
- **Smallest counterexample:** request the status ledger, `/review/units/{synthetic_id}`, or the
  synthetic unit's evidence pack and observe the drill record.

#### B-P1-6 — Credential separation is endpoint-local, not credential-wide least privilege

`get_actor()` maps either configured M2M key to an ordinary SYSTEM actor; only the two new endpoint
dependencies compare the exact credential-key ID
(`src/orchestrator/api/dependencies.py:55-87`,
`src/orchestrator/api/dependencies.py:90-123`).

- **False-success mode:** distinct key IDs and dedicated endpoint tests are reported as global
  least privilege.
- **Smallest counterexample:** use the runtime-observer credential against an existing generic
  SYSTEM-authorized route; its SYSTEM role is accepted unless that route adds a separate key check.

#### B-P1-7 — Partial rollout and retention-safe rollback are not provided

Both new credential IDs are mandatory in authenticated mode (`src/orchestrator/main.py:75-86`).
Migration 0017 downgrade deletes the runtime-observation link and table
(`migrations/versions/0017_runtime_observations.py:89-111`); earlier downgrades delete resource and
run tables. The mechanical review proved only an empty-fixture migration cycle and explicitly did
not prove retention-safe live rollback
(`docs/superpowers/evidence/2026-07-13-pr52-mechanical-review.md:85-88`,
`docs/superpowers/evidence/2026-07-13-pr52-mechanical-review.md:199-204`).

- **False-success mode:** an empty-database downgrade/re-upgrade is reported as safe production
  deployment and rollback.
- **Smallest counterexample:** omit one credential during deploy, or retain an observation and
  drill record before downgrading.

#### B-P1-8 — No executable R10 guard connects program claims to live evidence

The requirements trace finds no R10 production, migration, or runner path
(`docs/superpowers/evidence/2026-07-13-pr52-requirements-trace.md:188-194`,
`docs/superpowers/evidence/2026-07-13-pr52-requirements-trace.md:222-224`), and Remediation Phase 0.5
remains open (`docs/superpowers/evidence/2026-07-13-phase2-stabilization-status.md:42`).

- **False-success mode:** green CI, merge state, or declared routes are allowed to mark a program
  criterion MET without retained production drill evidence.
- **Smallest counterexample:** mark criterion #5 MET while PR #52 remains absent from production
  and no production drill evidence exists; no executable repository guard rejects it.

### P2

#### B-P2-1 — Deadline boundedness is configuration-relative, with no independent hard maximum

The setting has a lower constraint but no hard upper constraint
(`src/orchestrator/config.py:56`). `_require_deadlines()` accepts any command value up to that
setting, and scenario execution sleeps until the stored deadline
(`src/orchestrator/services/production_drills.py:1145-1161`,
`src/orchestrator/services/production_drills.py:1598-1612`). The control test replaces the configured
maximum with 60 seconds (`tests/services/test_production_drill_controls.py:70-85`).

- **False-success mode:** a configurable maximum is treated as proof of an externally bounded
  production wait.
- **Smallest counterexample:** configure the maximum to ten years and submit a matching deadline;
  validation accepts it and the synchronous scenario can sleep for that duration.

#### B-P2-2 — No test composes the real runner, HTTP adapter, service, and timing boundaries

The runner architecture test replaces BWS and curl with local executables
(`tests/architecture/test_production_drill_runner.py:119-145`). The scenario route test replaces the
fixed scenario implementation with a no-op
(`tests/api/test_production_drill_scenarios_api.py:42-50`). The service timing tests replace clocks
and sleep (`tests/services/test_production_drill_scenarios.py:51-63`).

- **False-success mode:** individually passing shell, route, and service tests are treated as proof
  that their composed delivery path survives production request and timing behavior.
- **Smallest counterexample:** impose an intermediary timeout shorter than the 60-second synchronous
  scenario request; no current composed test proves recovery or correct reporting.

### Production-file necessity audit

This audit covers all **23** PR #52 production application paths from the Task 2 trace. “Necessary”
means the changed behavior directly supplies part of R1-R10 and cannot simply be omitted from this
implementation while preserving that requirement. It does not mean the implementation satisfies
the requirement. “Defensive support” means the change supports this implementation but is not
itself required by R1-R10; a different local structure could omit it. This judgment is based on
predicate contribution, not mere import or call-graph reachability.

| # | Production path | R1-R10 necessity judgment under Reviewer B's lens | Task 2 comparison |
|---:|---|---|---|
| 1 | `src/orchestrator/api/dependencies.py` | **Necessary but insufficient (R3).** Exact-key endpoint checks are necessary to distinguish the runner and observer at their dedicated writes, but they do not constrain either SYSTEM key on the rest of the API (B-P1-6). | Agrees with `required`; qualifies Task 2's R3 mapping as endpoint-local. |
| 2 | `src/orchestrator/api/routes.py` | **Necessary but currently undeliverable (R1, R2, R3, R4, R7, R8).** Public observation, run, state, scenario, fail, and close interfaces are needed, but the documented proxy makes the HUMAN start/close predicates unreachable (B-P1-1). | Agrees with `required`; adds that route existence does not satisfy R1. |
| 3 | `src/orchestrator/api/schemas.py` | **Necessary (R1, R2, R4, R6, R8).** Fixed command and response shapes prevent caller-selected scenario/target/executor inputs and expose bounded state, though schema validation cannot prove external provenance. | Agrees with `required`; no classification disagreement. |
| 4 | `src/orchestrator/config.py` | **Not independently necessary; defensive support only.** It supplies a configurable deadline maximum, but R1-R10 do not require this setting and its lack of a hard upper bound leaves the boundedness predicate false (B-P2-1). | Agrees with Task 2's `defensive`/`none`; no disagreement. |
| 5 | `src/orchestrator/kernel/leases.py` | **Not independently necessary; defensive support only.** The added minimum-deadline constant supports validation but is not itself an R1-R10 contract surface and does not cure B-P2-1. | Agrees with Task 2's `defensive`/`none`; no disagreement. |
| 6 | `src/orchestrator/main.py` | **Necessary but rollout-unsafe (R3, R9).** Loading distinct SYSTEM IDs is part of authority separation, but making both mandatory at import violates the partial-rollout predicate (A-P1-1/B-P1-7). | Agrees with `required`; Task 2 mapped only R3, while this review also treats its rollout behavior as directly relevant negative evidence for R9. |
| 7 | `src/orchestrator/persistence/models.py` | **Necessary (R1, R2, R5, R8).** Durable run, provenance, observation, and resource-ownership models are required for retained authorization and audit; their existence does not make downgrade retention-safe. | Agrees with `required`; no classification disagreement. |
| 8 | `src/orchestrator/services/claims.py` | **Necessary (R4, R7).** Run-scoped lease duration and reclaim integration are required for the fixed crash scenario, although the scenario proves expiry/reclaim rather than restart (B-P1-3). | Agrees with `required`; qualifies the external predicate. |
| 9 | `src/orchestrator/services/dead_letter.py` | **Necessary (R4, R5, R8).** Run-scoped reads and ordinary dead-letter exclusion are direct synthetic-isolation behavior; they do not prove isolation in other projections (B-P1-5). | Agrees with `required`; narrows any inference beyond dead-letter. |
| 10 | `src/orchestrator/services/deployment_observations.py` | **Necessary but leaks delivery facts (R4, R5, R8).** Atomic creation of the fixed deployment observation and generated unit is part of deploy-split-brain, but its ordinary event writer feeds A-P1-4. | Agrees with `required`; adds an R5 delivery failure. |
| 11 | `src/orchestrator/services/evidence.py` | **Necessary (R4, R5, R8).** Fixed evidence recovery must create and supersede run-owned retained evidence; direct review projections still expose it by UUID (B-P1-5). | Agrees with `required`; qualifies R5 completeness. |
| 12 | `src/orchestrator/services/in_flight.py` | **Necessary (R5).** Default exclusion of run-owned work and release bindings is a direct ordinary-projection guard, and its dependency on migration 0016 creates A-P1-2 rollout coupling. | Agrees with `required`; no classification disagreement. |
| 13 | `src/orchestrator/services/lifecycle.py` | **Necessary (R3, R4, R5, R8).** Separate drill writers and ordinary-writer rejection enforce ownership and actor boundaries; close can nevertheless manufacture terminality (B-P1-4). | Agrees with `required`; qualifies R8 satisfaction. |
| 14 | `src/orchestrator/services/observations.py` | **Necessary (R4, R5).** The external-conflict scenario needs a run-owned observation writer; ownership does not prevent ordinary event/publication or direct-projection leakage elsewhere. | Agrees with `required`; no classification disagreement. |
| 15 | `src/orchestrator/services/packages.py` | **Necessary (R1, R4, R5).** Fixed namespaced template registration and HUMAN delegation bind scenario work to the approved revision; durable ordinary-domain rows make marker-stripping rollback unsafe (A-P1-3). | Agrees with `required`; adds rollback consequence. |
| 16 | `src/orchestrator/services/pr_bindings.py` | **Not independently necessary; defensive transaction support.** Commit suppression lets the external-conflict scenario share one transaction, but R1-R10 require atomic results rather than this specific session-info mechanism. | Agrees with Task 2's `defensive`/`none`; no disagreement. |
| 17 | `src/orchestrator/services/production_drill_resources.py` | **Necessary but incomplete (R4, R5, R8).** Durable ownership and ordinary-writer rejection are the core synthetic namespace, but not every ordinary projection or export consults it (A-P1-4/B-P1-5). | Agrees with `required`; qualifies R5/R8 completeness. |
| 18 | `src/orchestrator/services/production_drills.py` | **Necessary but fails several predicates (R1, R2, R3, R4, R5, R7, R8).** It owns the run and five scenarios, yet accepts stale-deployment provenance, does not prove restart, and permits empty closeout (B-P1-2/B-P1-3/B-P1-4). | Agrees with `required`; necessity is not acceptance of its combined 1,630-line boundary or contract sufficiency. |
| 19 | `src/orchestrator/services/reconciliation.py` | **Necessary (R4, R5, R8).** Run-owned condition creation/resolution is required for fixed conflict scenarios and closeout; closeout still need not prove every scenario ran. | Agrees with `required`; qualifies R8 satisfaction. |
| 20 | `src/orchestrator/services/reconciliation_detection.py` | **Necessary (R4, R5).** Run-scoped detection is required to exercise the real deploy-split-brain predicate without ordinary facts; its synchronous deadline inherits B-P2-1/B-P2-2. | Agrees with `required`; no classification disagreement. |
| 21 | `src/orchestrator/services/release_artifacts.py` | **Necessary but leaks delivery facts (R4, R5).** A run-owned release binding is part of deploy-split-brain, but its normal event enters the unfiltered publication path (A-P1-4). | Agrees with `required`; adds an R5 delivery failure. |
| 22 | `src/orchestrator/services/runtime_observations.py` | **Necessary but does not prove R2/R3.** Immutable ingestion is needed to bind retained facts, but it accepts shape-valid caller fields and the trusted producer is absent (A-P2-1/B-P1-2); endpoint identity is not global least privilege (B-P1-6). | Agrees with `required`; qualifies Task 2's already-partial R2/R3 mapping. |
| 23 | `src/orchestrator/web.py` | **Necessary but incomplete (R5).** Queue exclusion directly protects one ordinary HUMAN projection, while detail and evidence-pack remain accessible by synthetic UUID (B-P1-5). | Agrees with `required`; explicitly rejects treating this one exclusion as complete R5 isolation. |

Totals: **20 necessary** paths (several insufficient or harmful as implemented), **3 defensive
support paths not independently necessary**, and **0 accidental or unrelated** paths. This matches
Task 2's path-level required/defensive classifications. The disagreements are about requirement
satisfaction and scope, not the labels: Reviewer B adds R9 negative evidence to `main.py`, rejects
complete R5/R8 implications for several required paths, and rejects complete R2/R3 implications for
the observation and credential paths.

## Rejected Findings

- **Rejected qualification from A-P1-2:** the reviewer's premise that readiness may stay green with
  PR #52 code on schema 0015 is false. Readiness reads the database revision and compares it with
  the code's Alembic head (`src/orchestrator/api/health.py:27-52`), so that exact mismatch returns
  503. The underlying `UndefinedTable` consequence remains valid if traffic reaches the application,
  because the cited ordinary paths query the missing 0016 table.

No other finding was rejected. Where a premise depended on a documented production boundary rather
than a locally exercised live system, the finding says so explicitly. Where a configured maximum
exists, B-P2-1 is narrowed to the verified absence of an independent hard upper bound rather than
claiming that no validation exists.

## Reconciled Findings

The union below preserves both reviewers' priorities and scopes. Similar findings are linked but
not averaged into a different severity.

| Finding | Priority | Reviewer | Reconciliation |
|---|---|---|---|
| Auth configuration makes startup an atomic rollout dependency | P1 | A-P1-1 | Overlaps B-P1-7; A isolates startup availability. |
| Code-before-schema can break ordinary work | P1 | A-P1-2 | Unique halt lens finding. |
| Rollback strips ownership while synthetic domain rows survive | P1 | A-P1-3 | Overlaps B-P1-7; A identifies post-rollback ordinary-work contamination. |
| Synthetic release/deployment facts reach ordinary export | P1 | A-P1-4 | Unique delivery contamination path from the halt/rollback review. |
| Required observer infrastructure is absent | P2 | A-P2-1 | Overlaps B-P1-2 but retains A's P2 prerequisite ranking. |
| Browser-HUMAN start/close is unreachable at the documented proxy boundary | P1 | B-P1-1 | Unique authority-delivery finding. |
| Runtime provenance is caller-attested and has a deployment TOCTOU gap | P1 | B-P1-2 | Broader predicate failure than A-P2-1 and retains B's P1 ranking. |
| Crash recovery does not prove a restart | P1 | B-P1-3 | Unique external-predicate finding. |
| Closeout accepts zero or incomplete scenarios | P1 | B-P1-4 | Confirms and sharpens the R8 gap from the requirements trace. |
| Synthetic isolation omits status and direct HUMAN projections | P1 | B-P1-5 | Separate projection leak from A-P1-4's event export. |
| Credential least privilege is endpoint-local | P1 | B-P1-6 | Confirms the R3 gap from the requirements trace. |
| Partial rollout and retention-safe rollback are absent | P1 | B-P1-7 | Encompasses the deployment-order and destructive-downgrade predicates; A gives two concrete consequences. |
| Executable scorecard-to-live-evidence guard is absent | P1 | B-P1-8 | Confirms the open R10 and Remediation 0.5 gap. |
| Deadline maximum lacks an independent hard cap | P2 | B-P2-1 | Qualified to preserve the existing configuration-relative validation. |
| Tests do not compose runner, HTTP, service, and timing | P2 | B-P2-2 | Unique delivery-confidence gap. |

Both reviewers explicitly reported **P0: No findings**. They did not disagree on a factual premise.
Their only material priority difference is the overlap between A-P2-1 (missing observer capability
as an infrastructure prerequisite) and B-P1-2 (the resulting provenance and TOCTOU predicate
failure). Both rankings remain recorded above.
