# WS-6.1 Production Closeout Attempt Evidence

**Date:** 2026-07-09
**Repo:** `AlobarQuest/orchestrator`
**Merged source:** `102e7c660072988a787f3f2d062edcaeb5e418ad`
**Status:** Routes and migration deployed; final closeout blocked by
event-publication runtime packaging defect.

## Completed

- Backup completed before migration: restic snapshot `d4da00e0`.
- Built and pushed amd64 image
  `ghcr.io/alobarquest/orchestrator:102e7c6-ws61-closeout-amd64`.
- Image digest:
  `sha256:49a855b02b94bb06b27b0d2d719251705a347835ddbef252fd947d60a1bd5fa9`.
- Coolify app `eqj5l7k705fhi12x9i74fqf0` deployed as
  deployment `fiq9i6rz6k850cfzyw6dk7h6`.
- Running container used image tag
  `ghcr.io/alobarquest/orchestrator:102e7c6-ws61-closeout-amd64`.
- Alembic upgraded from `0011_ws53_deploy_obs` to
  `0012_ws61_observations`.
- Production `/health/live` and `/health/ready` returned 200.
- Production OpenAPI exposes `GET/POST /api/v1/observations`.
- Missing M2M returned 401; durable M2M with credential key
  `factory-runner-github` returned 200 for authenticated reads.

## Closeout Record

Recorded bounded production observation:

- observation ID: `a89d0d76-8989-4657-a57b-de9a9f461da8`;
- event ID: `467dafed-dfc8-4a0a-bb4b-b8c714915a6b`;
- normalized fact hash:
  `sha256:60a6a27732d95bdef4f00b18aeb8d5523bf4dae30271dd8e50a9951eb1559aa1`;
- status: `healthy`.

## Blocker

`POST /api/v1/event-publications/queue` for event
`467dafed-dfc8-4a0a-bb4b-b8c714915a6b` returned HTTP 500. Production logs show
`ModuleNotFoundError: No module named 'factory_events'`.

The fix is branch `codex/ws61-closeout-runtime-event-publications`. It makes the
production runtime image copy `agent_registry`, `factory_events`, schema files,
and agent identities from the digest-checked registry artifact. No private
GitHub dependency or new credential is introduced.

Final closeout requires Devon to merge the fix, then a second bounded deploy
with a fresh immutable amd64 image and a successful event-publication queue
check.
