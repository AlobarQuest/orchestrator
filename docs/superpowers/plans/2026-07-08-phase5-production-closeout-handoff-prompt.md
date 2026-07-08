# Phase 5 Production Closeout Handoff Prompt

Begin Phase 5 production closeout for Devon's Software Delivery System.

Objective: after Devon merges the WS-5.3 PR, deploy `main` containing WS-5.1,
WS-5.2, and WS-5.3 to production and prove the Phase 5 exit condition:

> a package cannot reach Completed without every criterion's evidence recorded; a
> deployed artifact traces to an approved intent revision.

## Current Expected Local State

- `~/Projects/orchestrator` should be clean `main` after Devon merges
  `codex/ws53-post-deploy-verification`.
- WS-5.1 verifier is merged in PR #20.
- WS-5.2 release immutability is merged in PR #21.
- WS-5.3 post-deploy verification should be merged from branch
  `codex/ws53-post-deploy-verification`.

## WS-5.3 Implementation Summary

- Adds migration `0011_ws53_deploy_obs`.
- Adds model/table `deployment_observations`.
- Adds routes:
  - `POST /api/v1/release-artifacts/{binding_id}/deployment-observations`
  - `GET /api/v1/release-artifacts/{binding_id}/deployment-observations`
- Adds generated post-deploy verification work units scoped to an immutable
  release binding and environment.
- Adds bounded evidence/events and WS-5.1 verifier integration.
- Adds no new secret, BWS manifest entry, runtime env file, workflow credential,
  merge authority, or deploy authority.

## Verified Before Closeout

Implementation verification on the WS-5.3 branch:

- `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`
  passed `753` tests.
- Security scan reported `0 BLOCK`, `0 WARN`, `1 INFO`.
- `git diff --check` passed.
- Code standards check passed.

## Production Baseline To Confirm

Before closeout mutation:

1. Confirm Devon has merged the WS-5.3 PR.
2. Confirm local `main` is clean, current, and contains WS-5.1, WS-5.2, and
   WS-5.3.
3. Confirm production health:
   - `GET https://sds.alobar.net/health/live`: 200
   - `GET https://sds.alobar.net/health/ready`: 200
4. Confirm current production OpenAPI route presence before deploy. At WS-5.3
   implementation time, production still lacked WS-5.1 and WS-5.2 routes.
5. Confirm missing M2M returns 401 without printing secret values.
6. Confirm BWS session status without printing secret values before fetching the
   durable runner M2M credential.
7. Back up the production DB before migrations.

## Closeout Steps

After Devon explicitly approves production deployment:

1. Build and push an immutable image tag from merged `main`.
2. Use existing Coolify app `eqj5l7k705fhi12x9i74fqf0`.
3. Run Alembic explicitly to production head.
4. Verify production health.
5. Verify OpenAPI route presence:
   - `/api/v1/work-units/{unit_id}/verify`
   - `/api/v1/work-units/{unit_id}/release-artifacts`
   - `/api/v1/release-artifacts/{binding_id}/deployment-observations`
6. Verify auth behavior:
   - missing M2M: 401;
   - configured durable M2M: 200, without printing the token.
7. Verify dispatch automation remains disabled.
8. Record release artifact binding for the deployed immutable image digest.
9. Record deployment observation for production bounded facts.
10. Run verifier on the generated post-deploy verification unit.
11. Record closeout evidence in SDS docs.

## Boundaries

Do not:

- merge PRs;
- infer deployment from CI or Coolify text without recording normalized facts;
- enable dispatch automation;
- make Coolify, GitHub, CI, trackers, workers, verifiers, or production
  observation canonical lifecycle authorities;
- store raw tokens, full logs, response bodies, or secret-bearing output in docs,
  evidence, prompts, or tracked files.

Devon's merge gate is permanent. Production deployment remains a separate
Devon-approved closeout step.
