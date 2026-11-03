# Release records: text superseded 2026-11-03

Release Part 3 corrected `docs/operations/post-deploy-verification.md` and
`docs/operations/release-immutability.md`. The text those pages held, kept here.

## The event-publication layer (deleted by Tier 3 item 24a, 2026-09-28)

From `post-deploy-verification.md`, "Evidence And Events":

> The event-publication layer that could project these as bounded factory events was deleted by
> Tier 3 item 24a (2026-09-28). The projection includes local mapping facts only; it does not
> include raw production output, response bodies, logs, tracker text, PR text, or external
> instruction text.

From `release-immutability.md`, "Evidence And Events":

> The event-publication layer that could project the local event was deleted by Tier 3 item 24a
> (2026-09-28); the rest of this paragraph describes what it did. The local `system` actor maps to
> the registered `unknown` external actor during publication while preserving the raw actor ID in
> the mapping evidence record.

The local events (`release_artifact.bound`, `deployment.observed`,
`post_deploy_verification.created`) are still written; only their publication is gone.

## The WS-5.3 production closeout (Phase 5)

From `post-deploy-verification.md`, "Secret Handling" and "Production Closeout Boundary". It was a
one-time checklist for closing Phase 5, and the deploy command (`docs/operations/deploy.md`) has
since replaced it.

> WS-5.3 adds no new secret, BWS manifest entry, runtime env file, GitHub Actions secret,
> production observation credential, merge authority, or deploy authority.
>
> If phase closeout uses production M2M calls, use the existing durable runner credential pattern:
> source the BWS bootstrap token through the approved Keychain/helper path; fetch the bearer
> credential by stable UUID at runtime; send M2M headers without printing or storing the raw
> value; keep production `ORCHESTRATOR_M2M_CREDENTIALS` hash-only; do not write raw tokens to
> tracked files, prompts, logs, package YAML, evidence, PR bodies, generated artifacts, or
> deployment-observation records.
>
> WS-5.3 implementation does not deploy production. After Devon merges the WS-5.3 PR, Phase 5
> closeout should deploy `main` containing WS-5.1, WS-5.2, and WS-5.3.
>
> Closeout should: build and push an immutable image tag from merged `main`; use the existing
> Coolify app `eqj5l7k705fhi12x9i74fqf0`; back up the production database before migrations; run
> Alembic explicitly; verify production health; verify OpenAPI route presence for WS-5.1, WS-5.2,
> and WS-5.3; verify missing M2M returns 401; verify configured M2M returns 200 without printing
> secret values; **verify dispatch automation remains disabled**; record the release artifact
> binding for the deployed image; record the deployment observation for the deployed image; run the
> verifier on the generated post-deploy verification unit; record closeout evidence in SDS docs.
>
> Closeout must not merge PRs, enable dispatch automation, or make Coolify, GitHub, CI, trackers,
> workers, verifiers, or production observation canonical lifecycle truth.

The bold item stopped being true when SDS 1.1 retired the `post-deploy-dispatch` criterion:
dispatch is a standing setting (`ORCHESTRATOR_DISPATCH_ENABLED`, an off switch only) and
production runs with it enabled.
