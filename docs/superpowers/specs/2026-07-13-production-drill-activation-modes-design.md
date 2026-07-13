# Production Drill Activation Modes Design

**Date:** 2026-07-13
**Status:** approved 2026-07-13

## Goal

Make the PR #52 production-drill subsystem safe to retain while preventing its configuration,
schema, and synthetic-data dependencies from disrupting ordinary factory operation before the
subsystem is deliberately activated.

This is a repository-only compatibility change. It does not create credentials, provision an
observer, deploy code, run migrations in production, execute a drill, or claim that the remaining
R1-R10 predicates are satisfied.

## Decision

Add one explicit setting:

```text
ORCHESTRATOR_PRODUCTION_DRILL_MODE=off|standby|enabled
```

The default is `off`. A single three-state setting is preferred over separate schema-ready and
feature-enabled booleans because invalid combinations cannot be represented.

The rejected alternatives are:

1. **Two booleans:** a schema flag and an enabled flag permit contradictory combinations and make
   readiness, authorization, and rollback behavior harder to prove.
2. **Operational atomicity only:** creating credentials and relying on code, configuration, and
   migrations to land together leaves ordinary factory availability dependent on deployment
   timing and does not provide a durable rollback floor.
3. **Revert before repair:** this removes the immediate risk but discards tested ownership,
   transaction, scenario, and audit mechanics that the replacement would need again.

## State Semantics

### `off`

`off` is the legacy-compatible state and the default.

- Existing authenticated environments start without either new credential-key ID.
- No ordinary service path queries `production_drill_runs`, `production_drill_resources`, or
  `runtime_observations`.
- Every production-drill and runtime-observation route returns HTTP 503 with stable error code
  `production_drill_unavailable` before authentication-specific drill logic or database access.
- Readiness accepts only the pre-drill Alembic head `0014_wsp21_recovery_controls`.
- No synthetic isolation is attempted because the drill schema may not exist and no drill may be
  created in this state.
- `off` is not a valid rollback target after drill data has existed. The application must use
  `standby` as its rollback floor.

### `standby`

`standby` is the compatibility, drain, and forensic state.

- Readiness requires Alembic head `0017_runtime_observations`.
- Synthetic ownership filters are active across every ordinary queue, status projection, direct
  HUMAN view, evidence view, and factory-event publication/export path.
- New runtime observations, drill runs, scenario executions, and SYSTEM failure commands return
  HTTP 503 `production_drill_unavailable`.
- Authenticated GET run/state routes remain available for retained evidence and forensics.
- HUMAN close remains available so an already-open run can be deliberately terminalized during a
  drain. Existing HUMAN authorization, CSRF, closure-reason, and idempotency rules still apply.
- The two dedicated credential IDs are optional. If either is configured, both must be configured,
  distinct, present in the credential registry, and mapped to SYSTEM.

### `enabled`

`enabled` is the only state in which the complete PR #52 route set may create or mutate drill
evidence.

- Readiness requires Alembic head `0017_runtime_observations`.
- Both dedicated credential IDs are mandatory, distinct, registered, and mapped to SYSTEM.
- Runtime-observation ingestion, HUMAN start, SYSTEM scenario/failure, read, and HUMAN close routes
  retain their existing authorization and idempotency rules.
- Synthetic isolation has the same behavior as `standby`.

An invalid mode value fails settings validation. Supplying only one dedicated credential ID fails
authentication configuration in every mode; supplying neither is valid only in `off` or
`standby`.

## Configuration Boundary

Define `ProductionDrillMode` as a string enum in `src/orchestrator/config.py` and add
`Settings.production_drill_mode`, defaulting to `off`. Code compares enum values rather than raw
strings.

`AuthConfig` retains optional credential-key fields. `load_auth_config()` applies these rules:

1. Parse the mode before validating drill-specific credentials.
2. Preserve the existing no-auth development mode in `off` and `standby`, but reject a missing
   registry bundle in `enabled`.
3. In `off` and `standby`, accept both IDs absent.
4. In every mode, reject a one-ID partial pair.
5. When both IDs exist, validate distinct SYSTEM mappings.
6. In `enabled`, reject both IDs absent.

Existing registry, proxy, CSRF, and non-drill M2M validation does not change.

The application stores the parsed mode in `app.state` so route dependencies and readiness use the
same immutable process-level value. Service-layer compatibility helpers use the cached Settings
value and expose named predicates rather than repeating enum comparisons.

## Route Availability

Add two dependencies:

- `require_production_drill_schema`: permits `standby` and `enabled`.
- `require_production_drill_enabled`: permits only `enabled`.

They raise `DomainError("production_drill_unavailable", ...)`, which the application maps to HTTP
503. Availability runs before drill-specific actor authorization and before the session-backed
service call.

| Route operation | `off` | `standby` | `enabled` |
|---|---:|---:|---:|
| POST runtime observation | 503 | 503 | existing observer authorization |
| POST start run | 503 | 503 | existing HUMAN authorization |
| GET run | 503 | allowed | allowed |
| GET run state | 503 | allowed | allowed |
| POST scenario | 503 | 503 | existing drill credential authorization |
| POST fail | 503 | 503 | existing drill credential authorization |
| POST HUMAN close | 503 | allowed | allowed |

This work does not fix the separately verified production proxy gap for HUMAN start/close. It only
makes route availability explicit; the reachable browser-HUMAN flow remains a later contract task.

## Schema Compatibility And Readiness

Readiness becomes mode-aware:

- `off` is ready only at `0014_wsp21_recovery_controls`.
- `standby` and `enabled` are ready only at `0017_runtime_observations`.
- All other mode/head combinations return HTTP 503 with reason `migration_drift`.

This creates an explicit transition rather than pretending intermediate states are ready:

1. Deploy the compatibility code in `off` against 0014.
2. Apply migrations 0015-0017; readiness is allowed to remain unavailable during the bounded
   migration window.
3. Restart or reconfigure the compatibility code in `standby`; readiness returns only after head
   0017 is visible.

Ordinary services must remain functionally safe in `off` even when directly exercised against the
0014 schema. The readiness check is defense in depth, not the only protection.

## Ordinary-Path Compatibility

Centralize mode predicates in a small compatibility module. Every ordinary path introduced by PR
#52 must use them before querying drill-owned tables.

In `off`:

- claim, renew, and reclaim use the ordinary fixed lease without ownership lookup;
- lifecycle transitions do not query drill ownership;
- dead-letter, in-flight, web queue, status ledger, direct review, evidence-pack, and event
  publication behave as their pre-PR #52 ordinary implementations;
- no import or request requires migrations 0015-0017.

In `standby` and `enabled`:

- ordinary lifecycle mutation rejects drill-owned units;
- every ordinary projection excludes drill-owned resources;
- factory-event queue/retry/export classifies drill-owned facts as a stable skipped mapping with no
  `factory_event` payload;
- explicitly run-scoped internal projections may opt in, but no public ordinary route accepts a
  caller-controlled include flag.

The compatibility module must not catch `UndefinedTable` or other database exceptions. Mode is the
explicit contract; exception swallowing would hide unsafe schema combinations.

## Synthetic Event And Projection Isolation

Complete the partial R5 boundary before `standby` can be considered safe:

- exclude drill-owned work units from status-ledger results, including direct-ID filters;
- return the existing not-found behavior for direct HUMAN unit detail and evidence-pack requests
  targeting drill-owned work;
- identify events whose subject is a drill-owned work unit, evidence row, observation,
  reconciliation condition, release artifact, deployment observation, or derived post-deploy unit;
- queue those events as `skipped` with a stable reason such as `production_drill_resource` and a
  null factory-event payload;
- ensure retry cannot convert a skipped drill fact into a publishable fact;
- ensure export omits skipped drill facts while continuing to export ordinary facts.

The resource registry remains the ownership source of truth. Synthetic name prefixes and invalid
example URLs are not authorization or isolation signals.

## Retention-Safe Migration Policy

Migrations 0015-0017 remain additive in production. Their downgrade functions keep supporting the
existing empty-fixture mechanical review, but must refuse destructive downgrade when retained drill
data exists:

- 0017 refuses while any runtime observation exists or any run references one;
- 0016 refuses while any production-drill resource binding exists;
- 0015 refuses while any production-drill run exists.

The refusal must happen before dropping a column, trigger, or table and must leave all retained data
unchanged. Empty-schema downgrade to 0014 and re-upgrade to 0017 must continue to pass.

Production rollback means:

1. stop new drill mutations by switching from `enabled` to `standby`;
2. retain migrations 0015-0017 and every ownership marker;
3. roll application code back no earlier than the accepted compatibility-floor commit containing
   this mode and isolation behavior.

Rolling production back to pre-PR #52 application code after synthetic data exists is unsupported
because that code cannot recognize retained ownership markers.

## Error Handling

- Invalid mode or invalid credential pairing fails startup without logging credential material.
- Disabled routes return 503 `production_drill_unavailable`, not 401, 403, or a database error.
- Mode/schema mismatch returns readiness 503 `migration_drift`.
- Populated destructive downgrade raises an explicit migration error before mutation.
- Ordinary paths in `off` never use exception handling as schema detection.

## Test Strategy

All behavior changes follow red-green-refactor. Each regression test must be observed failing for
the intended missing behavior before production code changes.

### Configuration and startup

- default mode is `off`; all three values parse; any other value fails;
- the complete pre-PR authenticated environment starts in `off` with no new IDs;
- `standby` starts with neither ID;
- a one-ID partial pair fails in all modes;
- `enabled` fails without both IDs;
- both valid distinct SYSTEM IDs pass in all modes;
- identical or non-SYSTEM IDs fail without exposing secrets.

### Route matrix

- assert the full seven-operation availability matrix for `off`, `standby`, and `enabled`;
- prove unavailable routes return 503 before their service function or drill-specific actor
  dependency runs;
- prove standby GET and HUMAN close retain the existing authorization behavior.

### Schema and ordinary work

- run ordinary claim, renew, reclaim, lifecycle, queue, and in-flight operations on schema 0014 in
  `off` and prove no drill-table query occurs;
- verify readiness for `(off, 0014)`, `(standby, 0017)`, and `(enabled, 0017)`;
- verify every other relevant mode/head pair returns migration drift.

### Isolation

- create drill-owned work and prove it is absent from dead-letter, in-flight, queue, status ledger,
  direct HUMAN detail, and evidence-pack surfaces in `standby` and `enabled`;
- create drill release/deployment facts and prove queue and retry leave them skipped and export emits
  no synthetic JSONL;
- prove an equivalent ordinary fact still queues and exports.

### Migrations

- empty 0017-to-0014 downgrade and re-upgrade still pass;
- each populated 0015, 0016, and 0017 boundary refuses downgrade before mutation;
- after refusal, all rows, links, triggers, and current revision remain intact.

The focused suites run sequentially against one PostgreSQL fixture. Completion still requires the
full correctly configured `make check`, portfolio code-standards review, suppression scan, security
scan, and an independent whole-diff review.

## Rollout Sequence

This design defines but does not authorize the eventual infrastructure work:

1. Merge and deploy the compatibility-floor code in `off` at schema 0014.
2. Verify ordinary factory behavior and the mode-aware readiness contract.
3. Apply migrations 0015-0017 during a bounded migration window.
4. Switch to `standby` and verify every synthetic isolation surface.
5. In a separate explicitly authorized infrastructure session, provision the two credentials and
   constrained observer.
6. Complete the remaining HUMAN, provenance, restart, closeout, deadline, composed-delivery, and
   R10 contract work.
7. Switch to `enabled` only after those gates are accepted.

## Non-Goals

- No credential, BWS, Coolify, DNS, database, or production mutation.
- No production deployment or production drill.
- No claim that `enabled` is deployable merely because activation modes exist.
- No implementation of the constrained observer, browser-HUMAN start flow, restart attestation,
  five-scenario closeout predicate, credential-wide scope matrix, hard deadline redesign, composed
  delivery test, or R10 scorecard guard in this workstream.
- No broad split of the 1,630-line production-drill service unless a touched responsibility cannot
  be tested without a focused extraction.
- No commit, acceptance, or implementation of the proposed Revert ADR.

## Success Criteria

This workstream is complete only when:

- the old authenticated configuration starts in default `off` mode;
- ordinary work operates against schema 0014 without touching drill tables;
- route availability exactly matches the three-state matrix;
- mode-aware readiness rejects unsafe schema combinations;
- `standby` and `enabled` exclude synthetic resources from every identified ordinary projection and
  event export;
- populated downgrade refuses data loss and empty downgrade/re-upgrade remains mechanical;
- rollback has a tested compatibility floor and retains additive schema plus ownership markers;
- full verification and independent review pass with no unresolved Critical or Important finding;
- the proposed Revert ADR remains unaccepted and uncommitted.
