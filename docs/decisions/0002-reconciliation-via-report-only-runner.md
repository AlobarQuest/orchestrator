# ADR 0002 — Reconciliation runs in a separate report-only runner, not the orchestrator process

**Date:** 2026-07-11
**Status:** Accepted (decision confirmed with Devon; formalized by WS-P2.1)
**Workstream:** WS-P2.1 (recovery controls + drills) — Program Phase 2, Wave 1
**Companion:** `~/docs/software-delivery-system/2026-07-09-program-phase2-post-mvp-plan.md`; intent package `ws-p2.1-recovery-controls-drills` (AlobarQuest/intent-packages)

## Context

Program Phase 2 Wave 1 requires the orchestrator to reconcile its stored lifecycle
state against external reality — pull-request state, CI results, deployment success,
and health. The plan's WS-P2.1 line reads "reconciliation against GitHub and deploy
state (PR changed/closed/merged externally, CI rerun)," which on its face suggests the
orchestrator should reach out and check GitHub and the deploy platform.

That reading collides with two load-bearing invariants of this codebase:

1. **Push-only core.** The orchestrator is a closed event store. It pushes dispatch
   *out* and ingests pushed observations; the observation, release, and post-deploy
   paths must not call GitHub or Coolify (`docs/operations/observation-ingestion.md`,
   `release-immutability.md`, `post-deploy-verification.md`). External truth arrives
   only as `Observation` rows submitted by trusted SYSTEM actors.
2. **No autonomous background loop.** Runtime recovery goes through public API/CLI
   surfaces; there is no scheduler, cron, or `while True` inside the orchestrator
   process (`PROJECT.md`, `CLAUDE.md`).

A reconciliation feature implemented as an in-process poller that calls GitHub/Coolify
on a timer would break both invariants at once.

## Decision

**The invariants constrain the orchestrator *process*, not the *system*.** Reconciliation
is therefore performed by a **separate, report-only reconciliation runner** — the same
species as `AlobarQuest/factory-runner` — that pulls pull-request, CI, deployment, and
health reality for in-flight units and **pushes the observed reality back** through the
orchestrator's existing public observation API.

Consequences of this shape:

- The orchestrator stays **push-only and loop-free**. Its only new logic is
  conflict-detection on observation ingest (record `reconciliation_required` when a
  pushed observation disagrees with stored state) plus operator recovery commands.
  **⚠️ Amended 2026-07-11 — see the Amendment section below. This bullet is inaccurate
  on two counts: the split-brain detection mechanism, and the omission of the recovery,
  dead-letter, and consistency-check surfaces.**
- The runner **reports only** — it pushes observations and never sets canonical
  lifecycle state, mirroring the standing invariant that workers never declare
  completion. Deterministic gates and the operator decide recovery.
- The runner **ships from the orchestrator repository as a distinct process and entry
  point** (not a new repository), sharing no import path with the orchestrator's
  request-handling code, so the orchestrator process itself remains free of any
  GitHub or Coolify call.
- The runner is **operator-invoked first**; a scheduled trigger is deferred to a
  separate, later decision.

## Alternatives considered

- **A — passive push-only (no runner).** The orchestrator reconciles only whatever
  observations CI/deploy monitors already push. Simplest and adds no component, but it
  cannot actively catch drift that nobody reported — leaving the marquee cases (a deploy
  that succeeded while verification timed out; a PR merged externally with no observation
  submitted) open. Rejected as under-delivering the point of the workstream.
- **B — operator-invoked pull inside the orchestrator.** A `reconcile` command that makes
  the orchestrator itself read GitHub/deploy once. Closes the gap without a new component
  but punches a hole in the "orchestrator must not call GitHub/Coolify" injection-containment
  invariant. Rejected.
- **C — separate report-only runner (chosen).** Preserves every invariant, actively
  reconciles against reality, and reuses a pattern already proven three times (factory-runner,
  plus the external scheduled reconcilers `infra-drift` at 3AM and `change-window` at 4AM).

## Related narrowing — no projection-rebuild engine

The plan also lists "projection rebuild." This architecture has **no replayable
event-derived projections**: projections are computed live from the append-only source
tables, which are themselves canonical and protected by a database append-only trigger.
There is no derived state that can drift and be rebuilt from an event log, so WS-P2.1
narrows this item to a **projection-vs-source consistency check** (an operator-invocable
audit) rather than building an event-replay engine, which would be a YAGNI violation and
contradict the "tables are the source of truth" design.

## Cost / trade-off accepted

One additional small deployable/process to build and operate, and reconciliation
reconciles against *reported* reality delivered by the runner — i.e. it trusts the runner
as a SYSTEM actor, the same trust model as every existing observation. Both costs were
judged well worth actually closing drift while keeping the canonical core inviolate.

---

## Amendment — 2026-07-11 (WS-P2.1 design approval, AC-012)

**Status:** Accepted. Approved by Devon at the AC-012 gate and recorded as a chained
factory event (`package.design_approved`, `evt-728df5d0afd34572aa96e5794a3002ce`).
**Design:** `docs/superpowers/specs/2026-07-11-wsp21-recovery-controls-drills-design.md` (v4).

The Decision above stands in full: reconciliation is performed by a separate,
report-only runner; the orchestrator stays push-only and loop-free; there is no
projection-rebuild engine. Two statements in the Consequences need correcting, both
found by the adversarial architecture review that AC-012 mandates.

### 1. Conflict detection is *hybrid*, not uniformly "on observation ingest"

The original bullet says the orchestrator's "only new logic is conflict-detection **on
observation ingest**." That is achievable for most paths but **provably impossible for
one**:

| Path | Where detection runs | Why |
|---|---|---|
| `github_pr` (AC-001) | **on ingest** (post-commit, own transaction) | as originally intended |
| `github_check` (AC-002) | **on ingest** (post-commit, own transaction) | as originally intended |
| `digest_divergence` | **on ingest**, incl. on a *rejected* ingest (recorded at the route layer) | the digest guard raises and rolls back, so the condition must be written outside that transaction |
| **`deploy_split_brain` (AC-003)** | **operator/runner-invoked detect-pass** | **not knowable at ingest under any design** |

The split-brain case is structural, not a shortcut: the post-deploy verification unit is
minted `SUBMITTED` *inside* the deployment-ingest transaction (zero seconds old), and
`_validated_subject` requires the bound implementation unit already be `COMPLETED`.
"Deployment succeeded while verification timed out" is a statement about **elapsed time**
and cannot be true at the instant of ingest. It is therefore detected by a detect-pass
(`POST /api/v1/reconciliation/detect`) comparing elapsed-since-`SUBMITTED` against a
configurable threshold — plus, to close the case ADR-0002 rejects Alternative A for
(drift *nobody reported*), a runner-reported deploy for a release binding that has **no**
post-deploy verification unit at all.

Detection is also, critically, **never inside the ingest transaction** — a rejected ingest
would roll the condition back and erase it.

**Every invariant this ADR exists to protect is preserved.** The detect-pass makes no
outbound call (it is a pure database read plus an append-only write) and is invoked on
demand — it is not a background loop, scheduler, or cron. Only the *mechanism* narrows;
the guarantee does not.

**Governance note:** the approved package (`ws-p2.1-recovery-controls-drills` rev 1, hash
`135af657…`) carries "reconciliation-on-ingest" in its title and `scope.included`. The
acceptance criteria themselves are mechanism-neutral — they require that the orchestrator
*records* a `reconciliation_required` condition, not where detection runs — so all ACs
remain satisfiable and the gate chain (intake, decomposition, per-unit authority approval)
is undisturbed. The residual AC-003 deviation was approved knowingly and is recorded in the
tamper-evident factory-events chain rather than only in prose.

### 2. The orchestrator gains more than conflict detection + recovery commands

The original bullet undercounts the in-process surface. WS-P2.1 also adds, all reachable
only through public API/CLI and none of them a background loop or an outbound call:

- an append-only `reconciliation_conditions` / `reconciliation_resolutions` model, with an
  operator resolution surface (a condition with no resolution row is *open*);
- a **dead-letter view** over terminally-failed/blocked units, failed/blocked dispatch
  records, and derived open failure-signature circuit breakers, plus operator
  retry/requeue/cancel that compose only existing guarded transitions;
- a **lease-expired evidence-attach recovery** path (SYSTEM/operator, never the expired
  worker; it supersedes the current evidence head rather than forking the supersession
  chain, which would otherwise wedge the unit permanently);
- a **projection-vs-source consistency check** (an operator-invocable audit — a *check*,
  never a rebuild, exactly as the "Related narrowing" section requires).
