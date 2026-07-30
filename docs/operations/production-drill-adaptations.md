# Production drill adaptations (ADR-0005 prerequisite 3)

The five recovery drills in `docs/operations/recovery-drills.md` run against a throwaway Postgres,
a throwaway uvicorn, and credentials minted per run. ADR-0005 (disposition A) requires them to run
against `sds.alobar.net`. This document is the per-drill production variant, written for Devon's
review **before** anything mutates production.

Status: **awaiting review.** Nothing in here has been executed.

Every claim below was verified against production on 2026-07-27, not inferred from the repository.
Evidence: `scripts/` (drill sources), the live OpenAPI document, the Traefik dynamic config at
`/data/coolify/proxy/dynamic/orchestrator.yaml`, and the running container's environment.

---

## 1. Five harness deltas that apply to every drill

The local harness (`scripts/drill_common.sh`) does five things that have no production equivalent.
Each needs a substitute, and the substitutes are the same for all five drills.

### 1.1 Seeding — `seed_unit` is unreachable in production

`seed_unit` registers a revision and a unit through `POST /api/v1/revisions` and
`POST /api/v1/revisions/{id}/work-units`. Both call `_require_human` (`services/packages.py:184`,
`:282`), and **neither has a forward-auth router**: the Traefik config gives dedicated
human routers only to `/api/v1/package-intakes` (exact path, POST),
`^/api/v1/work-units/{uuid}/approvals$`, `^/api/v1/work-units/{uuid}/retry-authorization$`, the
knowledge-promotion POSTs, and `/review`. Everything else under `/api` is the M2M-only
`orchestrator-api` router, which strips `X-authentik-*`.

So a browser `fetch` to `/api/v1/revisions` gets 401 (no identity survives the strip), and the
SYSTEM bearer gets `_require_human` rejection. The route is unreachable by anybody.

**Substitute — the real intake path** (ADR prerequisite 2 asks for exactly this):

1. Browser `fetch` → `POST /api/v1/package-intakes` (forward-auth router). Expect a clean 201;
   do not build a retry for a "first POST 401", which has never been observed.
2. SYSTEM bearer → `POST /api/v1/package-intakes/{revision_id}/decomposition-proposals`.
3. `/review/decomposition-proposals/{proposal_id}` GUI → approve. The response carries
   `created_work_unit_ids` — this is where the drill units are born, in `DRAFT`.
4. Per unit, the `/review/units/{id}` page → "Approve this authority envelope"
   (`POST /review/units/{id}/authority-approval`, `subject_type="authority"`).
5. SYSTEM bearer → `POST /api/v1/work-units/{id}/commands/ready`.

**Vocabulary trap** (CLAUDE.md, confirmed in the live schemas): `PackageAcceptanceCriterionResponse`
carries **both** `id` (DB UUID) and `ac_id` (the human string). `ac_mappings[].ac_id` on the
decomposition proposal wants the **UUID**; evidence and adjudication want the **string**. Read the
intake back with `GET /api/v1/package-intakes/{revision_id}` and map them explicitly.

### 1.2 Credentials — three roles, from BWS, by UUID

| Role | Credential key id | BWS UUID |
|---|---|---|
| system | `orchestrator-system` | `221a48d5-3f29-4898-b300-b4820140c880` |
| verifier | `orchestrator-verifier` | `660d5846-abcb-4751-be86-b483012899eb` |
| worker | `factory-runner-github` | `d2a4c0fc-128b-4bf5-8e25-b481010e1be0` |

Source `BWS_ACCESS_TOKEN` by **sourcing** `~/Projects/orchestrator/scripts/sds-token.sh` (it exports;
it does not echo). Every M2M call sends `Authorization: Bearer` **and** `X-Credential-Key-Id`. Never
echo a token; never `bash -x` the helper.

> Changed 2026-07-30: this was `~/Projects/vps-backup/bws-token.sh`, which is now **DENIED** on these
> secrets. They moved to the read-only `SDS Operator` BWS project. No value was rotated and the UUIDs
> are unchanged — only the bootstrap identity differs. The `worker` row above
> (`factory-runner-github`, `d2a4c0fc-…`) did **not** move and is still readable with the old helper.

**How the role is actually decided** — not from the bundle. `authenticate_m2m`
(`identity/auth.py:54`) returns `role=ActorRole.WORKER` for **every** M2M credential; the bundle
supplies only `actor_id`, `authority_profile` and `version`, and rejects human identities. The sole
promotion mechanism is `m2m_roles.get(credential_key_id, role)` (`api/dependencies.py:83`).

`ORCHESTRATOR_M2M_ROLES` is `{"orchestrator-system":"system","orchestrator-verifier":"verifier"}` —
there is **no worker entry**, so `factory-runner-github` stays at the worker default. Worker is not
what that credential *is*; it is what every unpromoted credential falls to.

**Attribution — accepted knowingly, with the alternatives considered.** Every drill worker event is
attributed to the `factory-runner` actor permanently: `claims.claimed_by`, `event.actor_id`, and the
status-ledger `actor_id` all record it, in an append-only ledger.

What this does **not** cost: the failure-signature circuit breaker groups by
`(work_unit_id, failure_signature)` (`dead_letter.py:189`), **not** by actor, so drill failures
cannot open a circuit that blocks real runner dispatches on other units.

What it does cost: drill worker actions are indistinguishable **by actor** from real runner work,
and the `actor_id`-keyed ownership/replay checks (`claims.py:614`, `:664`, `:748`) share a replay
surface with the real runner (theoretical — drill idempotency keys are prefixed).

Alternatives considered and rejected:

- **A temporary `orchestrator-drill-worker` credential bound to `open-engine-runner`** — that actor
  is in the baked bundle and backed by no credential, one-to-one validation holds, and it needs no
  image rebuild. Rejected: it *borrows* an unrelated identity, which CLAUDE.md warns against by
  name. The credential would be temporary; the events it writes are permanent.
- **A purpose-built `drill-runner` actor in security-standards** — correct, but it requires a merged
  commit plus an image rebuild, which changes the running artifact and **invalidates the
  identity proof in §5**. Too large a prerequisite for a labeling improvement.

`factory-runner` is kept because it is *truthful* — the drill's worker steps are genuinely
runner-role work — and because the discriminating label already exists one level down: drill units
live in package `drill-2026-07-27` with drill-prefixed unit keys, and every event binds to a work
unit, so "real vs drill" is a join the status-ledger and traceability surfaces already support.

**Run-time check this obliges:** before starting, confirm no `factory-runner` workflow run is active
or manually triggered for the drill window, so drill and real runner activity cannot interleave
under one identity.

The local `human` role forges `X-Alobar-Proxy` from a trusted-proxy allowlist. In production the
marker is injected by Traefik and the identity comes from Authentik. **Human steps are browser
steps.** They cannot be scripted with curl.

### 1.3 Readers — every `scratch_sql` assertion becomes an API read

| Local SQL | Production surface |
|---|---|
| `unit_version` | Thread it: `TransitionResponse` returns `version` on every command. Fallback `GET /api/v1/in-flight-units` → `units[].version` (non-terminal only). |
| `unit_state` | `GET /api/v1/status-ledger?work_unit_id=…&include_inactive=true` → `unit_state` |
| `unit_attempts` | `GET /api/v1/in-flight-units` → `units[].attempt_count` |
| claims: open / released / `terminal_reason` | `GET /api/v1/work-units/{id}/evidence-pack` → `claims[]`. Released ⟺ `terminal_reason` non-null. |
| evidence heads, `supersedes_evidence_id` | evidence-pack → `evidence[]` `{id, supersedes, current}`. `current == true` **is** the unsuperseded-head flag. |
| `adjudications` count | evidence-pack → `adjudications[]` |
| `reconciliation_conditions` by type | `GET /api/v1/traceability?work_unit_id=…` → `chains[].conditions[]` `{condition_type, open, detail}` |
| `reconciliation_resolutions` count | condition hop `open == true` / `resolution_decision == null` |
| `deployment_observations.post_deploy_work_unit_id` | `DeploymentObservationResponse.post_deploy_work_unit_id` |

**Three assertions genuinely lose fidelity and must be recorded as degraded, not silently dropped:**

- `count(dispatch_records)` and `count(status <> 'skipped')` — no row-count API exists. Becomes
  response-scoped: assert the POST returns `status=skipped`, `reason_code=dispatch_disabled`, and
  that a replay is idempotent.
- `count(work_units)` (drill 4's "the pass created no work unit") — becomes **differential**
  against the captured baseline of 27 units.
- Drill 3's `observed_state->>'head_sha'` / `stored_state->>'verification_read_head_sha'` — the
  traceability hop exposes `condition_type` and `detail`, not the raw state JSON. Assert the
  condition **type and openness**; record the head-level assertion as degraded unless `detail`
  turns out to carry them (check at run time, do not assume).

### 1.4 Lease expiry — wait the real fifteen minutes

`LEASE_DURATION = timedelta(minutes=15)` (`kernel/leases.py:4`) is hardcoded with no override.
`expire_lease`'s `UPDATE` is an ADR stop condition against production ("require private SQL").

**Decision (Devon, 2026-07-27): wait the real 15 minutes.** Drills 1 and 2 are driven concurrently
up to their lapse points, then share **one** wall-clock wait, then both recovery halves run.

Both drills already assert the pre-lapse refusal (`lease_not_expired`), so the wait is itself
under test rather than dead time.

### 1.5 Terminal exit — every drill unit has a proven public path

`(READY, CANCELLED)` does not exist, which ADR-0005 flagged as a debris risk. It is not one:
`(READY, FAILED)` is a SYSTEM edge. Verified edge/role pairs:

| From | Via | To |
|---|---|---|
| `executing` | HUMAN `/review/units/{id}/cancel` | `cancelled` |
| `submitted` | VERIFIER `commands/fail` → HUMAN cancel | `cancelled` |
| `awaiting_approval` | HUMAN `/review/units/{id}/cancel` | `cancelled` |
| `ready` | SYSTEM `commands/fail` → HUMAN cancel | `cancelled` |

`/commands/cancel` and `/commands/complete` require HUMAN but sit on the M2M-only router — they are
**GUI-only** in production. Use `/review/units/{id}/cancel` and `/review/units/{id}/review`.

---

## 2. The drill package

One drill-designated package revision, decomposed once (Devon's decision, 2026-07-27).

- `package_id`: `drill-2026-07-27`
- `source_repository`: `AlobarQuest/orchestrator`
- `intake_purpose` / `status_at_intake`: mark plainly as a recovery drill
- acceptance criteria: `AC-001` … `AC-005`, one per drill unit, `evidence_type: "test"`

  **Not `automated_test`** — it resolves to `judgment_required` for every AC, so an automated AC
  declared that way can never be discharged by automated evidence. (Mechanism corrected 2026-07-28:
  WS-P2.16 U4 moved `automated_test` INTO `JUDGMENT_TYPES`, so it is now a *named* judgment type
  rather than an unrecognised one falling off the end of `DETERMINISTIC_TYPES`. The outcome is
  identical; the diagnosis is not, and the older phrasing survives in CLAUDE.md.) `test` is
  deterministic. This authoring rule stands until remediation 2.1/2.2/2.3 ship together.
- unit keys: `drill-1-crash`, `drill-2-evidence-recovery`, `drill-3-pr-conflict`,
  `drill-4-split-brain`, `drill-5-stalled-approval`
- per unit: `required_capability: "repo.edit"`, authority capabilities `repo.edit` +
  `github.pr.create` (drills 2/3/4 must record a PR binding before submit — WS-P2.16 U3),
  `max_attempts: 3` (drills 1 and 2 each consume a second attempt)
- `constraints.target_repository`: `AlobarQuest/orchestrator`

Dispatch admission checks `if not settings.enabled` **first** (`services/dispatch.py:270`), so with
`ORCHESTRATOR_DISPATCH_ENABLED=false` the outcome is `dispatch_disabled` regardless of change class
or target repository. Drill 1's assertion holds in production unchanged.

---

## 3. Per-drill variants

### Drill 1 — orchestrator crash after dispatch

**Local step that cannot run:** `kill_orchestrator` (SIGKILL to a local uvicorn).

**Production variant:** restart the container through the ops lane —
`docker restart eqj5l7k705fhi12x9i74fqf0-…` on the Hetzner VPS, then poll `/health/ready`.

This is a **real production restart with a live claim outstanding**, which is precisely the drill.
It is not a waiver. Note the difference honestly in the evidence file: a container restart is a
graceful `SIGTERM` first, so it is a *weaker* crash than SIGKILL. If a true ungraceful kill is
wanted, `docker kill --signal=KILL` is the equivalent; recommend that, so the drill keeps its
meaning.

**Sequence:** seed → claim (worker) → start → dispatch (system) → capture
`state:version:attempt_count` → `docker kill --signal=KILL` → restart → assert canonical state
survived unchanged → assert reclaim of a live lease is refused (`lease_not_expired`) → **[shared
15-minute wait]** → reclaim (system) → assert attempt+1, dead claim released with a reason, exactly
one live claim, budget not exhausted.

**Degraded assertions:** the two `dispatch_records` row counts (§1.3).

**Terminal exit:** unit ends `executing` → HUMAN cancel via `/review`.

### Drill 2 — lease lapses before submit

**Local step that cannot run:** `expire_lease`.

**Production variant:** the shared 15-minute wait (§1.4).

**Sequence:** seed → claim → start → record evidence (worker) → **[shared 15-minute wait]** →
assert the expired worker is locked out (`claim_not_active`) → `recover-evidence` (system) → assert
the recovered row **supersedes** the worker's and there is **exactly one `current` head** for the AC
→ replay the byte-identical body, assert same id and still one head → assert a different body under
the same key is refused → assert claim released `lease_expired`, unit `failed`, no new attempt →
assert a worker cannot complete → requeue (system) → claim → start → PR binding → submit.

Build the `recover-evidence` body **once** and resend it byte-identical, exactly as the local drill
does — rebuilding it re-reads a version that recovery itself changed, and the resend then becomes a
different command under the same key.

**Terminal exit:** unit ends `submitted` → VERIFIER `commands/fail` → HUMAN cancel.

### Drill 3 — external PR merge and post-arming head divergence

**No local step is blocked.** Everything is observations (SYSTEM) and PR bindings (worker).

**Adaptation is entirely in the readers:** condition assertions are global `count(*)` locally and
must become **scoped to the drill unit** via `GET /api/v1/traceability?work_unit_id=…`. Production
already holds real reconciliation history; a global count would assert nothing.

Use PR number and head SHAs that cannot collide with a real PR — the local drill's `4242` and
`aaa…`/`bbb…`/`ccc…` are fine and are obviously synthetic.

**Sequence:** unchanged from local, with each `conditions_of_type` / `condition_count` replaced by a
scoped traceability read, and each "0" baseline replaced by "no condition hop of this type on this
unit".

**Degraded assertions:** the `observed_state` / `stored_state` head-level checks (§1.3).

**Terminal exit:** unit ends `submitted` → VERIFIER `commands/fail` → HUMAN cancel.

### Drill 4 — deploy split-brain

**Local step that cannot run:** `export ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1` before
process start (`Settings` is `lru_cache`d).

**Production variant:** Coolify env write + restart (§4). Blast radius verified zero — the only
post-deploy unit in production is `completed`, not `submitted`, so the low threshold cannot alarm on
anything but the drill's own unit.

**Use `environment: "drill"`, not `"production"`.** `environment` is a free-form string
(`schemas.py:155`). Writing `production` would put a fabricated production deployment of a
fabricated digest into the real traceability ledger. Keep `base_url` and `deployment_url` on
`.invalid` hosts as the local drill does.

**Sequence:** seed → carry the unit to `completed` through the public lifecycle (claim, start,
evidence, PR binding, submit, verify, adjudicate, review, **complete via the `/review` GUI** — the
`commands/complete` route is HUMAN and M2M-only) → bind a release artifact (system) → post a
deployment observation (system) → read `post_deploy_work_unit_id` from the response → assert it is
`submitted` → sleep 2s → `POST /api/v1/reconciliation/detect` (system) → assert one condition
recorded, zero skipped correlations, type `deploy_split_brain`, raised against the post-deploy unit
→ assert it is a report (no new units beyond baseline+2, nothing transitioned, nothing resolved) →
second pass records 0 and suppresses 1.

**Terminal exit — two units, and the second one matters.** The implementation unit ends `completed`
(terminal). The **post-deploy unit ends `submitted`**, and if left there it stays permanently
`> 900s` old and will raise `deploy_split_brain` on **every future detect pass** — a standing false
alarm created by the drill. It must be driven terminal: VERIFIER `commands/fail` → HUMAN cancel.
Do not try to complete it — `required_ac_ids` returns `POST_DEPLOY_AC_IDS` for generated post-deploy
units and public adjudication rejects those ids by design.

### Drill 5 — stalled approval gate

**Local step that cannot run:** `export ORCHESTRATOR_DEAD_LETTER_STALLED_APPROVAL_SECONDS=0` before
process start.

**Production variant:** same env write + restart as drill 4 (§4). Blast radius verified zero —
production has **no** units in `awaiting_approval`, so the drill unit is the only thing the report
can name. Setting it to 0 is maximally-on, and the report is derived and read-only.

**Sequence:** seed → claim → start → `commands/request-approval` (worker) → capture state and
version → `GET /api/v1/dead-letter` **with no query parameter** → assert an entry for the unit with
`source=stalled_approval`, `reason_code=approval_unanswered`, `unit_state=awaiting_approval`,
`requeue_eligible=false` → assert reading it transitioned nothing and **did not change the version**
→ read again, assert still exactly one entry.

The dead-letter read is SYSTEM-authenticated here (the local drill reads it as `human`; the route is
auth-only, and `/api/v1/dead-letter` is on the M2M router in production).

Baseline: 5 dead-letter entries, **0** of `source=stalled_approval`. Assert against that baseline,
not against an empty report.

**Terminal exit:** unit ends `awaiting_approval` → HUMAN cancel via `/review`.

---

## 4. Run order

Threshold changes are batched into one apply and one revert (Devon's decision, 2026-07-27).

1. **Prerequisite 4** — fresh production backup, verified restorable, via the vps-backup lane.
2. Seed the drill package: intake → decomposition → approve → 5× authority approval →
   5× `commands/ready`.
3. **Drill 3** (no threshold, no wait) — run first, it is the cheapest full proof.
4. **Drills 1 and 2** concurrently to their lapse points → one shared 15-minute wait → both
   recovery halves.
5. **Env apply**: write `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1` **and**
   `ORCHESTRATOR_DEAD_LETTER_STALLED_APPROVAL_SECONDS=0` in one change. **Verify both landed
   inside the container before restarting.** Restart. Confirm `/health/ready`.
6. **Drill 4**, then **drill 5**.
7. **Env revert**: remove both. Verify from inside the container. Restart. Confirm `/health/ready`.
8. Drive every drill unit terminal (§1.5). Re-read `status-ledger?include_inactive=true` and prove
   no drill unit is left non-terminal.
9. Re-run `python3 scripts/attest_exit_criteria.py` — must still PASS.
10. Re-run `GET /api/v1/consistency-check` — must still report `divergent: false`.

Neither env var is `ORCHESTRATOR_M2M_*`, so the fail-closed credentials/roles hazard does not apply.
Verify each write anyway; the cost of one extra check is nothing against a boot failure.

---

## 5. Stop conditions (ADR-0005), as run-time checks

| Condition | Check | Status at authoring |
|---|---|---|
| Artifact identity unprovable | container `RepoDigest` == intended GHCR digest | **PASS** — `sha256:2fc5463123…`, tag `8da4af3-wsp27inc2-amd64`, amd64 |
| Migration head unprovable | prod `alembic_version` == repo head | **PASS** — both `0019_wsp27_tracker_recon` |
| Live OpenAPI differs from the reviewed contract | `scripts/attest_exit_criteria.py` | **PASS** — 4 claims |
| Would touch a non-drill resource | every unit id checked against the 27-unit baseline before any write | enforced per step |
| Would need private SQL | none of the above uses SQL; §1.4 replaced the only such step | **satisfied** |
| Would need a capability outside standing credentials | system + verifier + worker only | **satisfied** |
| Would leave a unit non-terminal | §1.5 proves a path from every resting state | **satisfied** |

The four stale `bump-dependencies-*` units in `ready` are pre-existing debris and are **out of
scope**. Do not touch them. (§1.5 incidentally shows they are recoverable, which is worth a backlog
item, not a drill step.)
