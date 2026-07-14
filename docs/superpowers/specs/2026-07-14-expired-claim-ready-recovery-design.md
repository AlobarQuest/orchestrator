# Expired-Claim Ready Recovery and Runner Failure Reporting

**Status:** approved design, pending written-spec review  
**Date:** 2026-07-14  
**Repositories:** `orchestrator`, `factory-runner`

## Problem

A real `intent-packages` factory run failed in GitHub Actions after its worker had claimed and
started the work unit. The workflow did not report failure to Orchestrator, so the unit remained
`executing` until lease expiry.

The existing `reclaim-expired-claim` operation is a worker-to-worker handoff: it releases the old
claim, records failure, returns the unit to `ready`, immediately claims the next attempt, and
returns the new lease token to the HTTP caller. That contract cannot recover a GitHub-hosted run.
The replacement GitHub workflow cannot receive the caller's lease and cannot claim a unit that is
already `claimed`.

The recovery attempt also proved that reclaim authority is intentionally one-shot: an idempotent
replay returns no lease token. Persisting or replaying lease secrets is not the fix.

## Goals

1. Give SYSTEM a production-compatible expired-claim recovery operation that ends in `ready` and
   mints no replacement claim or lease.
2. Preserve attempt accounting: recovery closes the failed attempt but does not consume the next
   attempt. The next real runner consumes it when it claims normally.
3. Have factory-runner report coding or finalization failure through the ordinary worker lifecycle
   command while its lease is active.
4. Prevent finalization from running after the coding action has already failed.
5. Record a bounded failure reason in Orchestrator history without exposing logs or secrets.

## Non-goals

- Do not change or remove the existing worker-to-worker `reclaim-expired-claim` contract.
- Do not make lease tokens replayable, persist them outside the runner workspace, or pass them
  through workflow-dispatch inputs.
- Do not alter attempt budgets automatically.
- Do not add polling of GitHub workflow conclusions to Orchestrator.
- Do not redesign dispatch, verifier behavior, PR binding, evidence coverage, or the validation kit.
- Do not add lease heartbeat/renewal in this change. Runs that outlive the current lease remain a
  separate known problem.

## Approaches considered

### A. Add a SYSTEM recovery operation that ends in `ready` — selected

Release the expired claim, record `claimed|executing -> failed`, check current readiness and
attempt eligibility, then record `failed -> ready`. Return the work unit, not a lease. A normal
dispatch starts a real GitHub workflow, and that worker claims the next attempt.

This matches the deployed architecture and keeps lease authority inside the worker that uses it.

### B. Change `reclaim-expired-claim` to stop in `ready`

This is smaller in raw code but breaks an existing API contract and its tests. The existing
operation remains meaningful for a synchronous operator that can directly hand the returned lease
to a known worker, so changing it is unnecessary.

### C. Make reclaimed lease tokens retrievable or dispatchable

This would expand secret lifetime and create a second lease-delivery mechanism through GitHub
workflow inputs or external storage. It weakens the capability model and adds machinery solely to
work around the wrong recovery boundary. Rejected.

## Orchestrator design

### API

Add:

```text
POST /api/v1/work-units/{unit_id}/recover-expired-claim
```

The request uses the ordinary `CommandBase` fields:

```json
{
  "idempotency_key": "operator-selected-key",
  "expected_version": 7
}
```

Only an `ActorRole.SYSTEM` credential may call it. The success response is the ordinary
`UnitResponse`; it contains no `claim_id`, `lease_token`, or replacement owner.

### Service behavior

Within one database transaction and while holding the work-unit and latest-claim locks:

1. Require the exact expected version.
2. Require SYSTEM role.
3. Require the latest claim to be active-but-expired and the unit to be `claimed` or `executing`.
4. Release that claim with terminal reason `lease_expired`.
5. Transition the unit to `failed`, correlated to the recovery operation and attributed to SYSTEM.
6. Apply the existing shared readiness/attempt eligibility check.
7. If eligible, transition `failed -> ready` with the same correlation ID and commit.
8. If ineligible, commit the honest `failed` state and return the existing domain error.

The operation does not change `attempt_count` and does not insert a new `Claim`.

Exact replay returns the already-recovered unit without repeating transitions. Reuse of the same
idempotency key with a different unit, actor, or expected version fails with
`idempotency_conflict`.

### Lifecycle failure reason

Add optional `reason: str | None` to the public `LifecycleCommand` schema. The underlying
`TransitionCommand` and event payload already support it. Existing callers remain valid.

## Factory-runner design

### Client and CLI

Add `OrchestratorClient.fail(...)`, implemented through the existing generic lifecycle command
surface with command name `fail`.

Add:

```text
factory-runner fail-run --orchestrator-url ... --credential-key-id ... \
  --work-unit-id ... --workspace-dir ... --reason coding_action_failed
```

`fail-run` reads `run.json`, uses the recorded attempt, lease token, and current executing version,
and submits an idempotent worker failure command. It never prints the lease token. The reason is a
fixed workflow-stage value, not raw action output.

### Workflow

Give the coding and finalization steps stable IDs.

- Run finalization only when the coding step succeeded.
- Add a terminal failure-reporting step guarded by `always()` that runs only when prepare
  succeeded and either coding or finalization failed.
- Report `coding_action_failed` for coding failure and `finalization_failed` for finalizer failure.
- Preserve the original failing job conclusion; reporting failure must not turn failed work green.

Prepare failures are not included because a prepare failure may occur before a durable runner
workspace and lease exist. That crash window requires separate design if observed.

## Recovery data flow

```text
GitHub failure while lease active
  -> factory-runner fail-run
  -> executing -> failed, claim released
  -> SYSTEM requeue
  -> ready
  -> SYSTEM dispatch
  -> new GitHub runner claims next attempt

Expired stranded unit
  -> SYSTEM recover-expired-claim
  -> old claim released
  -> claimed|executing -> failed -> ready
  -> SYSTEM dispatch
  -> new GitHub runner claims next attempt
```

## Error handling

- A non-expired lease returns `lease_not_expired` without mutation.
- A unit without an active claim returns the existing claim error without mutation.
- An exhausted attempt budget leaves the unit honestly `failed` and returns
  `attempts_exhausted`; it does not create a false `ready` state.
- Unsatisfied readiness leaves the unit honestly `failed` and returns
  `readiness_not_satisfied`.
- A runner failure report with an expired, released, wrong-attempt, or wrong-owner lease fails
  closed through the existing active-claim guard.
- Failure reporting never includes raw Claude output, API credentials, or lease tokens in events.

## Tests

### Orchestrator

- Service test: expired `executing` claim becomes `ready`, claim is released, attempt count is
  unchanged, and no replacement claim exists.
- Service test: expired `claimed` unit follows the same behavior.
- Service test: non-expired claim is rejected without mutation.
- Service test: exhausted or no-longer-ready unit lands honestly in `failed`.
- Service test: exact idempotent replay emits no extra events; conflicting reuse fails.
- API test: SYSTEM succeeds; WORKER is forbidden; response/OpenAPI contains no lease token.
- Schema/contract test: lifecycle `reason` is optional and appears in generated command contract.

### Factory-runner

- Client test: `fail` posts `/commands/fail` with expected version, attempt, lease, idempotency key,
  and bounded reason.
- CLI test: `fail-run` reads the workspace authority, reports failure, and does not print the lease.
- CLI test: missing or mismatched workspace fails locally without an API mutation.
- Workflow contract tests: finalizer requires coding success; failure reporter uses `always()`, runs
  only after successful prepare, and distinguishes coding from finalizer failure.
- Cross-repo command-contract fixture is regenerated and pinned after the optional `reason` field is
  added.

## Production recovery after merge and deploy

1. Re-read the live unit; do not trust the state captured in the earlier run note.
2. If attempt 2 remains `claimed` with an expired lease, call `recover-expired-claim` with its live
   version.
3. Verify `ready`, attempt count 2, released claim, and no newly created claim.
4. Dispatch runner attempt 3 once.
5. Observe the real GitHub workflow and stop at the first incorrect behavior.
6. If it fails, verify factory-runner records `failed` and releases the claim rather than leaving
   the unit `executing`.

Production deployment and the attempt-3 dispatch remain explicit post-merge operations. No test or
CI job performs them automatically.
