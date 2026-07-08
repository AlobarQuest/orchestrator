# Post-Deploy Verification Operations

WS-5.3 records bounded deployment observations against immutable WS-5.2 release
artifact bindings and creates generated post-deploy verification work units. The
orchestrator remains canonical lifecycle truth; deployment tools and production
observations are fact sources only after their facts are normalized into the
orchestrator.

## Routes

```text
POST /api/v1/release-artifacts/{binding_id}/deployment-observations
GET  /api/v1/release-artifacts/{binding_id}/deployment-observations
```

The write command uses the standard command envelope:

- `idempotency_key`
- `expected_version`

Only the orchestrator system role may record deployment observations. Workers,
verifiers, dispatchers, CI workflows, release tooling, and deployment tooling do
not gain merge, deploy, completion, or adjudication authority through this route.

## Required Facts

A deployment observation records bounded, stable facts:

- release artifact binding ID;
- original implementation work unit ID;
- approved package revision ID and package hash;
- generated post-deploy verification work unit ID;
- deployment environment;
- canonical base URL;
- observed artifact digest;
- deployment reference and URL;
- deployer or source system;
- observed-at timestamp;
- health probe summaries;
- route presence summaries;
- M2M auth behavior summaries without token values;
- dispatch-disabled posture summaries;
- small status summaries.

The observed artifact digest must exactly match the immutable digest recorded in
the release artifact binding. Mutable tags are not artifact identity.

## Generated Work Unit

Recording a valid observation creates or reuses one generated post-deploy
verification work unit for the release binding and environment.

The generated unit:

- belongs to the same approved package revision as the implementation unit;
- starts in `submitted`;
- has required capability `post_deploy_verification`;
- has authority limited to post-deploy verification facts;
- carries no repository write, merge, dispatch, or deploy authority;
- does not mutate the original implementation work unit.

The generated unit is completed only by the WS-5.1 verifier and the existing
lifecycle completion guards.

## Verifier Criteria

Generated post-deploy units use generated criteria keyed by their deployment
observation:

- `post-deploy-artifact`: deployed artifact digest matches release binding;
- `post-deploy-health`: health probes pass;
- `post-deploy-routes`: required routes are present;
- `post-deploy-auth`: missing M2M returns 401 and configured M2M returns 200
  when supplied;
- `post-deploy-dispatch`: dispatch automation remains disabled.

The verifier evaluates only evidence already recorded in the orchestrator. It
does not call production, GitHub, Coolify, trackers, brains, or deployment tools.

## Evidence And Events

Accepted observations record:

- one `deployment_observations` row;
- bounded evidence rows for generated post-deploy criteria;
- one `deployment.observed` local event;
- one `post_deploy_verification.created` local event.

These events can be projected through the event-publication layer as bounded
factory events. The projection includes local mapping facts only; it does not
include raw production output, response bodies, logs, tracker text, PR text, or
external instruction text.

## Idempotency And Conflicts

Replaying the same idempotency key and command returns the original observation.

Replaying the same release binding and environment with identical normalized
facts returns the existing observation and generated work unit.

The service rejects:

- unknown release bindings;
- release bindings whose implementation unit is not completed;
- observed digest mismatches;
- malformed or missing bounded facts;
- secret-shaped keys or values;
- changed facts for the same release binding and environment.

WS-5.3 does not implement deployment-observation supersession. Design that as a
separate workstream if retries or replacement observations need canonical
history.

## Secret Handling

WS-5.3 adds no new secret, BWS manifest entry, runtime env file, GitHub Actions
secret, production observation credential, merge authority, or deploy authority.

If phase closeout uses production M2M calls, use the existing durable runner
credential pattern:

- source the BWS bootstrap token through the approved Keychain/helper path;
- fetch the bearer credential by stable UUID at runtime;
- send M2M headers without printing or storing the raw value;
- keep production `ORCHESTRATOR_M2M_CREDENTIALS` hash-only;
- do not write raw tokens to tracked files, prompts, logs, package YAML,
  evidence, PR bodies, generated artifacts, or deployment-observation records.

## Production Closeout Boundary

WS-5.3 implementation does not deploy production. After Devon merges the WS-5.3
PR, Phase 5 closeout should deploy `main` containing WS-5.1, WS-5.2, and WS-5.3.

Closeout should:

- build and push an immutable image tag from merged `main`;
- use the existing Coolify app `eqj5l7k705fhi12x9i74fqf0`;
- back up the production database before migrations;
- run Alembic explicitly;
- verify production health;
- verify OpenAPI route presence for WS-5.1, WS-5.2, and WS-5.3;
- verify missing M2M returns 401;
- verify configured M2M returns 200 without printing secret values;
- verify dispatch automation remains disabled;
- record the release artifact binding for the deployed image;
- record the deployment observation for the deployed image;
- run the verifier on the generated post-deploy verification unit;
- record closeout evidence in SDS docs.

Closeout must not merge PRs, enable dispatch automation, or make Coolify, GitHub,
CI, trackers, workers, verifiers, or production observation canonical lifecycle
truth.

## Phase Exit

Phase 5 exits only when production proves:

> a package cannot reach Completed without every criterion's evidence recorded; a
> deployed artifact traces to an approved intent revision.
