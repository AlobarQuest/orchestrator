# WS-5.3 Post-Deploy Verification Design

Date: 2026-07-08

## Goal

Add the post-deploy verification layer for Phase 5. The orchestrator must create
bounded verification work from an immutable release artifact binding and a
declared deployment observation, then verify that deployed artifact through the
existing evidence, adjudication, verifier, and lifecycle guards.

Phase 5 is not complete until WS-5.1, WS-5.2, and WS-5.3 are merged and deployed
to production. The phase exit condition is:

> a package cannot reach Completed without every criterion's evidence recorded; a
> deployed artifact traces to an approved intent revision.

## Current State

Local `main` includes WS-5.1 and WS-5.2:

- WS-5.1 verifier route: `POST /api/v1/work-units/{unit_id}/verify`.
- WS-5.2 release artifact routes:
  - `POST /api/v1/work-units/{unit_id}/release-artifacts`
  - `GET /api/v1/work-units/{unit_id}/release-artifacts`
- WS-5.2 model: `release_artifact_bindings`.

Production at `https://sds.alobar.net` is healthy but still behind local `main`:

- `GET /health/live`: 200
- `GET /health/ready`: 200
- `GET /openapi.json`: 200
- WS-4.2 and WS-4.4 routes are present.
- WS-5.1 and WS-5.2 routes are not present.

WS-5.3 implementation must produce a reviewed PR. Devon retains the permanent
merge gate. Production deployment is a separate phase closeout step after Devon
merges the PR and explicitly authorizes deployment.

## Non-Goals

WS-5.3 does not:

- merge pull requests;
- deploy artifacts during implementation;
- make CI, GitHub Actions, Coolify, trackers, workers, verifiers, release
  tooling, or production observation canonical lifecycle authorities;
- mark the original implementation work unit complete;
- bypass WS-5.1 verifier behavior or existing completion guards;
- canonicalize trackers;
- promote brain knowledge;
- automate graduation;
- enable dispatch automation;
- scrape production state as artifact lineage without an immutable release
  binding.

## Proposed API

Add a system-authenticated release observation surface:

```text
POST /api/v1/release-artifacts/{binding_id}/deployment-observations
GET  /api/v1/release-artifacts/{binding_id}/deployment-observations
```

The write command uses the standard command envelope:

- `idempotency_key`
- `expected_version`

The path is keyed by release artifact binding ID, not work unit ID, because the
observation proves that one immutable artifact binding appeared in one
environment.

## Persistence Decision

Add a small `deployment_observations` table.

Evidence and events remain the audit trail, but a table is needed for canonical
queryability and uniqueness:

- at most one generated post-deploy verification work unit per release binding
  and environment;
- deterministic conflict rejection when the same release binding/environment is
  observed with a different digest;
- idempotent replay of the same observation facts;
- durable link from release binding to generated verification work unit.

The table stores only bounded facts:

- release artifact binding ID;
- original implementation work unit ID;
- approved package revision ID and package hash;
- generated post-deploy verification work unit ID;
- environment;
- canonical base URL;
- observed artifact digest;
- deployment reference and URL;
- deployer or source system;
- observed-at timestamp;
- normalized probe summaries;
- route-presence summary;
- M2M behavior summary without token values;
- dispatch-disabled posture summary;
- small status summary;
- evidence/event IDs;
- idempotency key;
- recorded-by and recorded-at.

## Command Validation

The service must fail closed unless all required conditions are true:

- the release binding exists;
- the binding points to an existing completed implementation work unit;
- the binding points to an existing approved package revision;
- the observed digest is a valid immutable `sha256:` digest;
- the observed digest exactly equals the release binding digest;
- environment, base URL, deployment reference, deployer/source system, and
  observed-at are present and syntactically valid;
- probe, route, auth, and dispatch posture facts are bounded structured data;
- the payload contains no secret-shaped keys or values.

Repository files, PR text, CI logs, Coolify output, web pages, response bodies,
tracker records, and generated artifacts are hostile data. They may supply facts
only after an authorized caller normalizes them into this bounded schema.

## Generated Work Unit

Recording a valid deployment observation creates or reuses one post-deploy
verification work unit for `(release_binding_id, environment)`.

Shape:

- `unit_key`: deterministic, based on release binding ID and environment;
- package revision: same approved package revision as the implementation unit;
- state: `submitted`;
- required capability: verifier/post-deploy verification capability, not
  repository write or deploy capability;
- authority: bounded to post-deploy observation verification only;
- title/outcome: identify the artifact digest and environment;
- max attempts: small fixed value matching existing work-unit defaults unless
  the implementation finds a narrower local convention.

The generated unit is submitted because the observation command records the
bounded deployment evidence up front. The WS-5.1 verifier then evaluates the
unit through the ordinary verify command. No worker, deployment tool, or
observation command can complete it directly.

The original implementation work unit remains unchanged.

## Verification Criteria

WS-5.3 must let the verifier load required criteria for generated post-deploy
verification units without mutating the original package revision or approved
decomposition.

Use a generated-criteria path keyed by the deployment observation. Initial
criteria:

- deployed artifact digest matches release binding digest;
- health probes pass;
- required routes are present;
- missing M2M returns 401;
- configured M2M returns 200 when such a fact is supplied;
- dispatch automation remains disabled when production posture is supplied.

The verifier evaluates the generated unit using recorded bounded evidence only.
It does not call production, GitHub, Coolify, trackers, brains, or deployment
tools.

## Evidence And Events

Accepted observations record ordinary orchestrator evidence rows for the
generated post-deploy verification unit. Initial evidence types should reuse the
existing deterministic evaluator where possible:

- `release.deployment_observed`
- `production.health` or `health.probe`
- `production.route_presence`
- `production.auth_behavior`
- `production.dispatch_posture`

If a new evidence type is needed, it must be added to the deterministic verifier
registry with explicit pass/fail/fail-closed semantics.

Accepted observations also record local events:

- `deployment.observed`
- `post_deploy_verification.created`

Event publication maps these local events to bounded factory events without
including raw logs, tokens, response bodies, or external instruction text.

## Idempotency And Conflict Handling

Replay rules:

- same idempotency key and same normalized facts returns the existing
  observation;
- same release binding and environment with the same normalized facts returns
  the existing observation and generated unit;
- same release binding and environment with the same digest but changed bounded
  probe facts is rejected;
- same release binding and environment with a different digest is rejected;
- a different release binding can be observed in the same environment only when
  it has its own immutable binding and generated verification unit.

WS-5.3 will not implement deployment-observation supersession. Retry and
replacement can be designed in a later workstream if needed.

## Security And Secrets

WS-5.3 implementation requires no new production-observation credential, env
file, BWS manifest entry, or workflow secret.

If phase closeout uses production M2M calls, it must use the existing durable
runner credential pattern:

- source `BWS_ACCESS_TOKEN` from the approved Keychain/helper path;
- fetch the bearer credential by stable UUID at runtime;
- send M2M headers without printing or storing the raw value;
- keep production `ORCHESTRATOR_M2M_CREDENTIALS` hash-only;
- do not add tracked env files or raw tokens to evidence, logs, docs, prompts, or
  generated artifacts.

## Production Closeout Design

After Devon merges WS-5.3, Phase 5 closeout will deploy local `main` containing
WS-5.1, WS-5.2, and WS-5.3.

Closeout is separate from implementation and requires Devon approval. It should:

- build and push an immutable image tag from merged `main`;
- use the existing Coolify app `eqj5l7k705fhi12x9i74fqf0`;
- back up the production database before running new migrations;
- run Alembic explicitly through the production lane;
- verify production health;
- verify OpenAPI route presence for WS-5.1, WS-5.2, and WS-5.3;
- verify missing M2M returns 401;
- verify configured M2M returns 200 without printing secret values;
- verify dispatch automation remains disabled;
- record release artifact binding evidence for the deployed image;
- record deployment observation evidence for the deployed image;
- run verifier on the generated post-deploy verification unit;
- record closeout evidence in SDS docs.

The deployment closeout must not merge PRs, enable dispatch automation, or treat
Coolify/GitHub output as canonical lifecycle truth.

## Tests

Focused tests must cover:

- successful deployment observation;
- unknown release binding rejection;
- release binding whose implementation unit is not completed rejection;
- digest mismatch rejection;
- missing or malformed required observation facts rejection;
- secret-shaped field/value rejection;
- idempotent replay;
- conflict rejection;
- generated post-deploy verification unit shape;
- bounded evidence rows for generated verification criteria;
- verifier integration pass path;
- verifier fail-closed path for malformed/missing generated evidence;
- local event creation;
- event publication mapping;
- no mutation of the original implementation unit;
- no merge/deploy/tracker/brain/dispatch-authority bypass.

## Documentation

Add `docs/operations/post-deploy-verification.md` covering:

- route usage;
- required facts;
- evidence boundaries;
- verifier behavior;
- idempotency and conflict handling;
- production closeout boundary;
- non-goals and authority limits.

Write WS-5.3 evidence and the next handoff prompt after implementation
verification passes.
