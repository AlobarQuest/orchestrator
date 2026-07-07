11# Orchestrator Production Deploy Plan

Work unit: `deploy-plan`
Package: `orchestrator-production-deploy` revision 1
Revision ID: `085acc0c-fc94-45de-858b-a35e14e3b960`
Unit ID: `277e57c7-6afa-56e0-99b8-bd5c613d7447`
Status: read-only plan for Devon review

## Boundary

This plan is the required read-only infrastructure plan before any Coolify, DNS,
backup, secret, credential, or production database mutation.

The infrastructure mutation must happen in a fresh infrastructure-only session
through the existing change-manager/infraops lane. The orchestrator is a client
of that lane; it does not replace change-manager for sensitive infrastructure.

This plan does not implement factory-runner dispatch, reusable runner workflows,
runner credential rollout, Phase-5 verifier behavior, tracker canonicalization,
brain learning/promotion, graduation automation, or automatic merge.

## Target Shape

- Public service: `https://sds.alobar.net`
- Application: `AlobarQuest/orchestrator` deployed as the existing Docker image
  shape from this repository.
- Runtime: Coolify application using the repository `Dockerfile`.
- Database: separate production PostgreSQL resource for orchestrator state.
- Human surface: protected by Alobar ID forward-auth.
- Machine API: bearer M2M auth with credential-key ID.
- Registry: actor registry bundle pinned at image build by security-standards
  source revision and artifact digest.
- Secrets: sourced through BWS/Coolify/GitHub secret patterns only; no values in
  tracked files, prompts, workflow files, logs, or evidence.
- Backups: production database covered by `vps-backup` before first real
  production orchestrator data is accepted.

## Pre-Mutation Checks

Run these checks in the fresh infrastructure session before any mutation:

- Confirm the approved package hash:
  `2f6bc7da07aa00106cb6008fc8a85878e001652f6ec645bf25a37760d84c2e7d`
- Confirm this repository is on merged `main` with no unrelated uncommitted
  changes intended for deployment.
- Run `make check` from a fresh shell.
- Confirm `portfolio foundation` remains clean from `project-standards`.
- Confirm the factory-events chain verifies against its latest anchor.
- Confirm the production deployment does not depend on any runner dispatch path.
- Confirm BWS secret references are available before entering secret values into
  Coolify or GitHub settings.
- Confirm `vps-backup` can cover the new production database before production
  data is accepted.

## Repository Prep

The current Dockerfile already has the intended deployment shape:

- builder stage installs production dependencies with `uv sync --frozen --no-dev`
- registry bundle is built at image build time from a supplied registry artifact
- runtime stage runs as the non-root `orchestrator` user
- `ORCHESTRATOR_REGISTRY_BUNDLE` defaults to `/app/registry-bundle.json`
- app command is `uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000`
- healthcheck calls `/health/live`

Repository prep should only add or adjust deployment documentation, build
metadata, or CI/image-publish mechanics if the infrastructure session proves
they are missing. It must not add secrets or dispatch behavior.

## Coolify Plan

In the infrastructure-only session:

1. Create a new Coolify application for `AlobarQuest/orchestrator`.
2. Configure it to deploy the existing Dockerfile image path.
3. Provide build arguments:
   - `SECURITY_STANDARDS_REVISION`
   - `REGISTRY_ARTIFACT_SHA256`
4. Create a separate PostgreSQL 16 production database resource.
5. Set runtime environment:
   - `ORCHESTRATOR_DATABASE_URL`
   - `ORCHESTRATOR_M2M_CREDENTIALS`
   - `ORCHESTRATOR_M2M_ROLES` if non-worker machine roles are configured
   - `ORCHESTRATOR_TRUSTED_PROXY_IPS`
   - `ORCHESTRATOR_PROXY_MARKER`
   - `ORCHESTRATOR_EMAIL_TO_ACTOR`
   - `ORCHESTRATOR_CSRF_SECRET`
6. Configure `sds.alobar.net` routing.
7. Configure Alobar ID forward-auth for protected human routes.
8. Leave `/health/live` suitable for monitoring.
9. Run migrations explicitly as an operator step:
   - check current revision
   - check repository heads
   - run upgrade to head
   - check current revision again

## Secret Boundary

No secret value should appear in:

- tracked files
- package YAML
- workflow files
- shell history copied into evidence
- screenshots
- logs attached as evidence
- prompts or chat transcripts

Allowed evidence records may include:

- BWS secret UUID references
- Coolify variable names with values redacted
- GitHub secret names with values redacted
- command names without secret-bearing arguments
- pass/fail summaries of auth probes

If any live secret value is exposed, stop deployment and rotate it before
continuing.

## Backup Plan

Before accepting first real production orchestrator data:

1. Add the orchestrator production database to `vps-backup` coverage.
2. Record the backup target identifier.
3. Run or verify a backup job that includes the database.
4. Prove restore/read verification according to the `vps-backup` standard.
5. Record evidence without database credentials or dump content.

If backup coverage cannot be proven, do not cut over to real production use.

## Verification Plan

Required post-deploy checks:

- `https://sds.alobar.net/health/live` returns success.
- `/health/ready` reflects database readiness after migrations.
- Protected human routes reject unauthenticated direct access.
- Protected human routes accept requests only through configured Alobar ID
  forward-auth.
- M2M API rejects missing bearer auth.
- M2M API rejects invalid bearer auth.
- M2M API accepts only configured credential-key and bearer-token pairing.
- Migrations report the expected head.
- Security scan reports zero BLOCK findings for the repository.
- Deployment evidence names the package revision, image/commit, Coolify
  resources, database resource, health probes, auth probes, backup evidence, and
  rollback references.

## Rollback Plan

Rollback must be ready before cutover is considered complete:

- Disable or stop the Coolify application.
- Remove or reroute `sds.alobar.net` away from the new app.
- Preserve the production database for audit unless Devon explicitly approves
  deletion in a separate operation.
- If real data was accepted, restore from the verified backup or keep the
  database read-only while routing is disabled.
- Revert any repository deployment-prep PR through normal GitHub review if that
  PR caused the failure.
- Do not delete package history, orchestrator lifecycle records, evidence, or
  unrelated infrastructure.

## Go/No-Go Checkpoint

Devon must review this plan before mutation begins.

Proceed only if Devon confirms:

- this plan is sufficient for the infrastructure-only session
- BWS-managed secret references are available
- backup coverage can be established before production use
- the scope still excludes runner dispatch and Phase-5 behavior
- the infra session will use change-manager/infraops as the authority lane

## Protocol Friction Recorded

During local dogfooding, two orchestrator contract gaps surfaced:

- package approval event IDs are ledger strings such as `evt-...`; the
  orchestrator intake/API/persistence path previously required UUIDs
- package revisions without `required_context` could create work units that no
  complete worker context could claim

Both are local orchestrator fixes required for the bootstrap to proceed and
should be reviewed with the branch before relying on this protocol in
production.
