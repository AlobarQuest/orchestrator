# WS-P2.1 — Recovery controls, reconciliation, and scripted drills: design specification (v4)

**Date:** 2026-07-11
**Workstream:** WS-P2.1 (Program Phase 2, Wave 1)
**Package:** `ws-p2.1-recovery-controls-drills` rev 1 (approved, hash `135af657…2071f2bd`)
**Work unit:** `recovery-controls-drills-impl` (`575b8c63-…`)
**Binding ADRs:** ADR-0001 (authority-envelope contract), ADR-0002 (reconciliation via a separate report-only runner)
**Review:** `2026-07-11-wsp21-adversarial-architecture-review.md` — three adversarial rounds (2 independent reviewers). Round 1: 3 blockers + 8 majors. Round 2: 2 new majors + 9 minors. Round 3 (second reviewer, independent): **1 new data-corruption blocker + 1 governance blocker + 6 majors + 9 minors.** All resolved here.
**Status:** Draft v4 — pending Devon's AC-012 design approval, which precedes implementation.
**Approved deviation:** detection is **on-ingest** for AC-001/AC-002/digest (per the package's own wording) and a **detect-pass only for AC-003 split-brain**, which is time-elapsed by nature and unknowable at ingest. Devon selected this hybrid; the AC-003 residual deviation is recorded as a chained AC-012 factory event and ADR-0002 is amended (§14).

---

## 0. Scope and invariants

Conforms to ADR-0002: the invariants constrain the orchestrator *process*, not the *system*. The orchestrator stays **push-only and loop-free**; active pulling lives in a **separate report-only runner**. Preserved and tested:

1. **No orchestrator-side outbound GitHub/Coolify call** on the observation/release/post-deploy/reconciliation paths. The process legitimately imports `httpx` in exactly four places — `cli.py` (API client), `services/dispatch.py` (the one sanctioned push-out), `services/github_app.py` (App-token mint), `services/knowledge_promotions.py` (Brain submit). **No repo-wide outbound scan or allowlist exists today** (only per-file scans in `test_ws53_scope_guards.py` / `test_ws61_scope_guards.py` and an import-name check at `test_scope_guards.py:10-25`) — **AC-011 creates the first one** (§8). Stated as new, not existing.
2. **No autonomous background loop, scheduler, or cron** in the orchestrator process.
3. **No automatic merge**; no worker/agent/CI/orchestrator path merges a PR.
4. **Detection never auto-un-completes a completed unit and never auto-resolves** — it records a condition for operator decision.
5. **The runner reports only** — never sets canonical lifecycle state.
6. **No event-replay / projection-rebuild engine** — a consistency *check* only.
7. **No secret in a tracked file.**

---

## 1. Reconciliation detection (AC-001..003) — hybrid

### 1.1 Where detection runs

| Path | Where detected | Why |
|---|---|---|
| **AC-001 `github_pr`** | **On ingest**, post-commit, in its **own transaction** | Honors the package's "on ingest" wording; automatic on arrival. Separate transaction so a rejected ingest cannot roll the condition back. |
| **AC-002 `github_check`** | **On ingest**, post-commit, own transaction | Same. |
| **`digest_divergence`** | **On ingest**, including on a **rejected** ingest — recorded at the **route layer** after the `DomainError` is caught, in its own transaction | The digest guard *raises* (`deployment_observations.py:415-420`) and rolls back; the condition must be written outside that transaction or it is erased. |
| **AC-003 `deploy_split_brain`** | **Detect-pass only** (`POST /api/v1/reconciliation/detect`) | **Time-elapsed by nature**: the post-deploy unit is minted `SUBMITTED` in the ingest transaction (zero seconds old), so "verification stalled" cannot be true at ingest under *any* design. This is the one approved deviation (§14). |

**On-ingest detection is a post-commit hook, never in the ingest transaction.** The ingest commits first; detection then runs in a fresh transaction. It **never raises** — a detection failure or malformed correlation is *skipped and counted* (§1.7), so it can never turn a valid observation into a rejected ingest (a DoS on the observation path).

**Who invokes the detect-pass in production** (resolving the v3 §1.1/§6 contradiction): the **reconciliation runner** calls it at the end of each pass, and an **operator** may call it directly. The runner is therefore permitted exactly **two** endpoints — `POST /observations` and `POST /reconciliation/detect` — and the report-only test asserts precisely that pair (§6). Detect creates no unit and sets no lifecycle state, so it does not breach report-only.

### 1.2 The `reconciliation_required` condition — append-only model

```
reconciliation_conditions
  id                          UUID PK
  work_unit_id                UUID FK -> work_units.id
  observation_kind            TEXT   ("github_pr"|"github_check"|"deployment")   -- discriminator
  observation_id              UUID NULL FK -> observations.id
  deployment_observation_id   UUID NULL FK -> deployment_observations.id
  condition_type              TEXT   ("external_merge_alarm"|"pr_state_divergence"|
                                      "check_result_flip"|"deploy_split_brain"|"digest_divergence")
  stored_state                JSONB  (bounded)
  observed_state              JSONB  (bounded)
  normalized_divergence_hash  TEXT
  detail                      TEXT
  detected_at                 TIMESTAMPTZ
  event_id                    UUID
  idempotency_key             TEXT UNIQUE
  UNIQUE (work_unit_id, observation_kind, normalized_divergence_hash)
```

**A resolved divergence must be re-raisable (M-B fix).** A naive hash over `(kind, condition_type, key facts)` means: operator resolves a `check_result_flip`; the same flip recurs; the insert hits the UNIQUE, is silently swallowed, and the `reconciliation.required` event is never re-emitted — permanently blinding the operator to a *recurring* condition, and `check_result_flip` / `deploy_split_brain` are exactly the types that recur with identical facts. Therefore:

> `normalized_divergence_hash = sha256(kind, condition_type, key_facts, **resolution_generation**)`, where `resolution_generation` = the count of existing resolutions for this `(work_unit_id, kind, condition_type, key_facts)` lineage.

So an unresolved condition dedups (re-detection is a no-op), and a condition that recurs *after* resolution mints a new row and a new event. The same value is the `idempotency_key` namespace (`reconcile:{unit}:{kind}:{hash}`), keeping it distinct within the globally-unique `events.idempotency_key`.

**Resolution write surface (M-A fix — v3 had none, so every condition would have stayed open forever):**
`reconciliation_resolutions (id, condition_id FK UNIQUE, resolved_by, decision, rationale, event_id, idempotency_key)`.
- **`POST /review/reconciliation/conditions/{id}/resolution`** — **HUMAN**, via the `/review` router (M2M `/api` strips human headers). Emits `reconciliation.resolved`.
- **Open condition = no resolution row** (set-difference, mirroring evidence supersession). `UNIQUE(condition_id)` makes a condition resolvable exactly once; a recurrence is a *new* condition (above), not a re-resolution.

Both tables carry the `reject_append_only_mutation()` `BEFORE UPDATE OR DELETE` trigger (precedent: migration `0012_ws61_observations`). No UPDATE/DELETE ever touches either.

### 1.3 Correlation and the runner's observation contract

- **`github_pr` / `github_check` (AC-001/002):** submitted on the first-class **`subject_type="work_unit"` / `subject_reference`** channel (`models.py:60-102` — the enum members already exist; **no schema change**). Detection **cross-checks the observation's PR identity (number + head sha) against `unit_pr_binding`** before recording — satisfying AC-001 "on the exact pull-request head" and stopping a wrong/forged `work_unit_id` from alarming an unrelated unit. Malformed → skip + count, never raise.
- **Runner deployment observation (M-F fix):** submitted on **`subject_type="release_binding"`** (`models.py:70-81` — available), carrying `artifact_digest` + `deploy_status`. This is the join key for `digest_divergence` against `binding.artifact_digest`. (The existing trusted deploy-monitor path via `ReleaseArtifactBinding` → post-deploy unit is unchanged and is *not* the runner's path.)

**`source_reference` is content-addressed, and `observed_at` comes from upstream (M-C fix — this is load-bearing, not a residual).** `_fact_identity` includes `observed_at` (`observations.py:355-369`), so a content-addressed reference with a *wall-clock* `observed_at` **guarantees `observation_conflict` on the second unchanged pull**. Both halves ship together:

> `source_reference = pr:{number}@{head_sha}:{sha256(normalized_facts)}` (likewise `check:` / `deploy:`),
> and **`observed_at` is the upstream timestamp** — `PR.updated_at`, `check_run.completed_at`, deployment completion time — **never the runner's pull time**.

Consequences: an **unchanged** reality re-pulled next pass produces the *identical* `(source_reference, fact_hash)` → the existing content dedup early-returns (`observations.py:154-164`), so there is **no unbounded row growth** and no conflict. A **changed** reality produces a new reference → a new append-only row. Exact re-delivery dedups on `idempotency_key` (AC-007). No `pass_id` is needed and none is used (v3's `#{pass}` scheme is withdrawn — a repeated/reset counter would have reintroduced `observation_conflict`).

**"Current" observation for a `(unit, kind)` = newest by `(observed_at, received_at, id)`** — mirroring the existing ordering (`observations.py:123`), never `observed_at` alone (clock skew). This computed newest-wins over append-only rows **is** AC-002's "later `github_check` supersedes the earlier under append-only supersession" — no supersedes column.

**Fact shape must survive the secret scanner:** `SECRET_KEY_PARTS` contains `"log"` (`observations.py:33-44`), so GitHub's standard `logs_url` is **rejected outright**. The runner emits an explicit bounded schema (`pr_number`, `head_sha`, `state`, `merged`, `check_name`, `conclusion`, `deploy_status`, `artifact_digest`, `observed_at`) and never forwards raw provider payloads. Source system: the existing `"github"` member; a deploy/health member is confirmed against `OBSERVATION_SOURCE_SYSTEMS` first, and an extension (if needed) is a **listed** migration.

### 1.4 Detection rules

- **AC-001 `github_pr`:** newest PR observation, cross-checked to `unit_pr_binding`, vs stored state.
  - **merged** on a unit not yet `completed` → `external_merge_alarm` (the never-auto-merge alarm; never merges, never completes).
  - **closed**, or **head changed after verification read a head** (§1.6) → `pr_state_divergence`.
  - On a `completed` unit: informational at most; **never** un-completes (§1.5).
- **AC-002 `github_check`:** newest check per `(unit, ac)` wins. A result that **flips** (success when verification read it → later failure) → `check_result_flip`. Never auto-un-completes.
- **AC-003 `deploy_split_brain`** (detect-pass): a binding with a `DeploymentObservation` — whose existence **already proves the deploy succeeded**, so no runner-pushed deploy observation is needed as a precondition — whose **post-deploy verification unit has sat in `SUBMITTED` beyond `RECONCILE_SPLIT_BRAIN_STALL_SECONDS`** (measured from `WorkUnit.created_at`, server-defaulted at `models.py:218`) without completing → `deploy_split_brain`. A normal in-progress verification (under threshold) is explicitly **non-conflicting**.
  **The deploy nobody reported (M-E fix — ADR-0002 rejects Alternative A precisely because it "cannot catch drift nobody reported", so this case must not stay open):** the runner also reports deploys for release bindings. **A runner-reported deploy for a binding that has *no* post-deploy verification unit → `deploy_split_brain`** — a cheap read, no threshold, and it closes the case where nothing was ever ingested. This requires the read surface to expose release bindings and recently-completed units with bindings (§6), since an implementation unit carrying a binding is `COMPLETED` and thus not "in-flight".
- **`digest_divergence`:** runner-reported `artifact_digest` ≠ `binding.artifact_digest` → condition (a *read*). Additionally recorded at the route layer when the trusted deploy path's digest guard **rejects** an ingest (§1.1). The existing guard (`deployment_observations.py:415-420`) is unchanged.

### 1.5 Never-mutate-completed guard

Condition recording performs **no** `work_unit` write and **no** transition — only inserts into the two append-only tables + `events`. A protocol test drives a `completed` unit through every detection path and asserts `state == completed` and `version` unchanged. Structural proof of failure-modes #3/#4.

### 1.6 `unit_pr_binding` and the head-change alarm

The table **exists** (not deferred). `unit_pr_binding (work_unit_id PK, pr_number, head_sha, verification_read_head_sha NULL, updated_at)`.

- `head_sha` is **mutable, worker-written** — pre-verification rebases/force-pushes are normal and **do not alarm**.
- `verification_read_head_sha` is the **alarm-arming** field and is therefore **write-once**: set when verification reads the head, never updated (enforced by a service guard + a test). The mutable field arms nothing; the write-once field arms everything — which is why the table does not need the append-only trigger, and why the alarm cannot be disarmed by a later worker push.
- The head-change alarm fires only when a runner-observed head differs from `verification_read_head_sha`. Legitimate pre-verification iteration never alarms; a post-verification external push always does.

### 1.7 Fail-open is counted, not silent

Skip-never-raise (§1.1) plus dedup-swallow are two silent-miss modes. The detect-pass response and the on-ingest hook's event therefore **report counters**: `skipped_correlations`, `suppressed_duplicates`, `conditions_recorded`. A miss is observable, not invisible.

### 1.8 Concurrency

Detection uses the same triad as every other ingress: `pg_advisory_xact_lock` on the condition key + the UNIQUE constraint + replay-equality (IntegrityError → replay-return, never a 500). Each condition is written in **its own transaction**, so one failure cannot roll back an entire detect-pass.

---

## 2. AC-004 — lease-expired evidence-attach recovery

**What AC-004 promises** (`package.yaml:127`): a defined, attributable path to attach the evidence **without re-executing the work**, and the worker **still cannot transition the unit to completed**. It does **not** promise completion without a new attempt — `FAILED` has no edge to `SUBMITTED`, and only `WORKER_EDGES` reach `SUBMITTED` (`transitions.py:29`) while `VERIFIER_EDGES` begin there. An earlier draft claimed otherwise; that is **retracted**. The value delivered is that attempt *n+1* **short-circuits and does not redo the job**.

### 2.1 ⚠️ The supersession head — the blocker this design must not reintroduce

Recovery **must** bypass `_store_evidence`, because `_validate_attempt` (`evidence.py:593-625`) rejects a SYSTEM actor, a released claim, and an expired lease. But **`_store_evidence:368-378` is the only thing preventing two supersession heads** — `Evidence`'s constraints (`models.py:341-371`) permit two rows with `supersedes_evidence_id IS NULL` for one `(revision, unit, ac)`. Two heads ⇒ `_terminal` **raises** ("supersession chain has multiple terminals", `evidence.py:853-854`) ⇒ `current_evidence` raises ⇒ the verifier can never adjudicate that AC (`verifier.py:110,261`) **and** `_store_evidence:368` blocks all further evidence for it. `evidence` is in `APPEND_ONLY_TABLES` with a `BEFORE UPDATE OR DELETE` trigger (`0001_ws31_core.py:16`), so **the row cannot be repaired**. A single call to a naive recovery endpoint would **permanently wedge the unit so it can never complete.**

**Mandated, in the design (not left to implementation):**

- Recovery resolves `previous = current_evidence(revision, unit, ac)` and, if one exists, writes its row with **`supersedes_evidence_id = previous.id`** — it never writes a second `NULL`-supersedes head. (Refusing outright when *any* evidence exists for the AC is the acceptable stricter alternative; superseding is preferred because it preserves the recovery's purpose.)
- The check-then-insert is **TOCTOU-racy** against a concurrent submit from a new attempt, so recovery **serializes on `(work_unit_id, ac_id)` with `pg_advisory_xact_lock`** (the existing pattern at `evidence.py:_lock_idempotency_key`) and re-reads the head **under the lock**, also taking the `WorkUnit` row lock (`with_for_update`, as evidence writes already do at `evidence.py:529`).
- **Defense in depth:** a partial unique index on `(work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL` makes a second head structurally impossible for all time. **Consequence (must be honored):** this makes a *two-headed* corrupt fixture unbuildable, so **AC-008's corrupted fixture is respecced** to a **zero-head chain** (an orphaned `supersedes_evidence_id` pointing at a superseded row), which the independent `count != 1` check catches identically (§5).

### 2.2 Preconditions and flow

`POST /api/v1/work-units/{id}/attempts/{attempt}/recover-evidence` (+ CLI), **SYSTEM/operator — never the expired worker**.

- Target claim is **expired**: `lease_expires_at <= now`, **either still unreleased (`released_at IS NULL`)** — the AC's actual scenario, lease lapsed just before submit — **or** released with `terminal_reason == "lease_expired"`. (v3 required *both* released+terminal_reason, whose sole writer is `_perform_reclaim` (`claims.py:251-252`), which made AC-004's own scenario unreachable.)
- If still unreleased, recovery **releases the claim and SYSTEM-fails the unit** (`CLAIMED/EXECUTING → FAILED`, SYSTEM edges, `transitions.py:18-19`) **without minting a new attempt**, routed through a **shared release primitive factored out of `_perform_reclaim`** so `released_at`/`terminal_reason` keep exactly one writer.
- Refuses `COMPLETED`/`CANCELLED` units. Records against **that specific prior `attempt`**, provenance-tagged (`recovered_from_expired_lease`, original `claim_id`). Carries the standard idempotency-key + replay contract. Does **not** re-open the lease; does **not** transition toward `completed`.
- **Reclaim interaction:** disjoint by precondition. Recovery handles the unreleased-expired claim (releasing it itself, no new attempt). If reclaim already started attempt *n+1*, recovery still admits — superseding the head correctly (§2.1) — so no double-recovery and no orphaned claim, because there is exactly one writer of `released_at`.

A guard test proves a worker still cannot reach `completed` via any edge after recovery.

---

## 3. AC-005 / AC-006 — dead-letter view + operator recovery

### 3.1 Dead-letter view (AC-005, read-only, live-derived)

`GET /api/v1/dead-letter` + `orchestrator dead-letter` CLI. Live-computed from source tables:

1. **Failed / blocked / cancelled units** — `WorkUnit.state IN ('failed','blocked','cancelled')` + latest claim's `terminal_reason`. (Includes `blocked`, so `requeue` targets are in the surface the actions operate on.)
2. **Failed/blocked dispatch records** — `DispatchRecord.status IN ('failed','blocked')` + `reason_code`.
3. **Open failure-signature circuit breakers** — derived. `_opens_circuit` is a **prospective** predicate: `len(prior failures) + 1 >= threshold` (`dispatch.py:384-397`) — it counts the failure *about to be written*. Reusing it at rest would show a breaker open **one failure early**. It is therefore split into `signature_failure_count(unit, signature)` + `circuit_open(count, threshold)`; **dispatch passes `count + 1`, the view passes `count`** — one shared predicate, correct at both call sites.

Read-only. New GET routes are added deliberately to the pinned route inventory (`test_scope_guards.py`).

### 3.2 Recovery actions (AC-006)

**`retry` already exists — twice.** `/review/units/{id}/retry` (`web.py:551-577` → `authorize_retry`) and `POST /api/v1/work-units/{id}/retry-authorization` (`routes.py:1085`), both already pinned (`test_scope_guards.py:68,78`). **Adding another retry route would fail the pinned-route test.** The only genuinely new recovery action is **`requeue`**.

| Action | Precondition | Mechanism | Role & surface |
|---|---|---|---|
| **retry** (existing) | `FAILED`, attempts **exhausted** | `claims.authorize_retry` (raises `max_attempts`, `FAILED→READY`; `claims.py:424-437`) | **HUMAN** → existing `/review/units/{id}/retry` |
| **requeue** (**new**) | `FAILED`/`BLOCKED`, attempts **not exhausted** | SYSTEM `FAILED/BLOCKED→READY`; actor attributed, role forced SYSTEM (`claims.py:91` pattern) | **SYSTEM** → `/api` M2M. Refused if `attempt_count >= max_attempts`, and **reuses the existing readiness check** (`claims.py:464-473`) — else the unit lands `READY`, `claim_unit` rejects it (`claims.py:66`), and it drops out of the view: invisible *and* unrunnable. |
| **cancel** (existing edges) | legal `*→CANCELLED` (all HUMAN-only) | transition | **HUMAN** → `/review`. There is **no `BLOCKED→CANCELLED`** edge — a blocked unit is requeue-only. |

**The "cannot declare completion" proof, stated correctly.** "No `WORKER_EDGES→COMPLETED`" is a *non-sequitur* for these actions, because retry/cancel are HUMAN-surfaced and `HUMAN_EDGES` *does* contain `SUBMITTED/VERIFYING/AWAITING_REVIEW→COMPLETED`. The real guarantee is two-fold: **each recovery endpoint hardcodes its target state** (`READY` / `CANCELLED` — never `COMPLETED`), and **any** transition into `COMPLETED` is gated by `completion_satisfied` (`transitions.py:89-90`). The test asserts each endpoint's hardcoded target and that no recovery path reaches `COMPLETED`, grants a waiver, or merges (no merge edge exists in `LEGAL_EDGES`).

---

## 4. AC-007 — duplicate-delivery idempotency audit

Nearly every ingress already has the mechanism (advisory lock + unique `idempotency_key` + replay-equality) **and** a regression test. AC-007 **proves** every event/evidence/observation ingress is idempotent under duplicate delivery **against PostgreSQL**, and adds a test wherever one is missing:

- A checked-in coverage matrix (`tests/idempotency/`): path → mechanism → test; each row a real Postgres double-submit asserting one row + identical response.
- Explicit tests for the two asymmetries: the **lifecycle-transition path** (unique Event key + `with_for_update`, no advisory lock) and the **reclaim compound-key path** (`:failed`/`:ready` suffixes).
- All new WS-P2.1 ingress — conditions, **resolutions**, recover-evidence, requeue, detect — get duplicate-delivery tests from birth.

---

## 5. AC-008 — projection-vs-source consistency check

No materialized projections exist; projections are computed live from append-only source tables. Per ADR-0002 this is a **check, not a rebuild**. `orchestrator check-consistency` + `GET /api/v1/consistency-check` re-derives each projection with an **independent recomputation** and **reports** divergence (never repairs, never crashes):

- **It must not reuse the helpers it audits** — `_terminal` **raises** on a multi-head chain (`evidence.py:853-854`), so reusing it would crash instead of report, and reusing the set-difference would make the clean-fixture result a tautology. The check uses a **SQL-level group/count of unsuperseded heads** per `(revision, unit, ac)` and flags any `count != 1`.
- **Corrupt fixture respecced (per §2.1's partial unique index):** a two-headed chain is now structurally impossible, so the seeded corruption is a **zero-head chain** (an orphaned `supersedes_evidence_id`). `count != 1` catches it identically.
- Other invariants: status-ledger current-evidence agreement; completion integrity (every `completed` unit's required ACs have satisfied terminal adjudications); no open `reconciliation_condition` implying an illegal auto-mutation.
- **AC-008 evidence:** zero divergence on a clean fixture; the seeded divergence reported on the corrupted fixture.

---

## 6. AC-009 — the separate report-only reconciliation runner

- New top-level package `src/reconciliation_runner/`, `[project.scripts] reconciliation-runner = …`, own `httpx` client, `pydantic`/`typer` only. **Imports nothing from `orchestrator.*`** (including `persistence` — otherwise it could write the DB directly and gut report-only), enforced bidirectionally by a `tests/architecture` guard modeled on `test_application_has_no_external_mutation_integrations` (`test_scope_guards.py:10-25`).
- **Read surface** (`GET /api/v1/in-flight-units`, SYSTEM, read-only): in-flight units + their `unit_pr_binding`, **plus release bindings and recently-completed units carrying bindings** — required by M-E, since a unit with a release binding is `COMPLETED` and therefore not "in-flight".
- **Pushes** as a **SYSTEM** actor via `POST /api/v1/observations` (PR/check on `subject_type=work_unit`; deploy on `subject_type=release_binding`), then calls **`POST /api/v1/reconciliation/detect`**.
- **Report-only mandate:** the runner may call **exactly two write endpoints — `/observations` and `/reconciliation/detect`** — and **never `…/deployment-observations`**, which mints a `WorkUnit` (`SUBMITTED`) + five `Evidence` rows per accepted push (`deployment_observations.py:156-231`) and, because the environment regex admits arbitrary strings, would let a SYSTEM-credentialed runner create unbounded post-deploy units. The test asserts the **exact allowed endpoint set** (not merely "no unit created" — detect creates no unit either, so that assertion alone would enforce nothing) **and** that a full pass **transitions no existing unit**.
- **Operator-invoked**; no scheduler/cron in scope (deferred per ADR-0002).

---

## 7. AC-010 — four scripted recovery drills

Four re-runnable scripts joining the existing `scripts/` dir (which holds `build_registry_bundle.py`), in the **`restore-drill.sh` mold**: `#!/bin/bash`, `set -euo pipefail`, `--keep`, disposable scratch (`mktemp -d`, PID-suffixed throwaway Postgres, DB name **≠ `orchestrator_test`**), `trap cleanup EXIT` idempotent teardown, `die()` vs accumulating `fail()`, timestamped `log()`, explicit `[...] || fail`, **exit 0 = PASS**. Read-only toward production/shared systems; the orchestrator is driven only through **public API/CLI**.

1. **Orchestrator dies after dispatch** — dispatch recorded, process killed; the unit is recoverable via reclaim/requeue; assert no orphaned canonical state. *(Note: `dispatch_enabled` defaults `False` (`config.py:9`), so the drill makes **no live `workflow_dispatch`** — it exercises the recorded-dispatch path only, touching no shared system.)*
2. **Evidence submission fails after worker completion** — a worker claims, produces its result, and its lease lapses **before submit** (claim left *expired-but-unreleased*). The drill invokes `recover-evidence` and asserts: the evidence **attaches to attempt *n*, superseding any prior head** (never creating a second head); recovery **releases the claim and SYSTEM-fails the unit without minting a new attempt**; the **worker cannot reach `completed`**; and attempt *n+1* submits **without re-executing the work**.
3. **PR merged outside the session** — push a `github_pr` merged observation for a not-yet-completed unit; assert the **on-ingest** hook records `external_merge_alarm` and the unit is **not** auto-completed/merged.
4. **Deployment succeeds while verification times out** — with `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS` set low (it is `BaseSettings(env_prefix="ORCHESTRATOR_")` with an `lru_cache`d accessor, `config.py:7-8,34-36`, so the drill sets it before starting its throwaway process — **no real sleep, no private time manipulation**), run `reconcile-detect`; assert `deploy_split_brain` is recorded and the deployment is not silently accepted.

Documented for the quarterly cadence.

---

## 8. AC-011 — the invariant scan

Extends `tests/architecture/`, respecting the existing dispatch/post-deploy allowlist. **This creates the first repo-wide outbound scan** (§0):

1. **No auto-merge** — extends `test_no_automatic_merge.py`; asserts no merge endpoint and no merge edge.
2. **No orchestrator-side outbound GitHub/Coolify call** — repo-wide `httpx.`/`requests.`/`coolify` scan over `src/orchestrator/**` **minus** the four legitimate importers (`cli.py`, `services/dispatch.py`, `services/github_app.py`, `services/knowledge_promotions.py`). The new reconciliation/recovery files are in the **forbidden** set.
3. **No background loop/scheduler/cron** — new AST/string scan for `while True`, `create_task`, `BackgroundTasks`, `apscheduler`, `cron`, `Thread`, `schedule:` (empirically clean today; unguarded).
4. **Workers cannot complete** — structural assertion over `transitions.py` (no `WORKER_EDGES` target is `COMPLETED`), **plus** the per-endpoint hardcoded-target assertion for recovery actions (§3.2).

Plus a **no-tracked-secret** assertion wired to the security scanner, so the branch carries the evidence.

---

## 9. AC-012 / AC-013 — the human gates (retained)

Both retained in the decomposition — Devon owns them out-of-band. This design, after three adversarial rounds, **is** AC-012; the merge after Quality-green is AC-013.

---

## 10. Data model / migration

One forward-only migration: `reconciliation_conditions`, `reconciliation_resolutions` (both with the append-only trigger; `UNIQUE(condition_id)` on resolutions), `unit_pr_binding` (§1.6; `verification_read_head_sha` write-once by service guard), and the **partial unique index on `evidence (revision, unit, ac) WHERE supersedes_evidence_id IS NULL`** (§2.1 — verified against existing rows before applying). Evidence recovery adds **no** column. **No** column is added to `work_units`. New config `RECONCILE_SPLIT_BRAIN_STALL_SECONDS` in `config.py` (env-overridable). Downgrade drops the new objects.

---

## 11. Test strategy

TDD, Postgres-backed, mirroring `tests/services/conftest.py`, honoring the separate-runtime-DB invariant.

- `tests/services/` — on-ingest detection (AC-001/002 + digest, incl. the rejected-ingest route-layer case), detect-pass (AC-003 split-brain both ways: threshold *and* no-post-deploy-unit), completed-unit no-mutation, fail-open counters, resolution + re-raise-after-resolution, evidence recovery incl. **the two-head-prevention test** (AC-004), dead-letter + breaker off-by-one (AC-005), requeue guards + hardcoded-target proof (AC-006), consistency check clean/corrupt (AC-008).
- `tests/idempotency/` — the AC-007 matrix.
- `tests/api/` + `tests/cli/` — new GET/POST surfaces incl. the resolution route.
- `tests/architecture/` — AC-011's four scans + the runner import-isolation and **exact-endpoint-set** guards.
- `tests/reconciliation_runner/` — pulls fixture reality, pushes only the two allowed endpoints, transitions no unit.
- `scripts/` — the four drills, shellcheck-clean.

All AC-001..011 evidence lands on the PR-head **Quality** check; the eleven mapped ACs are then adjudicated `passed` by a VERIFIER credential; Devon completes and merges.

---

## 12. How the design forecloses each named failure mode

| Package failure mode | Foreclosed by |
|---|---|
| In-process poller calling GitHub/Coolify | §6 runner is the only puller; §8 repo-wide outbound scan (first of its kind) |
| Background loop/scheduler in orchestrator | §8 no-loop scan; detection is on-ingest or operator/runner-invoked, never a loop |
| Detection auto-un-completes / auto-resolves | §1.5 never-mutate guard + protocol test; detection only appends |
| Runner sets canonical lifecycle state | §6 exact-endpoint-set test (`/observations` + `/detect` only); never the deployment endpoint |
| Evidence-attach lets worker declare completion | §2 SYSTEM-only, attempt-scoped, no completion edge — **and §2.1 head-supersession prevents the wedge** |
| Recovery actions bypass guards | §3.2 hardcoded target states + `completion_satisfied`; requeue reuses the readiness check |
| Scope creep into replay/rebuild engine | §5 consistency check only, independent recomputation |
| Drill mutates real/shared state | §7 disposable scratch, trapped teardown, public-API-only, injectable threshold, no live dispatch |

---

## 13. Residual items for the task-level implementation plan

- Default + operator guidance for `RECONCILE_SPLIT_BRAIN_STALL_SECONDS` (drill sets it low; production above a normal verification's worst case).
- Confirm the `OBSERVATION_SOURCE_SYSTEMS` member for deploy/health facts (an extension would be a *listed* migration).
- Factoring the shared claim-release primitive out of `_perform_reclaim` (§2.2).
- Verify the evidence partial unique index applies cleanly to existing rows before the migration lands (§10).

---

## 14. What the approver is being asked to accept

1. **The architecture** — ADR-0002's Model C: a separate report-only runner; append-only `reconciliation_required` conditions with an operator resolution surface; a consistency *check*, not a rebuild engine.
2. **One narrow, approved deviation.** Detection is **on-ingest** for AC-001/AC-002/digest — matching the package's own "on ingest" wording. **Only AC-003's split-brain uses the detect-pass**, because it is time-elapsed by nature: the post-deploy unit is minted `SUBMITTED` inside the ingest transaction, so "verification stalled" cannot be true at ingest under *any* design. Devon selected this hybrid; **the residual AC-003 deviation is recorded as a chained AC-012 factory event**, and **ADR-0002 is amended** (its "only new logic is conflict-detection on observation ingest" line is now inaccurate on two counts — the split-brain mechanism, and its omission of the recovery/dead-letter/consistency surfaces).
3. **A scoping correction on AC-004** (§2) — the package promises evidence *attachment without re-executing the work*, not *completion without a new attempt*. An earlier draft over-claimed and is retracted.
4. **A data-model hardening** (§2.1/§10) — a partial unique index makes a second evidence supersession head structurally impossible. This is what prevents a recovery call from permanently wedging a unit, and it is why AC-008's corrupt fixture is a zero-head chain rather than a two-headed one.
