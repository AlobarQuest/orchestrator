# ADR-0005 — Production-drill disposition (remediation item 0.3)

**Date:** 2026-07-27
**Status:** Accepted 2026-07-27 by Devon — **disposition A** (the recommendation: run the five drills against production with drill-scoped resources). Execution waits for PR #82 to merge (Devon is holding the merge until WS-P2.7 Inc-2 completes) and then happens in a dedicated, explicitly authorized session per the prerequisites below.
**Numbering note:** 0004 is reserved for WS-P2.7 Inc-2's inbound-reconciliation decision, authored on its own branch.

## Context

Program exit criterion #5 ("crash, retry, and reconciliation drills pass") was marked MET on
2026-07-12 on the strength of the five scripted drills — which run against a throwaway local
Postgres and a throwaway uvicorn, and have never touched production. The 2026-07-12 remediation
order (item 0.3) requires the drills to run **against production**; the 2026-07-13 stabilization
checkpoint planned an ADR for *how*, and that ADR was never written. This is it. The scorecard was
reconciled on 2026-07-27: #5 now reads NOT MET IN PRODUCTION and #7 DEPLOYED, NOT
PRODUCTION-PROVEN. Both close through whichever disposition is accepted here.

The tension this ADR resolves: the drills' value is proving *production* recovers, but the drills
were deliberately built to own everything they touch and destroy it afterward — properties the
production database does not offer for free.

## Dispositions considered

### A — Run the five drills against production with drill-scoped resources (RECOMMENDED)

Each drill runs against the live orchestrator (`sds.alobar.net`) using drill-prefixed units in a
drill-designated package revision, through the same public HTTP surfaces the local drills already
use, with `dispatch_enabled` off (its current standing state) so nothing fires a
`workflow_dispatch`. Drill resources are created, exercised, and driven to a terminal state
(`cancelled`/`failed`) in the same run; nothing is deleted from the ledger — terminal drill units
*are* the retained evidence.

- **For:** proves the real thing — the production database, the production image, the production
  proxy and auth chain. It is the only disposition that actually discharges 0.3 as written.
- **Against:** leaves terminal drill units in the production ledger (acceptable: they are labeled,
  terminal, and auditable); requires a temporary SYSTEM/worker credential exercise identical to
  what closeout observations already do; drill 1 (SIGKILL) and the lease-expiry setup need
  production-safe equivalents (see prerequisites).

### B — Production-shaped clone

Restore the latest production backup into a fresh, isolated database + container from the exact
production image; run the drills there.

- **For:** zero footprint on the live ledger.
- **Against:** proves the image and schema, not the deployment — proxy routing, forward-auth,
  env wiring, and the live migration head stay unproven, which is precisely the class of gap
  (merged-recorded-as-done) that created item 0.3. Also standing infra to build and keep honest.

### C — Accept local drills + live-route attestation as standing evidence

Record an accepted-risk decision that the local suite plus the 0.5 attestation guard satisfy
#5/#7, and mark them met-with-caveat.

- **For:** cheapest; the attestation guard does close the "routes not served" half.
- **Against:** re-accepts the exact gap the remediation order was written to close. The 2026-07-12
  finding was not "routes missing" but "recovery behavior never exercised where it matters."

## Recommendation

**A**, with the prerequisites and stop conditions below. B is a weaker proof at higher standing
cost; C is the status quo ante with better paperwork.

## Prerequisites (before the run)

1. This ADR Accepted by Devon, with the drill-adaptation notes reviewed.
2. A drill-designated package revision + drill-prefixed units authored through the normal intake
   gates, so drill traffic is first-class, labeled, and traceable.
3. Per-drill production adaptations written and reviewed: drill 1's crash step becomes a
   container restart via the ops lane (or is explicitly waived for production with rationale);
   the drill 2/5 staleness setups use the env-overridable thresholds
   (`reconcile_split_brain_stall_seconds`, `dead_letter_stalled_approval_seconds`) — **never**
   an `updated_at` write, which the DB trigger silently overwrites.
4. A fresh production backup verified restorable (the vps-backup lane) immediately before the run.
5. The run happens in a dedicated session, explicitly authorized, with nothing else in flight.

## Stop conditions (abort the run, leave state as found)

- Running artifact identity (image digest) or migration head cannot be proven before starting.
- Live OpenAPI differs from the reviewed route contract.
- Any drill step would touch a non-drill-prefixed resource, require private SQL against
  production, or need a capability outside the standing credentials' roles.
- Any drill leaves a unit in a non-terminal state it cannot drive terminal through public
  surfaces. (Known constraint: no `(READY, CANCELLED)` edge exists — the drill unit design must
  avoid parking anything in `ready`, or this becomes permanent debris; see PROJECT.md backlog.)

## Closure

The run's evidence (per-drill transcript, unit IDs, image digest, migration head, HUMAN closeout)
lands as a dated evidence file in `~/docs/software-delivery-system/`, the scorecard cells for #5
and #7 are updated to MET with that citation, and the reconciliation block in
`2026-07-12-remediation-order.md` marks 0.3 closed.
