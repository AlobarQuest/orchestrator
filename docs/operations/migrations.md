# Migration operations

Migrations are an explicit operator action, separate from application startup. This page is
the production recipe as measured on 2026-09-19 (two migrations, `0034→0035` and `0035→0036`,
both applied this way). It is written from CLAUDE.md's invariants; read those for the history.

## The image does not migrate itself

The orchestrator's `Dockerfile` ends at a bare
`CMD ["uvicorn", "orchestrator.main:app", ...]`. There is no entrypoint script and no `alembic`
call anywhere in it, so **swapping the running image applies no migration**. The web process
never applies migrations, locally or in production.

change-manager is the opposite: its `entrypoint.sh` runs `alembic upgrade head` at container
start. Do not carry its "no manual migrate step" habit over to this repository. An older plan
here (`docs/superpowers/plans/2026-07-27-wsp27-inc2-inbound-reconciliation.md`) says to swap
first and then migrate inside the new container; that describes change-manager, not this image.

## The order is build, then migrate, then swap

"Migrate first" cannot mean migrate before building: the new migration exists only in the new
image, and the running container carries the old code. So:

1. **Build.** Run the `Release image` workflow (`.github/workflows/release-image.yml`) with the
   full 40-character commit sha in its `ref` input. It builds and pushes to GHCR and **deploys
   nothing**. Coolify only ever pulls a prebuilt tag (`build_pack: dockerimage`).
2. **Record the outgoing tag and digest**, then point the Coolify application at the new tag
   (infraops `coolify_update_application`) without deploying yet. Doing the tag write now makes
   the swap in step 4 a single call.
3. **Migrate from the new image**, on the `coolify` Docker network, on the VPS. Read the database
   URL from the running container's own environment inside the VPS and never print it:

   ```bash
   DB=$(docker inspect <running-container> \
     --format '{{range .Config.Env}}{{println .}}{{end}}' \
     | grep '^ORCHESTRATOR_DATABASE_URL=' | cut -d= -f2-)
   docker run --rm --network coolify -e ORCHESTRATOR_DATABASE_URL="$DB" <new-image> \
     sh -c 'cd /app && .venv/bin/alembic upgrade head'
   ```

4. **Swap** (infraops `coolify_deploy`). Keep the gap between steps 3 and 4 short.
5. **Verify the database, not only the process.** Health, digest, revision label and served
   schema cannot see a database that is behind the code. Ask the container:

   ```bash
   docker exec <new-container> sh -c 'cd /app && .venv/bin/alembic current; .venv/bin/alembic heads'
   ```

   `current` must equal `heads`, and `/health/ready` must return 200.

## The drift window between migrate and swap

After step 3 the still-running old container reports `/health/ready` 503 `migration_drift`,
because the readiness probe compares the code's expected head with the database's.
`/health/live` stays 200 and traffic keeps flowing. That is survivable only because neither
health check consults `/health/ready`: Coolify's own check is disabled
(`health_check_enabled: false`) and the Dockerfile `HEALTHCHECK` probes `/health/live`. **If
either is ever pointed at `/health/ready`, migrate-first becomes an outage**, so do not change
the health checks without re-deciding this order.

## Two other things the swap does

- **It is not zero-downtime.** With Coolify's health check disabled there is no readiness signal
  to gate removal of the old container. Measured 2026-09-01: roughly 20 seconds of
  `no available server` at the proxy before the new container served. Expect it; do not diagnose
  it.
- **It restarts the orchestrator.** Never swap while a dispatched run is live: the runner calls
  back at the end of its run, and a restart then strands the unit with its attempt spent.

## Authoring a migration

- Revision ids must be 32 characters or fewer (`alembic_version.version_num` is `varchar(32)`).
  A longer id fails only at runtime, when the row is stamped.
- Before an upgrade, compare the database revision with the repository head:

  ```bash
  uv run alembic current
  uv run alembic heads
  uv run alembic upgrade head
  uv run alembic current
  ```

- Local and test databases are disposable PostgreSQL 16 databases. A build worktree uses its own
  test database, because the test fixtures drop and recreate whatever `TEST_DATABASE_URL` names.

## Who runs it

The Coolify steps go through the infraops MCP (`coolify_update_application`, `coolify_deploy`);
the Docker commands on the VPS go through `vps_exec`. Pointing Coolify at a new tag is an
operator mechanic, not a human approval gate: the workflow just does not do it for you. Take a
backup of the production database before a migration that rewrites or drops data.
