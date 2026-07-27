# WS-P3.0 — First Observation Producer: the drift digest

**Date:** 2026-07-27
**Repos:** `AlobarQuest/infraops-mcp-server` (the producer change) + `AlobarQuest/orchestrator` (no code change; one credential/env change)
**Program:** Phase-3 input-layer plan (`~/docs/software-delivery-system/2026-07-27-program-phase3-input-layer-plan.md`), WS-P3.0 — the one Wave-2-resident workstream, scheduled after WS-P2.7 Inc 2 and before WS-P2.8.
**Backlog item closed:** PROJECT.md P2, "the WS-P2.6 traceability query observation tail is plumbing-complete but empty in prod" (added 2026-07-26).

## 1. Problem

The orchestrator's observation spine (`POST /api/v1/observations`, WS-6.1) is the purpose-built
ingestion surface for external reality. Its vocabulary already names `drift_digest` and `drift`.
It has **zero external producers**. Consequently "why is this in production?" cannot show an
external-reality tail, and the WS-P2.8 follow-up-scheduling spec would have to be written against
imagined data.

This workstream wires the first producer: the daily infra-drift audit, which already writes a
structured machine-readable digest and already posts it, under an M2M credential, to
change-manager.

**Success is narrow on purpose:** observations only. No work units, no new collectors, no tracker
sources, and no change to the drift loop's existing outputs.

## 2. Baseline (verified 2026-07-27, not assumed)

| Claim | Evidence |
|---|---|
| Production serves the route | `GET https://sds.alobar.net/openapi.json` → 57 paths, including `POST`/`GET /api/v1/observations`. No orchestrator deploy is required. |
| The tail has no producer | `GET /api/v1/observations` (SYSTEM bearer) returns **2** rows, both dated 2026-07-09, `recorded_by` = `ws61-closeout-system` and `claude-code-interactive` — hand-posted closeout artifacts. The backlog item's word "empty" is imprecise; the substance (zero external producers) holds. |
| `drift-reconciler` is in the baked bundle | `git show 65655ddf:registry/agents/drift-reconciler.yaml` exists; `registry/agents/` holds 13 files at the pinned revision. `runtime: node-executor`, so it passes `_m2m_credentials` validation (`main.py:141`). **No image rebuild is needed to attribute observations to it.** |
| Authorization is role-only | `ActorContext` is `(actor_id, role)`; `_authorize_actor` (`services/observations.py`) gates on `role is ActorRole.SYSTEM`. A dedicated credential therefore buys attribution and independent revocability, **not** least privilege. |
| The producer's existing POST is TypeScript, not curl | `drift-audit.sh:95-98` shells out to `dist/cli/change-mgr-cli.js`; the HTTP call is `fetch` in `src/change-manager/api-client.ts:49-67`. |
| Per-instance facts already exist upstream | `instances.<key>.summary` in the report is already exactly `{total_proposals, by_risk, by_kind}` (`src/standards/report.ts:6-15`). |

## 3. Decisions

### 3.1 Credential: mint a dedicated reporter (decided by Devon, 2026-07-27)

A new credential key id `orchestrator-drift-reporter` maps to registry actor **`drift-reconciler`**
with role `system`.

Rejected: reusing the standing `orchestrator-system` credential. It is free, but `recorded_by` is
written once and is permanent — one existing production row is stamped `claude-code-interactive`
for exactly this reason — and it would put a token that also drives `commands/ready` and dispatch
on a daily unattended cron.

Understood cost and limit: the new credential still holds role `system`, because the route requires
it. This is an attribution and revocability decision, not a least-privilege one, and the spec says
so rather than implying a narrower grant than exists.

### 3.2 Granularity: one observation per instance per day (decided by Devon, 2026-07-27)

Exactly two rows per day (`prod`, `dev`), emitted **even on a clean day**, so the tail never goes
silent. This is the "append-shaped digest" the Phase-3 plan names, and it is deliberately chosen
over a per-drifting-application shape: per-app rows are the higher-value long-term form, but a clean
day would emit nothing, and day-over-day changes to the same app's finding is precisely the case
that requires **observation supersession** — which the Phase-3 plan lists as a prerequisite that
does not exist yet.

### 3.3 The producer is a TypeScript CLI, not shell + curl

It mirrors the proven change-manager module in the same repo, and lands the logic where that repo's
tests actually live (40+ vitest files; zero shell tests).

## 4. Architecture

New, in `infraops-mcp-server`:

- `src/orchestrator/api-client.ts` — `fetch` wrapper, sibling of `src/change-manager/api-client.ts`.
  Sends `Authorization: Bearer <token>` **and** `X-Credential-Key-Id: <key-id>`; both are required by
  every orchestrator M2M route.
- `src/orchestrator/observation.ts` — the normalizer. The TypeScript analogue of the orchestrator's
  `src/reconciliation_runner/facts.py`, and it carries the same "two halves that must ship together"
  warning about content-addressing and upstream timestamps (§5.3).
- `src/cli/orchestrator-cli.ts` — `observe --report-dir <dir> --now <iso> [--dry-run]`, sibling of
  `change-mgr-cli.ts`, with the same env guard that throws when the base URL or token is absent.

Changed:

- `scripts/drift-audit.sh` — **one added block**, after the change-mgr sync and before the
  security-drift step, in the established non-fatal idiom:

  ```bash
  export ORCHESTRATOR_API_BASE="${ORCHESTRATOR_API_BASE:-https://sds.alobar.net}"
  # <new-uuid>: the BWS secret id minted in §6; the literal is filled in at implementation time.
  export ORCHESTRATOR_M2M_TOKEN="$(get_secret_by_id "${BWS_ORCHESTRATOR_OBS_SECRET_ID:-<new-uuid>}")"
  export ORCHESTRATOR_CREDENTIAL_KEY_ID="${ORCHESTRATOR_CREDENTIAL_KEY_ID:-orchestrator-drift-reporter}"
  node "$REPO/dist/cli/orchestrator-cli.js" observe --report-dir "$REPORT_DIR" --now "$NOW" >>"$LOG_FILE" 2>&1 \
    && log "orchestrator observation ok" || log "WARN: orchestrator observation failed (non-fatal)"
  ```

It touches neither `RC` nor `RC_REMEDIATE`, so the script's exit code, the Healthchecks ping, the
Resend email digest, the change-manager sync and the security-drift step are all unchanged.

**Failure is non-fatal but never silent.** The CLI attempts both instances independently — one
instance failing never suppresses the other's row — and always prints `posted=N deduped=N failed=N`
to the drift log, in addition to the shell's `WARN` line. A reporting obligation that can be
skipped without a trace is the WS-P2.15 defect class; a counted, greppable log line on every run is
the countermeasure proportionate to a two-row-per-day producer.

**The push-only invariant is preserved for free.** The producer lives entirely outside the
orchestrator repository, so no `OUTBOUND_ALLOWLIST` entry is required and no orchestrator source
file changes. The orchestrator does not poll for drift reports.

## 5. The payload contract

### 5.1 Fields

One command per instance per report:

| field | value |
|---|---|
| `idempotency_key` | `drift-digest:<generated_at>:<instance>` (schema max 200 chars) |
| `expected_version` | `0` — required by the schema, and the service rejects anything but `0`/null |
| `source_system` | `drift_digest` |
| `source_reference` | `infra-drift:<generated_at>:<instance>:<facts_digest>` — where `facts_digest` is the first 12 hex chars of `sha256` over the facts object serialised as canonical JSON (keys sorted, compact separators), matching `fact_digest()` in `reconciliation_runner/facts.py` |
| `source_url` | `null` — the report is a local file; there is no stable HTTPS URL for it |
| `trust_classification` | `monitor` |
| `subject_type` | `service` |
| `subject_reference` | `coolify:prod` \| `coolify:dev` |
| `environment` | `production` \| `development` |
| `observation_type` | `drift` |
| `status` | `passed` (reachable, 0 proposals) · `degraded` (reachable, >0) · `unknown` (unreachable) |
| `severity` | `critical` if `by_risk.destructive > 0`; else `warning` if `total_proposals > 0` or the instance is unreachable; else `info` |
| `observed_at` | the report's `generated_at` — **upstream time, never post time** |
| `summary` | e.g. `coolify:prod — 1 standards proposal (1 new)` (max 512) |
| `facts` | see §5.2 |
| `payload_digest` | `null` |

### 5.2 Facts

```json
{
  "report_date": "2026-07-27",
  "instance": "prod",
  "instance_ok": true,
  "total_proposals": 1,
  "by_risk": { "safe": 1, "caution": 0, "destructive": 0 },
  "by_kind": { "remediation": 1, "question": 0 },
  "delta_new": 1,
  "delta_resolved": 0,
  "read_error_count": 0
}
```

`total_proposals`, `by_risk` and `by_kind` are lifted verbatim from `instances.<key>.summary`.
`delta_new` / `delta_resolved` are counts of `delta.new` / `delta.resolved` filtered on
`item.instance === key`. `report_date` is the UTC date component of `generated_at`, which is also
the report file's basename.

**`observed_at` is deliberately absent from `facts`**, unlike `reconciliation_runner`'s
`NormalizedFacts`. It does not need to be there: the reference already embeds the full
`generated_at` literal, so it varies whenever `observed_at` varies, which is the property §5.3
requires. Adding it back would be harmless but redundant — do not "fix" its absence.

Three constraints this shape exists to satisfy:

- **Counts only; never raw text.** An unreachable instance's `error` string and the per-endpoint
  `errors[]` array are exception text from external systems. Only `instance_ok: false` and
  `read_error_count: N` cross the boundary. This is the Phase-3 injection-containment rule, and it
  is why the unreachable case still produces a well-formed row rather than being skipped.
- **Never use rule keys as fact keys.** The ingest secret scanner (`SECRET_KEY_PARTS`,
  `services/observations.py`) rejects any fact key containing `log`, `body`, `instruction`, `token`,
  `credential`, `secret`, `password`, `bearer`, `authorization`, or `api_key`. Infra rule keys
  plausibly contain `log`. If per-rule detail is ever wanted, it goes in a bounded **list of
  objects** with `rule` as a *value*, never as a key.
- **Bounds.** Facts must be a non-empty object, ≤ 4096 bytes encoded, keys ≤ 64 chars, strings
  ≤ 512 chars, lists ≤ 30 items. The shape above is roughly 250 bytes.

`subject_reference` is the logical `coolify:prod`, **not** the instance base URL: those come from
env at runtime and dev's is an OrbStack LAN address, so embedding one in a permanent record would be
both unstable and an internal-address leak.

### 5.3 Dedup and the conflict trap

The orchestrator rejects the same `(source_system, source_reference)` re-recorded with a different
`normalized_fact_hash` as `observation_conflict`, and **`observed_at` is inside that hash**. A
reference that did not vary with `observed_at` would therefore conflict on every subsequent post,
forever. This is the failure `reconciliation_runner/facts.py` documents.

Keying both the idempotency key and the content-addressed reference on the full `generated_at`
gives two distinct, correct behaviours:

- Re-running the **CLI against the same report file** → identical key, identical facts → **dedups**,
  returning the existing observations.
- Re-running the **whole audit** → a new `generated_at` → a **new row**. This is correct: it is a
  new observation of reality, not a duplicate.

The verification drill tests both, separately, so a passing "dedup" claim cannot be satisfied by the
wrong mechanism.

## 6. Credential rollout (orchestrator, production)

Generate a random bearer locally; store the value in BWS; put only `sha256(<bearer>)` into Coolify.
The token value never enters a tracked file, prompt, log, or this document.

Then, in this order, never combined:

1. **`ORCHESTRATOR_M2M_CREDENTIALS`** — merge in
   `"orchestrator-drift-reporter": {"agent_id": "drift-reconciler", "token_hash": "<sha256>"}`.
   Restart. Verify the key is present **from inside the container**.
2. **`ORCHESTRATOR_M2M_ROLES`** — merge in `"orchestrator-drift-reporter": "system"`. Restart.
   Verify.

`main.py` raises when `set(roles) ⊄ set(credentials)` and fails **closed**, so a half-applied
credentials write is a harmless no-op while a half-applied roles write is an outage. There is no
ordering that can strand production, at the cost of one extra restart. Never "save a restart".

Two operational constraints apply to reading and writing those variables: `/envs` API responses
carry `real_value` for every variable, including database URLs with passwords, so the response is
parsed in-process and only key names and hash prefixes are printed — never through an ad-hoc shell
pipeline. And Coolify's env `PATCH` intermittently 500s on this app; the reliable fallback is
delete-by-env-uuid then recreate.

Producer-side secret handling follows the repo's existing three-tier pattern unchanged: the
bootstrap BWS access token comes from the macOS Keychain (`scripts/bws-token.sh`), the reporter
token is fetched at runtime by stable UUID via `get_secret_by_id`, and that UUID is recorded in
`.bws-secrets.toml` by regenerating the manifest. No new entry is needed in
`~/.config/infra-drift/env`; the two non-secret variables default in-script exactly as
`CHANGE_MGR_API_BASE` does.

## 7. Verification

Six runs; two of them are supposed to fail.

1. **Dry run.** `--dry-run` against the current real report: both payloads printed and locally
   validated, nothing posted.
2. **First post.** Two rows visible via `GET /api/v1/observations?source_system=drift_digest`, and
   `recorded_by` reads **`drift-reconciler`** — asserted explicitly, since that field is the entire
   point of §3.1.
3. **Same-report re-run.** Row count unchanged; the same observation ids are returned.
4. **Second report** (the next real daily run, or a synthetic one with a different `generated_at`):
   two more rows, and **no `observation_conflict`**.
5. **Existing outputs unchanged.** `~/Library/Logs/infra-drift.log` still shows the change-mgr sync
   summary and the Resend digest line; Healthchecks still receives its success ping.
6. **Negative path.** With the reporter token blanked, the CLI fails, `drift-audit.sh` logs the
   `WARN` line, and the script's exit code and Healthchecks ping are unaffected — proving the drift
   loop is not hostage to the orchestrator being up.

**Tests** are vitest, in `infraops-mcp-server`. Normalizer: vocabulary values, the 4 KB / 64-char /
512-char bounds, the banned-key-substring rule, all four status/severity branches, the
instance-unreachable path, per-instance delta filtering, and reference stability-versus-variation.
CLI: the missing-env guard, per-instance fail-open counting, and `--dry-run`. Plus `shellcheck`
clean on the added block. No orchestrator-side tests, because no orchestrator source file changes.

## 8. Boundaries (what this explicitly does not do)

- **No work units, ever, from this path.** The observation→work bridge is WS-P2.8; Phase-3 proposal
  lanes are post-Wave-3.
- **No polling by the orchestrator.** The script pushes.
- **No new vocabulary.** `drift_digest` and `drift` already exist. If a payload field appears to
  demand a vocabulary change, the payload is wrong, not the vocabulary.
- **No change to the drift loop's existing behaviour.** Additive POST only.
- **No observation supersession.** Out of scope, and the append-shaped design in §3.2 is what makes
  that acceptable.

## 9. Scoped-out, stated rather than overclaimed

- **These rows will not appear in any traceability chain.** `services/traceability.py:216` filters
  the observation hop on `subject_type="work_unit"` and the unit id. A drift observation on a
  `service` subject lands in `GET /api/v1/observations` but not in a chain. The backlog item is
  therefore closed on "the generic tail carries real producer data, and WS-P2.8 has live input to be
  specified against" — **not** on "exit criterion #6's observation node is exercised end to end".
  Closing that gap needs per-unit observations or a traceability change; neither belongs here.
- **`delta.unchanged` has no per-instance breakdown** in the report (it is a report-level scalar), so
  per-instance facts carry `delta_new` and `delta_resolved` only, rather than fabricating a split.

## 10. Rollback

Delete the added block from `drift-audit.sh`, or blank `ORCHESTRATOR_M2M_TOKEN`. Nothing in the
drift loop or the orchestrator depends on the observations being posted. The credential can be
retired by removing its entries from `ORCHESTRATOR_M2M_ROLES` first, then
`ORCHESTRATOR_M2M_CREDENTIALS` — the reverse of §6, for the same fail-closed reason.

## 11. Definition of done

- Credential decision recorded with the bundle-membership check shown (§2, §3.1).
- The daily drift run posts normalized, deduped, non-fatal-on-failure observations to production.
- All six verification runs evidenced (§7), including the two negative ones.
- Existing drift outputs demonstrably untouched.
- PROJECT.md backlog item closed with the evidence and the §9 caveat.
- Closeout note written to `~/docs/software-delivery-system/`.

## 12. Follow-ups discovered, not fixed here

- `drift-audit.sh:62` consumes BWS secret `68733abe-682a-4597-b88f-b4750189a56a`
  (`APPBRAIN_ACCESS_KEY`) which is **absent from `.bws-secrets.toml`** — a manifest drift predating
  this workstream. Backlog it against infraops-mcp-server rather than fixing it in a drift-digest
  change.
- Per-application drift observations (§3.2) and observation supersession are the natural
  follow-on once WS-P2.8 exists.
