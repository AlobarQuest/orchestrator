# WS-P2.1 — Recovery controls, reconciliation, and scripted drills: design specification (v3)

**Date:** 2026-07-11
**Workstream:** WS-P2.1 (Program Phase 2, Wave 1) — recovery controls + drills
**Package:** `ws-p2.1-recovery-controls-drills` rev 1 (approved, hash `135af657…2071f2bd`)
**Work unit:** `recovery-controls-drills-impl` (`575b8c63-…`)
**Binding ADRs:** ADR-0001 (authority-envelope contract), **ADR-0002 (reconciliation via a separate report-only runner)**
**Review:** `2026-07-11-wsp21-adversarial-architecture-review.md` — two adversarial rounds. Round 1: 3 blockers + 8 majors. Round 2 (re-review of the fixes): 2 new majors + 9 minors. **All resolved here.**
**Status:** Draft v3 — pending Devon's AC-012 design approval, which precedes implementation. **Note §1.1 and §14: this design deviates from ADR-0002's "on ingest" mechanism and asks for that deviation to be approved knowingly.**

---

## 0. Scope, invariants, and what this design must not do

Conforms to **ADR-0002**: the invariants constrain the orchestrator *process*, not the *system*. The orchestrator stays **push-only and loop-free**; active pulling of external reality lives entirely in a **separate report-only runner** that pushes observations back through the existing public API. Preserved and tested invariants (package `risk.failure_modes`, `constraints.other`):

1. **No orchestrator-side outbound GitHub/Coolify call** on the observation/release/post-deploy/reconciliation paths. (The process legitimately calls `httpx` for the one sanctioned push-out — `workflow_dispatch` — plus its App-token mint, Brain-promotion, and the CLI-as-API-client; those files are allowlisted by the architecture suite. §8/AC-011.)
2. **No autonomous background loop, scheduler, or cron** in the orchestrator process.
3. **No automatic merge**; no worker/agent/CI/orchestrator path merges a PR.
4. **Conflict detection never auto-un-completes a completed unit and never auto-resolves** — it records a condition for operator decision.
5. **The runner reports only** — pushes observations, never sets canonical lifecycle state.
6. **No event-replay / projection-rebuild engine** — only a consistency *check*.
7. **No secret in a tracked file.**

All evidence is realizable against an ephemeral orchestrator + Postgres and fixture/recorded observations — no production mutation, no live third-party call.

---

## 1. Reconciliation detection (AC-001..003) — decoupled from ingest

### 1.1 Architecture — a dedicated detect-pass, not in-transaction ingest logic

**Detection does NOT run inside the observation-ingest transaction** (v1's root flaw: rejected ingests roll back conditions; split-brain isn't ingest-knowable; write-once observation semantics wedge re-push). Instead, conflict detection is a dedicated, SYSTEM/operator-invoked **reconciliation-detection pass**:

- `POST /api/v1/reconciliation/detect` (SYSTEM/operator) + `orchestrator reconcile-detect` CLI.
- Runs in its **own transaction(s)**, decoupled from ingest. It **reads** current observations, stored lifecycle state, `unit_pr_binding`, and post-deploy elapsed state; it **records** `reconciliation_conditions` for divergence; it **never** transitions or writes a `work_unit`; it makes **no outbound call**; it is invoked on demand (no background loop).
- It **fails open**: a malformed/unknown correlation on an observation is *skipped*, never raised — the detect-pass is a read pass and must never reject a valid observation or DoS the ingest path.

The reconciliation runner (§6) pushes reality as generic observations, then invokes detect; an operator can also invoke detect directly.

> ### ⚠️ Deviation from ADR-0002 — requires Devon's knowing approval
>
> **ADR-0002 (line 40-42) states the orchestrator's "only new logic is conflict-detection *on observation ingest*."** The approved package uses the same "on ingest" language (`package.yaml` scope). **This design deviates: detection is moved OFF the ingest path into an operator/SYSTEM-invoked detect-pass.**
>
> **Why the deviation is necessary** — the adversarial review found that in-transaction ingest detection is not merely awkward but *unimplementable*, in three independent ways:
> 1. A **rejected** ingest (e.g. the digest-mismatch guard, `deployment_observations.py:415-420`) rolls the transaction back, **erasing any condition written first**. No condition can ever survive a rejected ingest.
> 2. **Split-brain is not ingest-knowable**: `_validated_subject` (`deployment_observations.py:400-405`) *requires* the bound implementation unit be `COMPLETED`, and the post-deploy unit is minted `SUBMITTED` in the same transaction — zero seconds old. "Verification stalled" only becomes true *later*.
> 3. **Write-once observation semantics** (`unique(source_system, source_reference, normalized_fact_hash)`, with `observed_at` inside the fact identity) make the runner's re-push either dedup-return *before detection re-runs* or raise `observation_conflict`.
>
> **What is preserved:** every invariant ADR-0002 exists to protect — the orchestrator stays push-only (the detect-pass makes no outbound call; it is pure DB read + append-only write) and loop-free (invoked on demand, never a background loop). Only the *mechanism* narrows, not the guarantee.
>
> **Consequence:** ADR-0002's Consequences bullet should be amended to say "conflict-detection driven by pushed observations, evaluated in an operator-invoked detect-pass." Devon is asked to approve this deviation explicitly as part of the AC-012 design approval.

### 1.2 The `reconciliation_required` condition — append-only model

New append-only `reconciliation_conditions` (+ sibling `reconciliation_resolutions`), both carrying the `reject_append_only_mutation()` `BEFORE UPDATE OR DELETE` trigger (precedent: migration `0012_ws61_observations`).

```
reconciliation_conditions
  id                          UUID PK
  work_unit_id                UUID FK -> work_units.id
  observation_kind            TEXT   ("github_pr" | "github_check" | "deployment")   -- discriminator
  observation_id              UUID   NULL  FK -> observations.id
  deployment_observation_id   UUID   NULL  FK -> deployment_observations.id           -- dual nullable FK per kind
  condition_type              TEXT   ("external_merge_alarm" | "pr_state_divergence" |
                                      "check_result_flip" | "deploy_split_brain" | "digest_divergence")
  stored_state                JSONB  (bounded)
  observed_state              JSONB  (bounded)
  normalized_divergence_hash  TEXT   (declared; = sha256 over normalized (kind, condition_type, key facts))
  detail                      TEXT
  detected_at                 TIMESTAMPTZ
  event_id                    UUID
  idempotency_key             TEXT UNIQUE   (= "reconcile:{work_unit_id}:{observation_kind}:{normalized_divergence_hash}")
  UNIQUE (work_unit_id, observation_kind, normalized_divergence_hash)   -- dedup identical re-detection
```

`reconciliation_resolutions (id, condition_id FK, resolved_by, decision, rationale, event_id, idempotency_key)`. **Open condition = no resolution row** (set-difference, mirroring evidence supersession). No UPDATE/DELETE ever touches a condition. Detect emits `reconciliation.required`; operator acknowledgement emits `reconciliation.resolved`. The `idempotency_key` is namespaced (`reconcile:…`) so it never collides in the globally-unique `events.idempotency_key`.

### 1.3 Correlation — how a PR/check observation finds "its" unit

- **Deployment (AC-003):** via the existing `ReleaseArtifactBinding.work_unit_id` and the post-deploy unit (`deployment_observations.py`), read by the detect-pass.
- **`github_pr` / `github_check` (AC-001/002):** the runner submits these as generic observations on the **first-class `subject_type="work_unit"` / `subject_reference`** channel (`OBSERVATION_SUBJECT_TYPES`), tagged with the in-flight unit id. The detect-pass reads by subject, then **cross-checks the observation's PR identity (number + head sha in facts) against the unit's `unit_pr_binding`** before recording — satisfying AC-001 "on the exact pull-request head" and blocking a wrong/forged `work_unit_id` from raising a false alarm on an unrelated unit. Malformed correlation → skip (fail-open).

**Runner re-push (B-2 fix):** the runner uses a **per-snapshot `source_reference`** — `pr:{number}@{head_sha}#{pass_id}`, `check:{ac}@{sha}#{pass_id}` — so each distinct observed reality is a new append-only observation (never `observation_conflict`); exact re-delivery of one snapshot is deduped by `idempotency_key` (AC-007).

- **`pass_id` must be strictly monotonic and unique per pass** (m-1). `observed_at` is inside the fact identity (`observations.py:365`), so a *reused* `pass_id` with a changed timestamp yields same-`source_reference`/different-facts → `observation_conflict` (`observations.py:166-179`). Generator: a per-run monotonically increasing ordinal (run start timestamp + sequence), minted once per runner pass and never reused.
- **"Current" observation for a `(unit, kind)` = newest, ordered `(observed_at, received_at, id)`** — mirroring the existing ordering (`observations.py:123`), **not** `observed_at` alone (m-2): a clock-skewed runner restart would otherwise make a stale snapshot "current." The monotonic `#{pass_id}` ordinal already embedded in `source_reference` is the authoritative tiebreak and is asserted by a regression test. This computed newest-wins over append-only rows *is* AC-002's "later `github_check` supersedes the earlier under append-only supersession" — no supersedes column needed — and the detect-pass re-runs against the newest each pass.
- **Fact shape must survive the secret scanner** (m-7). `SECRET_KEY_PARTS` includes `"log"` (`observations.py:33-44`), so GitHub's standard `logs_url` field is **rejected outright**. The runner therefore normalizes to an explicit, bounded fact schema (`pr_number`, `head_sha`, `state`, `merged`, `check_name`, `conclusion`, `deploy_status`, `artifact_digest`, `observed_at`) and never forwards raw provider payloads.
- **Source system** (m-8): the runner's observations use the existing `"github"` `OBSERVATION_SOURCE_SYSTEMS` member for PR/check facts. Deploy/health facts require either an existing member or an explicit enum extension — **if an extension is needed it is a listed migration**, not an incidental one (resolved during implementation; the enum is checked first).

**Detect-pass concurrency and torn reads (m-3):** two concurrent detect-passes cannot double-insert — the `UNIQUE (work_unit_id, observation_kind, normalized_divergence_hash)` catches it as dedup (IntegrityError → replay-return, the same race pattern the evidence path uses). To avoid a **torn read** (a detect running mid-push evaluating half a runner pass and recording a permanent false condition), the detect-pass evaluates **only the newest *complete* pass** — the runner stamps a pass as complete (its final observation carries `pass_complete: true`), and detect ignores an in-flight pass. A condition recorded against a pass that is later contradicted is closed by an operator `reconciliation.resolved` row (§1.2); conditions are never silently mutated.

### 1.4 Detection rules

- **AC-001 `github_pr`:** newest PR observation for the unit, cross-checked to `unit_pr_binding`, compared to stored lifecycle state.
  - Observed **merged** on a unit not yet `completed` → `external_merge_alarm` (never merges, never completes; records the condition).
  - Observed **closed**, or **head changed** *after verification read a head* (§1.6), diverging from expectation → `pr_state_divergence`.
  - On a `completed` unit: recorded informationally at most; **never** un-completes (§1.5).
- **AC-002 `github_check`:** newest check per `(unit, ac)` wins. A result that **flips** (success when verification read it → later failure) → `check_result_flip`, `reconciliation_required`. Never auto-un-completes.
- **AC-003 `deployment` split-brain (B-1 fix):** detected by the detect-pass, **off the ingest path**. The **existing `DeploymentObservation` row already proves the deploy succeeded** — it only exists because an accepted deployment ingest created it — so detection needs **no runner-pushed deploy observation as a precondition** (m-9). The predicate is: a binding has a `DeploymentObservation` **and** its **post-deploy verification unit has sat in `SUBMITTED` longer than a config-injectable threshold** (`RECONCILE_SPLIT_BRAIN_STALL_SECONDS`, measured from the unit's `created_at` / `post_deploy_verification.created` event, `deployment_observations.py:156-263`) without reaching completion → `deploy_split_brain`. A normal in-progress verification (under threshold) is explicitly non-conflicting. Separately, a runner-reported artifact digest differing from `binding.artifact_digest` (a *read*, never at a rejected ingest) → `digest_divergence`. The existing digest-equality guard on the trusted deploy-monitor ingest path (`deployment_observations.py:415-420`) is unchanged. Because the predicate is a pure read over rows the orchestrator already owns, the drill can drive it by lowering the threshold — no real sleep, no private time manipulation.

### 1.5 Never-mutate-completed guard

`record_reconciliation_condition(...)` performs **no** `work_unit` write and **no** transition — only inserts into the two append-only tables + `events`. A protocol test drives a `completed` unit through every detection path and asserts `state == completed` and `version` unchanged. Structural proof of failure-modes #3/#4.

### 1.6 `unit_pr_binding` and the head-change alarm (M-11 fix)

`unit_pr_binding (work_unit_id, pr_number, head_sha, verification_read_head_sha NULL, updated_at)` records the unit's PR head. Pre-verification, the worker's own PR-open/update evidence updates it (rebases/force-pushes are normal and **do not alarm**). When verification reads the head, `verification_read_head_sha` is captured. The **head-change alarm arms only after that**: a runner-observed head differing from `verification_read_head_sha` → `pr_state_divergence`. This makes AC-001's "exact PR head" decidable and immune to legitimate iteration.

---

## 2. AC-004 — lease-expired evidence-attach recovery

**Gap:** `evidence._validate_attempt` (`evidence.py:593-625`) hard-rejects worker evidence once `lease_expires_at <= now`; the reclaim path (`claims.py:186-295`) fails+re-readies and starts a fresh attempt, orphaning the prior attempt's produced-but-unsubmitted evidence.

**Design — a narrow, higher-authority, attempt-scoped recovery:** `POST /api/v1/work-units/{id}/attempts/{attempt}/recover-evidence` (+ CLI), authorized for **SYSTEM/operator, not the expired worker** (closes the "worker declares completion / attaches work it didn't do" hole). Guards (M-5):

**What AC-004 actually promises (and what this design must NOT over-claim).** Package AC-004 (`package.yaml:127`) promises the expired worker's evidence has *"a defined, attributable path to attach that evidence to the corresponding attempt **without re-executing the work**"*, and that *"the worker still cannot transition the unit to completed."* **It does not promise completion without a new attempt.** An earlier draft of this design claimed the recovered evidence "can feed the normal verifier→human completion" — that is false and is retracted: `FAILED` has no edge to `SUBMITTED`, and only `WORKER_EDGES` reach `SUBMITTED` (`transitions.py:29`) while `VERIFIER_EDGES` begin there (`:34-45`), so *some* worker attempt must submit. The value delivered is that the **work is not redone** — the recovered evidence is attached and reusable by attempt *n+1*, which short-circuits (submits without re-executing) rather than re-running the job.

**Preconditions (F1 fix — the v2 draft's precondition was unreachable).** `released_at` and `terminal_reason` have exactly one writer in the tree — `_perform_reclaim` (`claims.py:251-252`) — so requiring `released_at != NULL AND terminal_reason == "lease_expired"` made recovery reachable *only after* reclaim, and reclaim either mints attempt *n+1* with a live lease (whereupon the newer-evidence guard refuses recovery) or leaves the unit `FAILED` with no route forward. The scenario AC-004 names was therefore unreachable. Corrected preconditions:

- Target claim is **expired**: `lease_expires_at <= now`, **either still unreleased (`released_at IS NULL`) or released with `terminal_reason == "lease_expired"`**. The unreleased case is the AC's actual scenario (lease lapsed just before submit, reclaim not yet run).
- If the claim is still unreleased, recovery **releases it and SYSTEM-fails the unit** (`CLAIMED/EXECUTING → FAILED` are SYSTEM edges, `transitions.py:18-19`) **without minting a new attempt** — routed through a **shared release primitive** factored out of `_perform_reclaim`, so `released_at`/`terminal_reason` keep exactly one writer.
- Refuses `COMPLETED`/`CANCELLED` units.
- Records against **that specific prior `attempt`**, provenance-tagged (`recovered_from_expired_lease`, original `claim_id`).
- **Refuses if any newer attempt already has evidence for that AC** — `current_evidence` (`evidence.py:152`) selects the terminal head across *all* attempts, so this prevents stale recovered evidence from silently becoming the CURRENT evidence feeding `_completion_satisfied`.
- Takes the **`WorkUnit` row lock** (`with_for_update`, as evidence writes already do at `evidence.py:529`) and **re-checks the newer-attempt guard under that lock**; carries the standard idempotency-key + replay contract every other mutation has.
- Does **not** re-open the lease; does **not** transition toward `completed`.

**Reclaim interaction:** the two paths are disjoint by precondition. Recovery handles the *unreleased-expired* claim (releasing it itself, no new attempt). If reclaim already ran and started attempt *n+1*, recovery is admitted only while that attempt has no evidence for the AC; once it does, recovery is refused. Exactly one writer of `released_at` means no double-release or orphaned claim. A guard test proves a worker still cannot reach `completed` via any edge after recovery.

---

## 3. AC-005 / AC-006 — dead-letter view + operator recovery

### 3.1 Dead-letter view (AC-005, read-only, live-derived)

`GET /api/v1/dead-letter` (SYSTEM/operator) + `orchestrator dead-letter` CLI. Enumerates, computed live from source tables (no new materialized state):

1. **Terminally-failed and blocked units** — `WorkUnit.state IN ('failed','blocked','cancelled')`, with the latest claim's `terminal_reason`. (Includes `blocked` so `requeue` targets are in the same surface the actions operate on — M-16.)
2. **Failed/blocked dispatch records** — `DispatchRecord.status IN ('failed','blocked')` + `reason_code`.
3. **Open failure-signature circuit breakers** — *derived*. The live predicate is `len(prior failed/blocked with this signature) + 1 >= threshold`, evaluated **pre-insert** (`dispatch.py:384-397`); an at-rest row count is therefore **off by one** against it (m-12). The view does not restate the arithmetic: `_opens_circuit` is factored into a single shared function that both the breaker (pre-insert) and the view (at-rest) **call**, so they cannot drift. A post-retry same-signature failure re-blocks and the item reappears — documented operator semantics.

Read-only; the WS-3.2 GET-only guard is mirrored, and the new GET routes are added to the pinned route inventory (`test_scope_guards.py`) deliberately (m-13).

### 3.2 Recovery actions (AC-006) — compose existing guarded edges only, with an explicit surface/role per action (M-8/M-10)

| Action | Precondition | Edge / service | Role & surface |
|---|---|---|---|
| **retry** | `FAILED`, attempts **exhausted** | `claims.authorize_retry` (raises `max_attempts`, `FAILED→READY`) | **HUMAN** → the **existing** `/review/units/{unit_id}/retry` human route (`test_scope_guards.py:78`) — no new router needed (M2M `/api` strips human headers). |
| **requeue** | `FAILED`/`BLOCKED`, attempts **not exhausted** | SYSTEM `FAILED/BLOCKED→READY`; actor attributed then role forced SYSTEM (`claims.py:91` pattern) | **SYSTEM** → `/api` M2M. **Refused if `attempt_count >= max_attempts`**, and **reuses the existing readiness check** (`claims.py:464-473`) before the transition (m-5) — otherwise the unit lands `READY`, `claim_unit` rejects it (`claims.py:66`), and it drops out of the failed-units view: invisible *and* unrunnable. |
| **cancel** | legal `*→CANCELLED` (`AWAITING_APPROVAL`/`FAILED→CANCELLED`) | transition | Role per edge; human `cancel` → `/review`. (No `BLOCKED→CANCELLED` edge exists — a blocked unit is requeue-only, documented.) |

Every action is attributable, idempotent, and **cannot declare completion, grant a waiver, or merge** — a test enumerates each action's reachable transition targets and proves `COMPLETED`/waiver/merge are excluded (they are, structurally: no `WORKER_EDGES→COMPLETED`, no merge edge in `LEGAL_EDGES`).

---

## 4. AC-007 — duplicate-delivery idempotency audit + gap closure

Nearly every ingress already has the mechanism (advisory lock + unique `idempotency_key` + replay-equality) **and** a regression test. AC-007 is an **audit that proves** every event/evidence/observation ingress is idempotent under duplicate delivery **against PostgreSQL**, adding a test to any path lacking one:

- A checked-in coverage matrix (`tests/idempotency/`) — path → mechanism → test — each row a real Postgres double-submit asserting one row + identical response.
- Explicit tests for the two consistency gaps: the **lifecycle-transition path** (unique Event key + `with_for_update`, no advisory lock — prove double-submit safe under concurrency) and the **reclaim compound-key path** (`:failed`/`:ready` suffixes).
- All new WS-P2.1 ingress (reconciliation condition/resolution, recover-evidence, recovery actions) get duplicate-delivery tests from birth.

No new idempotency machinery is invented where one exists.

---

## 5. AC-008 — projection-vs-source consistency check (independent recomputation)

No materialized projections exist; "projections" are computed live from append-only source tables. Per ADR-0002 this is a **check, not a rebuild**. `orchestrator check-consistency` + `GET /api/v1/consistency-check` re-derives each projection **with an independent recomputation** and reports divergence (never repairs, never crashes):

- **Critically (M-9):** the check does **not** reuse `_terminal`/set-difference helpers — on a two-headed chain `_terminal` *raises* (`evidence.py:853-854`), which would crash instead of report. Instead it uses a SQL-level group/count of unsuperseded heads per `(unit, ac)` and flags any `count != 1`.
- Invariants: status-ledger current-evidence agreement; terminal-chain single-head; completion integrity (every `completed` unit's required ACs have satisfied terminal adjudications); no open `reconciliation_conditions` implying an illegal auto-mutation.
- **Evidence (AC-008):** reports **zero divergence on a clean fixture** and **the seeded divergence on a corrupted (two-headed) fixture** — both built and asserted.

---

## 6. AC-009 — the separate report-only reconciliation runner

Mirrors `factory-runner`: a distinct process/entry point that **ships in the orchestrator repo** but **shares no import path** with request-handling code.

- New top-level package `src/reconciliation_runner/` with `[project.scripts] reconciliation-runner = "reconciliation_runner.cli:app"`, its own `httpx` client, `pydantic`/`typer` only. **Imports nothing from `orchestrator.*`** (incl. `persistence` — M-7), enforced by a `tests/architecture` guard modeled on `test_application_has_no_external_mutation_integrations` (`test_scope_guards.py:10-25`).
- Reads in-flight units + their PR bindings via a new **`GET /api/v1/in-flight-units`** (SYSTEM, read-only — m-15), pulls PR/CI/deploy/health reality (fixture/recorded in scope; live wiring out of scope), and **pushes only via `POST /api/v1/observations`** as a **SYSTEM** actor (bearer + `X-Credential-Key-Id`, factory-runner's auth).
- **Report-only mandate (M-6):** the runner calls **only `/observations`** — never `…/deployment-observations` (which mints post-deploy units + evidence). A test asserts the runner client references no lifecycle-mutation/deployment-binding endpoint, and that a full runner pass **transitions/creates no unit** (it only inserts observations, which the operator/detect-pass then evaluates).
- **Operator-invoked**; no scheduler/cron in scope (deferred per ADR-0002).

---

## 7. AC-010 — four scripted recovery drills

Four re-runnable scripts joining the existing `scripts/` dir (which today holds `build_registry_bundle.py`), each in the **`restore-drill.sh` mold**: `#!/bin/bash`, `set -euo pipefail`, config constants, `--keep`, disposable scratch (`mktemp -d`, PID-suffixed throwaway Postgres container, throwaway DB name **≠ `orchestrator_test`**), `trap cleanup EXIT` idempotent teardown, `die()` vs accumulating `fail()`, timestamped `log()`, explicit `[...] || fail`, **exit 0 = PASS**. Read-only toward production/shared systems; drive the orchestrator only through **public API/CLI**.

1. **Orchestrator dies after dispatch** — dispatch recorded, process killed; on restart the unit is recoverable via reclaim/requeue; assert no orphaned canonical state.
2. **Evidence submission fails after worker completion** — a worker claims, produces its result, and its lease lapses **before submit** (claim left *expired-but-unreleased* — the AC-004 scenario, no reclaim run). The drill invokes `recover-evidence` on that attempt and asserts: the evidence **attaches** to attempt *n*; recovery **releases the claim and SYSTEM-fails the unit without minting a new attempt**; the **worker cannot reach `completed`**; and the recovered evidence is **reusable by attempt *n+1***, which submits **without re-executing the work** (the actual AC-004 promise — attachment and no-rework, *not* completion without a new attempt).
3. **PR merged outside the session** — push a `github_pr` merged observation for a not-yet-completed unit, run detect; assert `external_merge_alarm` recorded, unit not auto-completed/merged.
4. **Deployment succeeds while verification times out** — push a "deploy succeeded" observation for a unit whose post-deploy verification exceeds the **injectable** `RECONCILE_SPLIT_BRAIN_STALL_SECONDS` (m-14, set low in the drill), run detect; assert `deploy_split_brain` recorded, not silently accepted. No real sleep / private time manipulation.

Documented for the quarterly cadence.

---

## 8. AC-011 — the invariant scan

Extends the `tests/architecture/` scope-guard family, **respecting the existing dispatch/post-deploy allowlist**. Four assertions:

1. **No auto-merge** — extends `test_no_automatic_merge.py`; asserts no `WORKER_EDGES→COMPLETED` and no merge endpoint.
2. **No orchestrator-side outbound GitHub/Coolify call** — **repo-wide** `httpx.`/`requests.`/`coolify` scan over `src/orchestrator/**` **minus** the allowlisted dispatch/App/Brain files **and `cli.py`** (client `httpx`; the new recovery CLI lands there — M-7). The new reconciliation/recovery orchestrator files are in the *forbidden* set.
3. **No background loop/scheduler/cron** — new AST/string scan for `while True`, `create_task`, `BackgroundTasks`, `apscheduler`, `cron`, `Thread`, `schedule:` over `src/orchestrator/**` (empirically clean today; unguarded).
4. **Workers cannot complete** — structural assertion over `transitions.py` role-edge tables (no `WORKER_EDGES` target is `COMPLETED`).

Plus a **no-tracked-secret** assertion wired to the security scanner (the `bws-scan-gate` mold) so the branch carries the evidence.

---

## 9. AC-012 / AC-013 — the human gates (retained)

AC-012 (this design, after adversarial review) and AC-013 (merge) are **retained** — Devon owns both out-of-band. This design being approved *is* AC-012; the merge after Quality-green is AC-013. No lifecycle state gates on them (a human can only `waive`, not `pass`; design pre-dates code, merge post-dates completion).

---

## 10. Data-model / migration plan

One forward-only Alembic migration adds: `reconciliation_conditions` + `reconciliation_resolutions` (both with the append-only trigger, dual-nullable-FK + discriminator per §1.2), `unit_pr_binding` (§1.6), their unique constraints/FKs. Evidence-recovery adds **no** column (provenance in the bounded `Evidence` payload). **No** column is added to `work_units`. Downgrade drops the new tables. The new config `RECONCILE_SPLIT_BRAIN_STALL_SECONDS` (default e.g. 900) lands in `config.py` (env-overridable).

---

## 11. Test strategy

TDD throughout, Postgres-backed, mirroring `tests/services/conftest.py` (drop/recreate schema + `alembic upgrade head`), honoring the separate-runtime-DB invariant (never `orchestrator_test` for dogfood state).

- `tests/services/` — detect-pass per AC-001/002/003 (merged/closed/head-change/flip/split-brain/digest, incl. completed-unit no-mutation + fail-open-on-bad-correlation), evidence-recovery guards (AC-004), dead-letter enumeration + derived breaker (AC-005), recovery reachable-target proof (AC-006), consistency check clean+corrupt (AC-008).
- `tests/idempotency/` — the AC-007 duplicate-delivery matrix against Postgres.
- `tests/api/` + `tests/cli/` — new GET (dead-letter, consistency-check, in-flight-units) and POST (recover-evidence, recovery, reconcile-detect) surfaces.
- `tests/architecture/` — the AC-011 four-part scan + runner import-isolation guard.
- `tests/reconciliation_runner/` — pulls fixture reality, pushes only `/observations`, transitions/creates no unit (AC-009).
- `scripts/` — the four AC-010 drills, shellcheck-clean, exit-0 on happy path.

All AC-001..011 evidence lands on the PR-head **Quality** check; the eleven mapped ACs are adjudicated `passed` by a VERIFIER credential (the known WS-P2.3 manual-adjudication runbook); Devon completes + merges.

---

## 12. How the design forecloses each named failure mode

| Package failure mode | Foreclosed by |
|---|---|
| In-process poller calling GitHub/Coolify | §6 runner is the only puller; §8 repo-wide outbound scan (minus allowlist) |
| Background loop/scheduler in orchestrator | §8 no-loop scan; runner operator-invoked, out-of-process |
| Conflict detection auto-un-completes/auto-resolves | §1.5 never-mutate guard + protocol test; detection is a read pass |
| Runner sets canonical lifecycle state | §6 runner restricted to `/observations`; report-only test (no unit transitioned/created) |
| Evidence-attach lets worker declare completion | §2 SYSTEM/operator-only, attempt-scoped, newer-evidence guard, no completion edge |
| Recovery actions bypass guards | §3.2 compose existing edges only; reachable-target proof; per-action role/surface |
| Scope creep into replay/rebuild engine | §5 consistency check only (independent recomputation), ADR-0002 |
| Drill mutates real/shared state | §7 disposable scratch, trapped teardown, read-only toward prod, public-API-only, injectable threshold |

---

## 13. Residual items for the task-level implementation plan

- Default value + operator guidance for `RECONCILE_SPLIT_BRAIN_STALL_SECONDS` (the drill sets it low; production wants it above a normal verification's worst-case).
- Whether a unit's PR head is already recoverable from existing evidence (avoiding a distinct `unit_pr_binding` table) — confirm against code during implementation; the table is the fallback.
- Whether the runner's deploy/health facts need an `OBSERVATION_SOURCE_SYSTEMS` enum extension (a *listed* migration if so) or fit an existing member (§1.3).
- Factoring the shared claim-release primitive out of `_perform_reclaim` so `released_at`/`terminal_reason` keep exactly one writer (§2).

*(Resolved, no longer open: the `retry` human route already exists at `/review/units/{unit_id}/retry` — `test_scope_guards.py:78`; the `source_reference` grammar and `pass_id` generator are specified in §1.3.)*

---

## 14. What the approver is being asked to accept

1. **The architecture** — ADR-0002's Model C: a separate report-only reconciliation runner; conflict detection as an operator-invoked detect-pass; a consistency *check*, not a rebuild engine.
2. **One explicit deviation from ADR-0002 and the package's "on ingest" language** (see the boxed note in §1.1) — detection moves off the ingest transaction, because in-transaction detection is provably unimplementable in three independent ways. Every invariant the ADR protects is preserved; only the mechanism narrows. **ADR-0002's Consequences bullet should be amended to match.**
3. **A scoping correction on AC-004** (§2) — the package promises evidence *attachment without re-executing the work*, not *completion without a new attempt*. This design delivers exactly what the AC promises; an earlier draft over-claimed and has been retracted.
