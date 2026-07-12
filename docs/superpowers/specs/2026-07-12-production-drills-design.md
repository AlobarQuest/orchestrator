# Production Drill Design

**Status:** approved in principle, pending written-spec review.

## Goal

Make the five recovery drills prove the deployed orchestrator's real API and
recovery behavior. Local drills remain component drills; they cannot be treated
as production evidence.

## Decision

Use a dedicated production-drill contract, not a `--production` switch on the
local shell harness. Each run creates explicit, namespaced canonical records in
the live orchestrator and closes them through an audited, append-only process.
The crash drill performs one controlled restart of `sds.alobar.net`; this is an
intentional acceptance condition because a replica does not prove live recovery.

## Run Model

A production drill starts from one human-approved immutable recovery-drills
package revision. A browser-authenticated HUMAN calls
`POST /api/v1/production-drills` to create and authorize the run; a dedicated
SYSTEM drill credential can operate only on that authorized run. A HUMAN closes
the run after reviewing its submitted assertions. It creates a run with:

- immutable run ID and package-revision reference;
- exact application image reference, image digest, and OpenAPI digest;
- owner, start and end timestamps, assertions, and closure outcome;
- namespaced synthetic work units, evidence, observations, and conditions.

Only resources tagged with the run ID may be controlled by the drill APIs. The
service must reject arbitrary unit IDs, cross-run reads, missing human approval,
worker credentials, and arbitrary timeout values. Production-drill rows are
never deleted; closing a run resolves or completes its synthetic records with a
named `production_drill_closed` audit reason.

## Assertions And Timing

The production API exposes a run-scoped read model for state, version, claim,
evidence-head/supersession, reconciliation conditions, and deployment
observations. The runner never uses production SQL.

Run-scoped lease and reporting deadlines are bounded at run creation. They do
not mutate global application settings or ordinary work units. This lets the
evidence-recovery, split-brain, and stalled-approval cases use elapsed time
without a private database backdate or process-wide threshold override.

## Drill Mapping

1. Crash recovery: create a synthetic unit and verify dispatch-disabled state;
   restart the live application using the approved Coolify control surface;
   after readiness returns, wait for the run-scoped lease deadline and reclaim
   through the public API.
2. Evidence recovery: allow the run-scoped lease to expire, prove the worker is
   locked out, recover the evidence as SYSTEM, and assert one superseding head
   through the run-scoped read model.
3. External PR conflict: bind reserved synthetic PR identifiers, ingest
   synthetic observations, assert both alarms and normal-iteration silence, and
   close the generated conditions with the run.
4. Deploy split-brain: complete a synthetic unit, bind a clearly synthetic
   release artifact and deployment observation, wait for the run-scoped
   deadline, invoke detection, and close the generated condition and post-
   deployment unit.
5. Stalled approval: move a synthetic unit to an approval gate, wait for its
   run-scoped deadline, assert the dead-letter projection and lack of
   requeue eligibility, then close the run without treating silence as approval.

## Safety Boundaries

- Production-drill data is visibly namespaced and excluded from ordinary
  operational queues by default, while remaining queryable in its run view.
- No production SQL, direct table mutation, process signal, or global config
  override is permitted from the drill runner.
- The controlled restart is the sole deliberate availability interruption. It
  requires a preflight readiness check and a separate explicit approval at run
  time.
- The runner uses a dedicated BWS-managed credential by stable UUID. It never
  stores or prints bearer material.
- A run cannot report PASS until every synthetic record has reached its audited
  closed state.

## Verification

Tests cover rejected non-drill IDs, missing human authorization, worker actors,
cross-run reads, arbitrary deadlines, and closure before assertions. The
production runner preflights the live OpenAPI and readiness endpoint, records
the image and OpenAPI digest, and emits per-drill evidence. A final run against
`sds.alobar.net` proves all five drills, including the controlled restart.

## Related Phase 0 Work

The executable exit-criteria guard is separate from the drill contract. It uses
a versioned criteria manifest and live OpenAPI to prevent an item from being
marked MET when its declared routes are absent from production.
