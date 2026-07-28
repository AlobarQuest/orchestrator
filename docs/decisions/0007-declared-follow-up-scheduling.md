# ADR 0007 — Declared follow-up scheduling: package supplies intent, orchestrator supplies timing, human supplies discharge

**Date:** 2026-07-28
**Status:** Accepted 2026-07-28 by Devon.
**Workstream:** WS-P2.8 (follow-up scheduling) — Program Phase 2, Wave 2 (LEGIBLE), final workstream.
**Precedents:** ADR-0002 (reconciliation via a separate report-only runner), ADR-0003 (tracker
projection is outbound-only, out-of-process), ADR-0006 (human gates are browser-only, permanently).
**Design:** `docs/superpowers/specs/2026-07-28-wsp28-follow-up-scheduling-design.md`
**Runbook:** `docs/operations/follow-up-scheduling.md`

## Context

WS-6.1 (observation ingestion) lists "create follow-up work units" as an explicit non-goal — the
observation spine is deliberately evidence-only, and an observation must never be able to create
canonical work. Independently, the `follow_up` block (`required`, `revisit_when`, `signals`,
`owner`) has been part of every approved intent package since WS-2.1, and until this workstream
nothing ever read it. A package author could declare "revisit this in 90 days if signal X appears"
and that declaration simply sat in the package YAML, unactioned.

This workstream closes that gap: D-MVP-4 ("Follow-up scheduling (`follow_up` → timed observation
units)") is the last unbuilt clause of the original Phase-6 scope.

## Decision

System-side minting is bounded to **declared** follow-ups, and to nothing else. The package — a
human-approved artifact — supplies the *intent*: whether an outcome should be revisited at all.
The orchestrator supplies only the *timing*: a bounded, configurable elapsed-time window computed
from when the revision's work settled. A human supplies the *discharge*: the actual judgment,
made by adjudicating a generated criterion and completing the minted unit through `/review`. The
orchestrator itself gains no scheduler — no thread, no loop, no timer anywhere in `src/`. See
`tests/architecture/test_wsp21_invariant_scan.py`, which continues to hold for this module because
it makes no outbound call and starts nothing on its own.

## Why a route and not a runner

Two existing modules independently forbid the two ways this could have been built as a
free-standing process:

- `reconciliation_runner/client.py` exists specifically to keep a runner-species process from
  minting units: "It would be setting canonical lifecycle state while calling itself a reporter."
  A new out-of-process runner that minted follow-up units would be exactly that contradiction.
- `reconciliation_detection.py` states plainly that it "never writes `work_units` and never
  transitions." Folding follow-up minting into the detect pass would break that statement for the
  one module in the codebase that most depends on it staying true.

So minting lives as a SYSTEM-only in-process route, `POST /api/v1/follow-ups/mint` — a pure
database read plus an append-only write, invoked on demand by an external caller. No outbound
call is made from inside it, so it needs no `OUTBOUND_ALLOWLIST` entry: there is no new HTTP
client anywhere in `src/`.

## Mechanical guarantees

Following the ADR-0003 four-item template — what bounds this capability, mechanically, not by
policy:

1. **The declaration is the pass's only source of "should this mint."** `mint_due_follow_ups`
   selects every `WorkPackageRevision` with no `WHERE` clause and decides in Python:
   `validate_follow_up` normalizes the row's `follow_up` block and `evaluate_due` mints only when
   `required is True`. There is no SQL predicate and no approval-status filter — every registered
   revision is an approved one, since `register_revision` is reached only through intake and
   decomposition approval. A revision with `follow_up = NULL` or `required: false` never mints,
   but the mechanism is a filter in `evaluate_due`, not a query the database enforces. Proved by
   planting both shapes and asserting zero mints, which is where this bound actually lives.
2. **The minted unit id is content-addressed** — `uuid5(NAMESPACE_URL,
   f"sds:follow-up:{revision_id}")` — backed by the existing `(work_package_revision_id, unit_key)`
   unique constraint. A revision cannot mint a second review unit even if the reporting-level
   already-minted check is somehow bypassed; the id collision is the structural backstop. **That
   id is also the unit's IDENTITY marker**: `lifecycle.is_generated_follow_up_unit` requires it,
   and every predicate that grants a unit the generated criterion in place of its package's real
   acceptance criteria goes through that one function. `required_capability` is authorable at unit
   ingress, so a capability-only marker would have let any unit claim the substitution; the `uuid5`
   is producible only by this pass.
3. **The minted envelope carries no mutation authority.** Its capability set is exactly
   `{follow_up_review: allowed}`, disjoint from `RUNNER_CAPABILITIES`, and carries no
   `constraints.target_repository` and no `allowed_commands`. `follow_up_review` is in neither the
   runner vocabulary nor `ORCHESTRATOR_ONLY_CAPABILITIES` — `_mint` constructs its unit directly
   and never passes through `validate_unit_capabilities`, so unit ingress refuses the capability
   outright and no authored unit can wear the marker. A minted unit therefore cannot be claimed or
   worked by a runner; admission would independently refuse it on `target_repository_missing` and
   `capability_not_enabled` even if it tried.
4. **Per-item fail-open with counted skips.** Each revision is evaluated inside its own SQL
   savepoint; a malformed declaration or a race on the unique constraint rolls back only that
   revision's attempt and is reported as a skip, never discarding units already minted earlier in
   the same pass. The pass commits once, at the end.

## This is not an observation→work bridge

**WS-P2.8 reads no observation, at any point.** The `Observation` table is never queried by any
part of this mechanism. The trigger is a human-approved package declaration; the timing is
orchestrator policy applied to that declaration and the clock; the discharge is a human. This
corrects an earlier framing in planning documents that called this "the sole sanctioned
observation→work bridge" — under the design actually built, it is a **declared-follow-up→work**
bridge instead, and the distinction is load-bearing: WS-6.1's non-goal ("observations cannot
create work") is not violated, because nothing here is triggered by an observation.

A future Phase-3 source that wants scheduled follow-up work does not get to acquire minting
authority of its own. It rides this mechanism the only way any source can: by **declaring a
follow-up** in a package it proposes, which routes through the existing human intake and approval
gates unchanged. The mechanism is source-agnostic by construction — nothing in it is specific to
any one caller.

## Scheduled trigger

Unlike ADR-0002 and ADR-0003, both of which deliberately deferred any scheduled trigger to "a
separate, later decision," **this ADR chooses to wire a scheduled trigger rather than defer one.**
The plan is a small additive step in `drift-audit.sh` (`AlobarQuest/infraops-mcp-server`), which
already runs daily at 03:00: add one more pass of the mint route to that run, non-fatal and
fail-open (a counted WARN on failure, touching neither the drift loop's exit code nor its other
steps — Healthchecks ping, Resend digest, change-manager sync, security-drift step), using the
`orchestrator-system` credential distinct from that script's own
`orchestrator-drift-reporter` observation-posting credential (the two identities must not be
conflated, since one is observe-and-propose-only and the other is the one authorized to mint).

**That wiring step is its own deliverable — WS-P2.8 Task 10 — shipping as a separately-mergeable
pull request in a different repository than this one.** This ADR records the decision to wire a
trigger and the shape it takes; it does not itself land the change. Until Task 10 merges,
`drift-audit.sh` holds only `orchestrator-drift-reporter` and contains no reference to minting at
all, and `scripts/run-follow-up-mint.sh` (run manually or from cron) is the only trigger. See
`docs/operations/follow-up-scheduling.md` for how to verify whether the daily step has landed.

Once it lands, this still does not put a scheduler inside the orchestrator. The orchestrator has
no loop either way; what changes is that an already-scheduled *external* operator job invokes a
route that already exists for on-demand use. The ADR-0002/0003 posture — the orchestrator process
stays push-only and loop-free — is honoured, not bypassed: the schedule lives entirely outside
`src/`, in a different repository, on its own merge cycle.

## Consequences

- **Forward-only. There is no backfill and none is possible.** Revisions intaken before migration
  `0020_wsp28_follow_up` have `follow_up = NULL`. The declaration cannot be recovered after the
  fact — the package YAML itself is never stored, only the derived intake payload, and
  `follow_up` was not part of that payload before this migration. No pre-existing revision can
  ever mint a follow-up review.
- **One follow-up per revision, forever.** There is no recurrence: once a review unit is minted
  and discharged, the revision cannot mint a second one. Recurrence is deliberately out of scope
  here and is owned by WS-P2.10, which will also need an intent-packages schema change (`MapSpec`
  requires every declared key present, so adding a recurrence field is not additive without schema
  work, and editing existing packages' YAML changes `package_hash` and invalidates their lineage
  approvals).
- **`FAILED` blocks minting, and nothing surfaces a `FAILED` unit as needing disposition.**
  `FAILED` is not terminal — `(FAILED, READY)` and `(FAILED, CANCELLED)` are both legal — so a
  package's outcome is not yet knowable while a `FAILED` unit sits undecided, and the review
  correctly does not mint behind it. But this cost is real and currently unbounded: `dead_letter`
  reports `FAILED` units, but reporting is not resolution, and nothing prompts an operator to act.
  Tracked as backlog item `6bcd7ee8b6b2`.
- **`docs/operations/observation-ingestion.md`'s non-goal "create follow-up work units" is
  superseded only in part** by this ADR — see that document's amended non-goal for the precise
  boundary. WS-6.1's ingestion path still creates nothing; a package-declared `follow_up` block
  now yields a work unit through this separate mechanism, which reads the declaration and the
  clock and never reads an observation.

## Alternatives considered

- **Fold minting into the reconciliation detect pass.** Rejected: `reconciliation_detection.py`'s
  own stated invariant is that it never writes `work_units`; minting there would directly
  contradict it.
- **A new out-of-process runner, mirroring the ADR-0002/ADR-0003 shape.** Rejected:
  `reconciliation_runner/client.py` exists specifically to forbid a runner-species process from
  setting canonical lifecycle state. Minting is canonical mutation, so a runner is the wrong
  species for this job regardless of how it authenticates.
- **Let the observation ingestion path mint directly on a matching observation.** Rejected before
  reaching implementation: it would make an observation capable of creating work, which is the
  exact invariant WS-6.1 exists to hold, and it would require the mechanism to read observations
  at all, which it deliberately does not.
