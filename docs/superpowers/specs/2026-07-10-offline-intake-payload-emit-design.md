# Offline intake-payload emit — design

**Date:** 2026-07-10
**Status:** approved (design)
**Scope:** one CLI subcommand. No server, auth, or proxy change.

## Problem

`POST /api/v1/package-intakes` is human-actor-only (`_require_human`,
`services/package_intake.py:65,178`). In production it is reachable only through the
`orchestrator-intake-human` Traefik router, which applies the Alobar ID forward-auth
chain — so a caller must present as a browser-authenticated human, and
`get_actor` (`api/dependencies.py:60`) rejects any request that carries *both* an
M2M bearer and forward-auth headers.

But intake also depends on **machine-local verification** the CLI performs via
`load_package_intake_payload` (recompute the package content hash against
`~/Projects/intent-packages`, `verify-approval`, walk the factory-events chain).
Critically, the server does **not** re-verify: `register_package_intake` requires
`verification_mode == "caller_attested_cli_verified"` and stores `content_hash` /
`approval_event_id` / `approval_ledger_commit` as *attested* values. **The local
verification is therefore load-bearing, not a pre-flight.**

These two facts conflict: the existing `intake-package` CLI speaks M2M only
(`request()`, `cli.py:70-82`) and is rejected by the human endpoint; a browser can
present as human but cannot run the local verification.

## Goal

Let a human intake an approved package into production while keeping the load-bearing
local verification in the CLI, without adding any credential handling to the CLI.

## Approach (chosen)

Split the two responsibilities across the tools that can each satisfy one of them:

- **CLI does the verification and emits the attested request body** — offline, no
  network, no auth.
- **The human POSTs that body from a browser tab already logged in to
  `sds.alobar.net`** — the native session cookie makes the Authentik outpost
  authorize the request; Traefik injects `X-Alobar-Proxy` + `X-authentik-email`;
  `authenticate_human` mints the HUMAN actor. This is exactly the WS-6.3 governed-
  promotion pattern.

Rejected alternatives: a CLI "cookie mode" (adds live-session-credential handling to
the CLI and rests on the unverified assumption that the outpost authorizes a
non-browser client); a one-off manual hack (becomes the de facto tool).

## The command

New subcommand `emit-intake-payload`, mirroring `intake-package`'s inputs:

```
orchestrator emit-intake-payload <path> \
    --source-repository <repo> \
    --idempotency-key <key> \
    [--out <file>]
```

Behavior:

1. Call `load_package_intake_payload(path, source_repository=...)` — same call
   `intake-package` makes via `_load_intake_payload`. All local verification runs
   here; a `PackageSourceError` surfaces as a `CliError` with a non-zero exit, so a
   bad or unverifiable package fails **before** any browser step.
2. Build the identical body `intake-package` would POST
   (`cli.py:231-235`): the loaded payload merged with
   `{"idempotency_key": <key>, "expected_version": 0}`.
3. Emit that body as deterministic JSON (sorted keys, matching the repo's existing
   `--json` convention) to **stdout** by default, or to `--out <file>` when given.
   Nothing else is written.

The command never constructs `request()`, never reads `ORCHESTRATOR_API_TOKEN`, and
makes no network call. It does require the same local package sources
`intake-package` needs today (`~/Projects/intent-packages`, `~/.factory/events.jsonl`,
`~/Projects/security-standards`).

## Operator workflow

1. `orchestrator emit-intake-payload <package> --source-repository <repo>
   --idempotency-key <key> --out /tmp/ws-p2.1-intake.json`
2. Open a browser tab already authenticated to `sds.alobar.net`, open devtools.
3. Wrap the emitted JSON in a `fetch('/api/v1/package-intakes', {method:'POST',
   headers:{'Content-Type':'application/json'}, body: <json>})` — the same fetch used
   for the WS-6.3 promotion. **Retry once on a 401**: the first same-origin POST
   after login can 401 (the fetch follows the forward-auth 302 and degrades to GET);
   the immediate retry succeeds (orchestrator `CLAUDE.md`; WS-6.3 evidence).
4. Success returns the `revision_id`. Because the body carries the idempotency key, a
   re-run after an ambiguous first attempt safely replays to the same revision
   (`_intake_replay`) rather than double-registering.

## Interfaces & dependencies

- Depends on the existing `load_package_intake_payload` (`package_sources.py`) and the
  existing `CliError` / typer patterns in `cli.py`. No new module.
- Produces a request body identical in shape to the current `intake-package` POST —
  the contract is "the body `POST /api/v1/package-intakes` already accepts," so this
  cannot drift from the server independently of `intake-package`.

## Testing

- **Payload parity:** for the same `(path, source_repository, idempotency_key)`,
  `emit-intake-payload` produces a body byte-identical to what `intake_package`
  constructs. Assert against the real construction path, not a hand-copied dict, so
  the two stay coupled.
- **Verification failure propagates:** a package that fails
  `load_package_intake_payload` yields a non-zero exit and emits no payload (neither
  to stdout nor to `--out`).
- **Deterministic output:** emitted JSON has sorted keys and re-emits identically.
- **End-to-end proof:** the actual WS-P2.1 intake from the browser (the real
  acceptance test; not automatable here).

## Security

- Zero new credential surface in the CLI: no token read, no session/cookie handled,
  no network call. The live session stays in the operator's browser.
- The emitted payload contains package metadata and attestation hashes
  (`content_hash`, `approval_event_id`, `approval_ledger_commit`, `approved_by`) — no
  secrets/tokens — so it is safe to write to a file and inspect. No BWS-hook concern.

## Out of scope (YAGNI)

- No CLI cookie/session mode.
- No change to the authority-approval flow — those are browser fetches with no local
  verification, so they need no emit.
- No server, proxy, or `intake-package` change (`intake-package` remains the M2M
  poster for non-production / M2M contexts).

## Risks / unknowns

- The browser-POST leg is proven (WS-6.3 used the identical router + fetch pattern),
  so the only residual is operator ergonomics (wrapping the fetch, the first-401
  retry) — documented above, not a code risk.
