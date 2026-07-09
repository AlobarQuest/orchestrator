# WS-6.1 Observation Ingestion Evidence

**Date:** 2026-07-09
**Owning repo:** `AlobarQuest/orchestrator`
**Branch:** `codex/ws61-observation-ingestion`
**PR:** `https://github.com/AlobarQuest/orchestrator/pull/23`
**Status:** Implementation complete and PR-ready pending Devon merge. Not deployed to production.

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

Local verification on the PR branch:

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

## Production Boundary

No production deployment was performed for WS-6.1 in this implementation
session. Production remains on the Phase 5 closeout image and Alembic head until
Devon merges PR #23 and a separate closeout deploy is explicitly performed.
