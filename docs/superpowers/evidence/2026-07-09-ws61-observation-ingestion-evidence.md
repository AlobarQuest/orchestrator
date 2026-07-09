# WS-6.1 Observation Ingestion Evidence

**Date:** 2026-07-09
**Owning repo:** `AlobarQuest/orchestrator`
**Branch:** `codex/ws61-observation-ingestion`
**PR:** `https://github.com/AlobarQuest/orchestrator/pull/23`
**Status:** Implementation merged to `main` as `102e7c660072988a787f3f2d062edcaeb5e418ad`.
Production deployment and migration were performed on 2026-07-09, but closeout
found a runtime packaging defect in event-publication queueing. The fix is in
branch `codex/ws61-closeout-runtime-event-publications`; Devon merge is required
before the final WS-6.1 closeout redeploy.

## Implemented Surface

WS-6.1 adds the smallest orchestrator-owned observation ingestion layer:

- migration `0012_ws61_observations`;
- model/table `observations`;
- service `src/orchestrator/services/observations.py`;
- routes:
  - `POST /api/v1/observations`;
  - `GET /api/v1/observations`;
- local event `observation.recorded`;
- `factory-event/v1` publication mapping `orchestrator.observation_recorded`;
- operations doc `docs/operations/observation-ingestion.md`;
- WS-6.2 handoff prompt `docs/superpowers/plans/2026-07-09-ws62-governed-promotion-handoff-prompt.md`.

## Accepted Sources And Subjects

Accepted source systems are allowlisted:

- `deployment_observation`;
- `watchtower`;
- `ops_dashboard`;
- `healthchecks`;
- `uptime_monitor`;
- `github`;
- `drift_digest`.

Supported subject types are:

- `service`;
- `repo`;
- `deployment`;
- `release_binding`;
- `deployment_observation`;
- `work_unit`;
- `package_revision`;
- `endpoint`;
- `monitor`;
- `external_run`.

## Bounded Fact Rules

Observation records store only bounded facts and provenance:

- source system/reference and optional stable HTTPS source URL;
- trust classification;
- subject type/reference;
- optional environment;
- observation type/status/severity from allowlists;
- observed-at and received-at timestamps;
- small summary;
- bounded normalized fact object;
- normalized fact hash;
- optional payload digest;
- recorded-by actor;
- local event ID.

The implementation rejects raw or high-risk payload shapes: secret-like keys or
values, auth headers, raw logs, response bodies, PR/issue/tracker/email text,
unbounded payloads, and instruction-shaped external text. Normalized fact JSON is
size bounded; string values, keys, and lists are bounded; payload digests must be
`sha256:` values.

## Idempotency And Conflict Handling

WS-6.1 is append-only and has no update/delete/supersession route.

- Same idempotency key and same command returns the original row.
- Same `(source_system, source_reference, normalized_fact_hash)` returns the
  original row even with a different idempotency key.
- Same `(source_system, source_reference)` with different normalized facts is
  rejected as `observation_conflict`.
- Failed ingestion does not invent a successful observation and does not mutate
  lifecycle state.

## Boundary Confirmation

WS-6.1 does not:

- promote brain knowledge;
- create lessons, rules, or promotion proposals;
- write to any brain;
- create follow-up work units;
- canonicalize trackers, monitors, CI, GitHub, Healthchecks, uptime monitors,
  watchtower, ops-dashboard, Coolify, drift digest, or deployment tooling;
- merge pull requests;
- deploy artifacts;
- enable dispatch automation;
- add polling or background collectors;
- add secrets, runtime credentials, BWS manifest entries, env files, or GitHub
  Actions secrets.

## Verification

Original local verification on the PR branch:

```text
SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check
773 passed
```

Security scan:

```text
PYTHONPATH="$HOME/Projects/security-standards/src" .venv/bin/python -m security_scan.cli . --category security
0 BLOCK, 0 WARN, 1 INFO
```

Formatting and standards:

```text
git diff --check
clean

/Users/devon/Developer/code-standards/.venv/bin/code-standards check
passed
```

GitHub PR state before final docs update:

```text
PR #23 draft: true
mergeStateStatus: CLEAN
Quality checks: SUCCESS
```

After Devon requested documentation updates, the PR was updated with this
evidence document, the expanded WS-6.2 handoff prompt, the `CLAUDE.md` local
instruction convention, and SDS masterplan updates.

## Production Closeout Attempt

Production closeout was explicitly requested on 2026-07-09 after Devon merged
PR #23.

- Pre-deploy backup: `/Users/devon/Projects/vps-backup/backup.sh`;
- restic snapshot: `d4da00e0`;
- built/pushed amd64 image:
  `ghcr.io/alobarquest/orchestrator:102e7c6-ws61-closeout-amd64`;
- image digest:
  `sha256:49a855b02b94bb06b27b0d2d719251705a347835ddbef252fd947d60a1bd5fa9`;
- Coolify app: `eqj5l7k705fhi12x9i74fqf0`;
- Coolify deployment UUID: `fiq9i6rz6k850cfzyw6dk7h6`;
- production Alembic upgraded from `0011_ws53_deploy_obs` to
  `0012_ws61_observations`;
- production `/health/live` and `/health/ready` returned 200;
- production OpenAPI exposes `GET/POST /api/v1/observations`;
- missing M2M returned 401; durable M2M with credential key
  `factory-runner-github` returned 200 for authenticated reads;
- no new secret, runtime credential, BWS manifest entry, env file, collector,
  polling loop, lifecycle mutation, brain write, automatic merge, or automatic
  deployment was added.

Closeout observation recorded in production:

- observation ID: `a89d0d76-8989-4657-a57b-de9a9f461da8`;
- local event ID: `467dafed-dfc8-4a0a-bb4b-b8c714915a6b`;
- normalized fact hash:
  `sha256:60a6a27732d95bdef4f00b18aeb8d5523bf4dae30271dd8e50a9951eb1559aa1`;
- status: `healthy`.

The event-publication queue check for that observation event returned HTTP 500.
Production logs showed:

```text
ModuleNotFoundError: No module named 'factory_events'
```

Root cause: local tests and development resolve `factory_events` and
`agent_registry` from the sibling `~/Projects/security-standards/src`, but the
production runtime image only copied the orchestrator virtualenv and generated
registry bundle. The fix branch makes the Docker runtime copy the
security-standards helper modules, schema, and agent identities from the
digest-checked registry artifact, without adding private GitHub build
credentials.

Fix-branch verification:

```text
SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check
775 passed

PYTHONPATH="$HOME/Projects/security-standards/src" .venv/bin/python -m security_scan.cli . --category security
0 BLOCK, 0 WARN, 1 INFO

docker buildx build --platform linux/amd64 ... --load
passed

docker run --rm -i --platform linux/amd64 orchestrator:ws61-event-publications-runtime-fix python -
factory-event/v1
True
```

Do not treat WS-6.1 production closeout as complete until this packaging fix is
merged, a new immutable amd64 image is deployed, and the production
event-publication queue check for event
`467dafed-dfc8-4a0a-bb4b-b8c714915a6b` returns a pending
`orchestrator.observation_recorded` publication instead of HTTP 500.
