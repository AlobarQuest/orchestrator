# Dispatch Adapter Operations

WS-4.2 adds an orchestrator-side adapter that can dispatch one Ready work unit to the
factory-runner pilot workflow. The orchestrator remains canonical lifecycle truth:
GitHub workflow state and runner PRs are evidence only, and Devon's merge gate remains
permanent.

## Runtime Controls

Dispatch is fail-closed by default.

- `ORCHESTRATOR_DISPATCH_ENABLED`: global kill switch. Defaults to `false`.
- `ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES`: allowlisted change classes.
- `ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES`: allowlisted runner capabilities.
- `ORCHESTRATOR_DISPATCH_TARGET_REPOSITORY`: target repo, default `AlobarQuest/orchestrator`.
- `ORCHESTRATOR_DISPATCH_WORKFLOW_ID`: workflow file or ID, default `.github/workflows/factory-runner-pilot.yml`.
- `ORCHESTRATOR_DISPATCH_WORKFLOW_REF`: workflow ref, default `main`.
- `ORCHESTRATOR_DISPATCH_ORCHESTRATOR_URL`: callback URL, default `https://sds.alobar.net`.
- `ORCHESTRATOR_GITHUB_DISPATCH_TOKEN`: least-privilege GitHub credential for Actions dispatch.
- `ORCHESTRATOR_DISPATCH_FAILURE_SIGNATURE_THRESHOLD`: same-signature failure threshold, default `3`.
- `ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS`: optional age-out evidence threshold for human-gate states.

The GitHub credential must be provided only through the approved BWS/Coolify secret
path. Do not store raw tokens in tracked files, prompts, logs, package YAML,
evidence, PR bodies, or generated artifacts.

## Admission

A work unit is dispatchable only when all of these are true:

- the global kill switch is enabled;
- the GitHub dispatch credential is configured;
- the unit is `ready`;
- an approved authority envelope is recorded on the unit;
- the unit capability and change class are allowlisted;
- the package revision conformance snapshot is green, or every touched standard is
  explicitly accepted in the snapshot.

Missing or unknown conformance fails closed. Human-gate states never auto-proceed by
timeout. Optional age-out records blocked evidence with reason `human_gate_aged_out`
without changing the lifecycle state.

## Idempotency And Evidence

Dispatch idempotency is enforced by both `idempotency_key` and
`(work_unit_id, runner_attempt)`. Replaying the same dispatch returns the existing
record and does not call GitHub again.

Every dispatch outcome records a `dispatch_records` row and a canonical orchestrator
event:

- `dispatch.dispatched`
- `dispatch.skipped`
- `dispatch.blocked`
- `dispatch.failed`

The adapter records target repository, workflow ID, workflow ref, GitHub run fields
when available, skipped or blocked reasons, and failure signatures. The workflow
inputs remain one work unit per runner execution:

- `work_unit_id`
- `orchestrator_url`

Runner-created commits and PRs must retain greppable provenance:

- `SDS-Unit:`
- `SDS-Package-Rev:`

No dispatcher or runner path may merge PRs.

## Infra-Lane Boundary

Do not use the dispatch adapter for work units whose authority envelope covers
production infrastructure mutation. Those units route to the existing
change-manager/infraops lane and are linked back to the work unit as evidence;
see `docs/operations/infra-lane-linkage.md`.
