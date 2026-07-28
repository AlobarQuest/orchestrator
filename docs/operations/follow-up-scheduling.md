# Follow-Up Scheduling Operations

WS-P2.8 turns a package-declared `follow_up` block into a timed, human-discharged work unit. The
package (a human-approved artifact) supplies the *intent* — whether an outcome should be
revisited; the orchestrator supplies only the *timing* — when; a human supplies the *discharge* —
the actual judgment. The orchestrator gains no scheduler: there is no thread, no loop, and no timer
anywhere in `src/`. See `docs/decisions/0007-declared-follow-up-scheduling.md` for the bounds this
capability operates inside.

## What it does

Every work package revision may carry a `follow_up` declaration. When a revision's work has
settled and the declared window has elapsed, an operator-invoked pass mints exactly one
`AWAITING_REVIEW` work unit carrying a single generated, human-adjudicable acceptance criterion.
The pass is a pure database read plus an append-only write — no outbound call, nothing scheduled,
invoked on demand by `POST /api/v1/follow-ups/mint` (SYSTEM only).

## The `follow_up` block

Four fields, mirrored field-for-field from the intent-packages schema:

| Field | Type | Machine-read? |
|---|---|---|
| `required` | `bool` | **Yes** — the sole gate the due predicate consults |
| `revisit_when` | `str \| null` | No — prose, carried verbatim into the minted unit's `outcome` and into the generated criterion's evidence for a human to read |
| `signals` | `list[str]` | No — prose, carried verbatim into `outcome` |
| `owner` | `str \| null` | No — carried verbatim into the generated criterion's `approver`, falling back to the revision's `approved_by` when null |

Only `required` is machine-read. The other three are never parsed, matched, or interpreted by the
orchestrator — they are the author's judgment, handed to whichever human eventually discharges the
review. A revision with no `follow_up` at all (`NULL`) is treated identically to one with
`required: false` for the purposes of minting.

Unknown keys, a missing mandatory key, or a wrong type make the whole block invalid at intake — a
named `DomainError`, never a 500. A payload that omits `follow_up` entirely is accepted and stored
`NULL` (this is how older emitters remain compatible).

## The due predicate

A revision's follow-up review becomes due when, in order:

1. **Already minted** — any work unit of the revision already carries the `follow_up_review`
   capability. There is no separate flag for this; it is derived from the units themselves, so
   there is exactly one mechanism deciding it, not two that could disagree. If due, evaluation
   stops here (`already_minted`).
2. **Declaration requires a follow-up** — `follow_up` is non-null and `required is True`.
   Otherwise `not_required` (not surfaced as a skip — see "Reading the output" below).
3. **No `FAILED` unit** — if any work unit of the revision is in `FAILED`, minting blocks with
   `unsettled_failed_unit`.
4. **Every remaining unit is settled** — every unit must be `COMPLETED` or `CANCELLED`. Otherwise
   `units_in_flight`.
5. **At least one `COMPLETED` unit** — a revision whose every unit was cancelled shipped nothing,
   and there is no outcome to revisit. Otherwise `no_completed_unit`.
6. **The window has elapsed** — `now >= anchor + follow_up_due_after_days`, where `anchor` is the
   latest time any unit of the revision *entered* a settled state, read from the event ledger (not
   from `work_units.updated_at`, which a trigger rewrites on every update and so cannot record when
   a state was entered). Otherwise `not_yet_due`.

### Why `FAILED` blocks, and what that costs

`FAILED` is not a terminal state — `(FAILED, READY)` and `(FAILED, CANCELLED)` are both legal
edges — so a `FAILED` unit is a resting state whose disposition (retry, or retire) has not yet
been taken. The package's outcome is not yet knowable while one sits there, so there is nothing to
schedule a revisit of.

The cost of that correctness is real and currently unbounded: **nothing in this system surfaces a
`FAILED` unit as needing disposition.** `dead_letter` reports `FAILED` units, but reporting is not
resolution, and a revision parked behind a lingering `FAILED` unit will never mint — not late,
never — until an operator resolves it through the ordinary `FAILED → READY` (retry) or
`FAILED → CANCELLED` (retire) transition. This gap is tracked as backlog item `6bcd7ee8b6b2`.

## `follow_up_due_after_days`

```
ORCHESTRATOR_FOLLOW_UP_DUE_AFTER_DAYS   default 30, bounded 0..365
```

A plain `int`, deliberately with no off value — following the same discipline as
`dead_letter_stalled_approval_seconds`. An unbounded value would silence the mechanism as
effectively as an `Optional` default ever did, so the upper bound (365 days) is the point. `0`
means "due the instant the work settles" — maximally on, not a special case.

## Running a pass

```bash
scripts/run-follow-up-mint.sh [--json]
```

The launcher fetches the `orchestrator-system` SYSTEM bearer from BWS at runtime (never stored in
the repo), then execs `orchestrator mint-follow-ups`. One pass, then exit.

**The credential must be `orchestrator-system`, never `orchestrator-drift-reporter`.** The
drift-reporter identity's registry profile is *observe and propose, never mutate*; minting a work
unit is canonical mutation, and event attribution (`ActorContext.actor_id`) is permanent. Both
credentials already exist in production with role `system`; no new credential, env write, or
restart is needed to run this pass.

**`drift-audit.sh` runs one pass daily** as a small additive step alongside its existing 03:00 run,
non-fatal and fail-open (a counted WARN on failure, never touching the drift loop's exit code or
its other steps). This is the one part of WS-P2.8 that *is* wired to a schedule on day one — see
ADR-0007's "Scheduled trigger" section for why that is not a contradiction of the "no loop inside
the orchestrator" posture.

## Reading the counted output

```json
{ "minted": [ {"work_unit_id": "…", "work_package_revision_id": "…", "due_at": "…"} ],
  "skipped": [ {"work_package_revision_id": "…", "reason": "not_yet_due"} ],
  "considered": 12 }
```

`considered` is every revision the pass looked at. `minted` lists what was created this pass.
`skipped` carries a reason per revision that was considered but did not mint — except revisions
whose declaration doesn't require a follow-up at all, which are omitted entirely rather than
flooding the response (there is no `not_required` entry in practice, even though it exists as an
internal reason code).

| Reason | Meaning | Operator action |
|---|---|---|
| `already_minted` | A review unit already exists for this revision | None — this is steady state |
| `unsettled_failed_unit` | A `FAILED` unit is blocking; the package's outcome is undecided | Resolve the `FAILED` unit (`READY` to retry or `CANCELLED` to retire), then the next pass reconsiders |
| `units_in_flight` | Work is still moving | None — wait |
| `no_completed_unit` | Every unit is settled but none completed (fully cancelled revision) | None — there is nothing to revisit |
| `not_yet_due` | The window hasn't elapsed | None — wait |
| `declaration_malformed` | The stored `follow_up` failed validation (should not happen post-intake; a defensive skip) | Investigate the revision's stored declaration |

A malformed or already-minted revision cannot stop the pass from minting the rest: each revision
runs inside its own SQL savepoint (`session.begin_nested()`), so a failure on one revision rolls
back only that revision's attempt and is counted as a skip — units already minted earlier in the
same pass, and units still to come, are unaffected. The pass issues exactly one
`session.commit()`, at the very end.

**`idempotency_key` on the mint route is accepted and never read.** The command envelope requires
one (the same shape every command uses), but idempotency for minting is entirely structural: the
minted unit's id is `uuid5(NAMESPACE_URL, f"sds:follow-up:{revision_id}")`, backed by the unique
`(work_package_revision_id, unit_key)` constraint. Re-running the pass under a brand-new
idempotency key still mints nothing new for a revision that already has a review unit — it reports
`already_minted`. Do not expect the key to drive dedup; it doesn't.

## Discharging a review unit

1. Open `/review/units/{id}` for the minted unit (state `AWAITING_REVIEW`).
2. The page shows one adjudicatable criterion, `ac_id: follow-up-review`, whose evidence is the
   package's `revisit_when` prose (or a default sentence if none was declared) and whose approver
   is the declared `owner` (or the revision's `approved_by` if none was declared). Adjudicate it —
   Passed / Not applicable — with a rationale.
3. Press **Complete** on the unit review form. This is the ordinary `AWAITING_REVIEW → COMPLETED`
   human-gated transition; nothing about it is special-cased for follow-ups.

## If nobody discharges it

An undischarged review unit is reported by `dead_letter` as `stalled_approval` once it has sat
past `dead_letter_stalled_approval_seconds` (default 7 days, capped at 30) with no answer.
Reporting, not resolution — silence is never treated as approval, and nothing auto-completes the
unit.

## Retiring a review that's become moot

If the review question is no longer worth answering, retire the unit through the existing public
lifecycle surfaces — no new edge was added for this:

```
AWAITING_REVIEW →(HUMAN)  REVISION_REQUIRED
                →(SYSTEM)  READY
                →(SYSTEM)  FAILED
                →(HUMAN)   CANCELLED
```

Each arrow is an ordinary, already-existing transition reachable through `/review` or the CLI —
nothing here is a WS-P2.8-specific lifecycle edge.

## Forward-only — there is no backfill

Revisions intaken before migration `0020_wsp28_follow_up` have `follow_up = NULL`, and that is
permanent: the package YAML itself is never stored, only the derived intake payload, and
`follow_up` was not part of that payload before this migration. There is no way to recover the
original declaration for a pre-existing revision, so no revision intaken before the migration can
ever mint a follow-up review — not retroactively, not by any operator action.
