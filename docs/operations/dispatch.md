# Dispatch Adapter Operations

WS-4.2 adds an orchestrator-side adapter that dispatches one Ready work unit to the
factory-runner workflow **in that unit's own target repository**. The orchestrator remains
canonical lifecycle truth: GitHub workflow state and runner PRs are evidence only, and
Devon's merge gate remains permanent.

## Routing

Each work unit declares its target repository at
`authority.constraints.target_repository`. Dispatch resolves the repository **per unit**
and fires that repository's caller workflow. This is not a convenience: the factory-runner
refuses to act unless the workflow it is running in *is* the unit's target repository
(`factory_runner/authority.py`, `target_repo != current_repo` → `AuthorityError`), because
the runner may only mutate the repository it checked out. A process-global target would
therefore misroute every fan-out unit — opening a PR against the wrong repository — rather
than fail closed.

`ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES` bounds where dispatch may route.
It is empty by default, so an unconfigured orchestrator dispatches nowhere.

Every target repository must host the caller workflow at `ORCHESTRATOR_DISPATCH_WORKFLOW_ID`
and carry the `FACTORY_RUNNER_TOKEN`, `FACTORY_RUNNER_CREDENTIAL_KEY_ID`, and
`ANTHROPIC_API_KEY` secrets.

## Runtime Controls

Dispatch is fail-closed by default.

- `ORCHESTRATOR_DISPATCH_ENABLED`: global kill switch. Defaults to `false`.
  Read once per process (`get_settings` is `lru_cache`d), so flipping it needs a restart.
- `ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES`: allowlisted change classes.
- `ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES`: allowlisted runner capabilities.
- `ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES`: allowlisted target repos. Empty by default.
- `ORCHESTRATOR_DISPATCH_WORKFLOW_ID`: workflow file or ID, default `.github/workflows/factory-runner-pilot.yml`.
- `ORCHESTRATOR_DISPATCH_WORKFLOW_REF`: workflow ref, default `main`.
- `ORCHESTRATOR_DISPATCH_ORCHESTRATOR_URL`: callback URL, default `https://sds.alobar.net`.
- `ORCHESTRATOR_GITHUB_DISPATCH_TOKEN`: least-privilege GitHub credential for Actions dispatch.
- `ORCHESTRATOR_DISPATCH_FAILURE_SIGNATURE_THRESHOLD`: same-signature failure threshold, default `3`.
- `ORCHESTRATOR_DISPATCH_HUMAN_GATE_AGE_OUT_SECONDS`: optional age-out evidence threshold for human-gate states.

The frozenset-valued variables are parsed as JSON, e.g.
`ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES='["AlobarQuest/brain"]'`.

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
- the unit declares `authority.constraints.target_repository`, and that repository is
  allowlisted;
- the unit's own conformance claim is green, or every touched standard is explicitly
  accepted in it.

## Conformance

Conformance is attested **per work unit**, in `authority.conformance`, against that unit's
own target repository:

```json
{"status": "green", "standards_touched": ["project"], "accepted_standards": []}
```

It cannot live on the package revision's enforcement snapshot. That snapshot is written once
at intake, *before* decomposition has chosen the target repositories — so a single snapshot
could not honestly describe a fan-out across several repos, and at intake the orchestrator
does not yet know which repos to inspect. Because the claim lives in the authority envelope,
it is covered by the authority fingerprint, so the human's per-unit authority approval
attests the conformance too. A malformed claim is rejected at proposal time
(`authority_conformance_invalid`); a missing one fails closed (`conformance_missing`).

The decomposition author computes it from real repo state — `security_scan.cli.scan(repo)`
and `portfolio.compliance.build_rows([(repo, frontmatter)], …)` are both importable and
local-only. **`accepted_standards` must come from a real waiver source** (project-standards'
`exceptions:` frontmatter, security-standards' `.security-scan-allow.toml`) and must never be
echoed from `standards_touched` — the `touched ⊆ accepted` branch would otherwise admit
everything.

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
