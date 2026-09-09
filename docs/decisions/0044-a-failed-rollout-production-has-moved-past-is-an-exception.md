# ADR-0044: A failed rollout production has moved past is an exception, not a finding

- Status: Accepted
- Date: 2026-09-09
- Deciders: Devon
- Supersedes: nothing. Extends the category ADR-0019's landing lane established on 2026-08-13.

## Context

The deploy watcher reports `rollout_did_not_succeed` when a deploying merge's rollout run
concluded anything but `success`. Measured on 2026-09-09 against production, it held 35
deploying-merge change records and produced exactly two findings, both of that kind:

| record | pull request | run | merge commit |
|---|---|---|---|
| 78 | `alobarquest/brain#59` | `33843605720` attempt 1, failure | `aa65710b…` |
| 79 | `alobarquest/brain#60` | `34016016798` attempt 1, failure | `de1bb53b…` |

Both are real. Both are also over. `brain`'s `main` is `34ce166e…`; both merge commits are strict
ancestors of it (`GET /compare` answers `ahead`, `behind_by: 0`), `ci.yml` run `34216728359`
succeeded at that head, and all four brain applications are serving it. There is nothing a reader
of either finding can do.

This estate has now ruled on this shape three times, each time for the landing lane
(2026-08-13 twice, 2026-08-14 once), and the reasoning transfers exactly: **a finding a reader can
do nothing about trains its reader to ignore the report.** The nightly control's whole value is
that a non-zero exit means something; a permanent resident spends that.

The two categories the landing lane already separates are *deliberate refusal* (clears at the next
window) and *exception* (never clears without a person). A superseded rollout is the second: the
run failed, and it will have failed forever.

## Decision

A `rollout_did_not_succeed` finding is reported as an **exception** — printed with a distinct
`[exception]` marker, and deliberately not driving exit 2 — when, and only when, all four hold:

1. There is a **newest** successful `push` run of the rollout workflow on its own
   `trigger_branch`.
2. That run's head compares strictly **`ahead`** from this merge commit.
3. That head's **own workflow revision** is `ATTESTS_REVISION`.
4. The runs at that head reduce through the existing `_unanimous` to one run that concluded
   `success`.

Anything else means not superseded, and the finding stands.

The same excuse is threaded to `SETTLED_ROLLOUT_NOT_SUCCESS` (a closed record whose latest observed
rollout did not succeed), which gets an exception twin under the same condition.

## Why supersession and not age

Age is the obvious discriminator and it is the wrong one. A rollout that failed an hour ago and a
rollout that failed a week ago are the same fact if production is still on the old build — and the
week-old one is *worse*. **Age would quiet a live outage**, which is precisely the failure this
estate hit on 2026-09-06, when `app-brain` served a stale build for a day and a half under a red
run whose message sent a reader to three wrong places.

Supersession asks the question age is a proxy for, and asks it of the world rather than of a clock:
*has production moved past this?*

## Why clause 3 is load-bearing

The excuse must be at least as strong as what would **settle** a record under change-manager's own
rule. `brain`'s workflow history contains four revisions whose green run establishes only that a
webhook answered 2xx — a liveness poll landing inside Coolify's rolling swap, answered by the
container that was already running. Excusing a real failure with one of those would be excusing it
with nothing.

This is the clause most easily left out, because it is the only one that is about the *quality* of
the evidence rather than about its shape. The registry that answers it is the same one
`change_proposer` derives acceptance criteria from, so the two lanes agree by construction.

## Why the fail direction is inward

The finding is **already established** when the excuse is looked for. So `ReadError`, `NotSettled`
and `Unmeasurable` are caught inside the predicate and mean "not superseded".

Letting one propagate would reach `_watch_one`'s `except (Unmeasurable, ReadError)` and convert a
measured failure into `incomplete` and exit 3 — replacing an answer a reader can act on with one
they cannot, on a GitHub hiccup. An absent excuse is exactly a finding that is not excused.

## What is not un-asserted

`_body` is unchanged and the recorded observation still says the rollout failed. An exception is
the same fact with a reason attached, not a suppression — so its detail names the superseding run,
its head and its workflow revision, and a reader who disputes the excuse has everything needed to.

Only the rollout finding is excusable. `rollout_job_named_by_the_registry_is_not_in_the_run` stays a
finding whatever production is serving: it is a live transcription defect, and the supersession says
nothing about it.

## Deliberately not built

**`rollout_never_ran` (`ROLLOUT_ABSENT`) is the same shape one kind over and is out of scope.** A
merge that deployed nothing is also something a later successful rollout moves production past. No
live record carries it, so building it now would be building against no population — which is how
`age_out_human_gates` came to report nothing for a whole workstream. It is named here because
**every fix in this family has generated its next category**, and naming the omission is what stops
it reading as an oversight.

**Option D — a `superseded_by_a_later_rollout` retirement reason in change-manager — is held in
reserve.** Devon's call. The consequence is accepted rather than unnoticed: records 78 and 79 stay
`approved`, and change-manager's queue grows slowly with records nothing will retire. That is a
queue a person can drain, where a permanently-red nightly control is a signal nobody can restore.

## Two things decided while building, which the brief did not settle

**`newest_successful_push_run` drops a run with no start time rather than ordering it against a
default.** GitHub's times are timezone-aware and `datetime.min` is naive, so the obvious
`r.started_at or datetime.min` raises `TypeError` the moment one usable run lacks a start time
beside one that has it. That exception is in neither `superseded_by`'s caught tuple nor
`_watch_one`'s, so the lane would exit **1** — *the tool broke* — about a finding it had already
measured, which is the exact inversion this ADR's fail-direction section exists to prevent. A run
that cannot be ordered cannot be the newest of anything, so dropping it is both correct and the
conservative direction: it can only under-report supersession, and an unfound excuse leaves the
finding standing.

**`backfill` reports its exceptions.** It consumes the same `observe()` and its whole product is
the distribution — so reporting only `findings` would have shrunk that distribution with nothing
saying where the difference went, and historical merges, which is all that command reads, are the
population most likely to have been superseded. It prints `[exception]` lines and carries an
`exceptions` list in the `--json` summary. An exception drives exit 2 there no more than it does
in `watch`.

## Known residuals

Both were found by adversarial review of this branch, both are Low, and both are named here rather
than built so that neither reads as an oversight — the same treatment `ROLLOUT_ABSENT` gets above.

**Clause 1 orders by `run_started_at`, which resets on a RE-RUN — measured, not inferred.**
`brain`'s own `ci.yml` history carries run `32131769609` at attempt 2 with
`created_at: 2026-08-18T11:26:54Z` and `run_started_at: 2026-08-18T13:32:37Z`, two hours apart. So
re-running a long-superseded
success moves it to the front of the ordering; its head is `behind` this merge, clause 2 refuses,
and a genuinely superseding run further along is never consulted. The direction is conservative —
an excuse is withheld, never wrongly granted — but the exception flaps back to a finding until a
newer push-run success lands. The alternative predicate is *any* success whose head is `ahead`,
which answers the "A failed, B succeeded, C failed" case identically and survives the re-run;
`ahead` already means "descended from this merge", so it is not a wider door. It is not built here
because it multiplies the per-failure read cost by the page, and because "newest" is the wording the
ruling used. Changing it is a design fork, not a defect fix.

A cheaper candidate that stays inside the "newest" design: order by `created_at`, which the
measurement above shows does NOT move on a re-run. `_run` currently prefers `run_started_at` and
falls back to `created_at`, and that preference is right for the OTHER reader — `concurrent_rollout_run`
asks whether a run started inside another's window, which is an attempt-scoped question. So the two
readers want different fields from one model, and that is the shape of the fix rather than a
one-line swap.

**Clause 4 accepts a run-level `success` where `_settled` reads the rollout STEP.** This repository
records at length that a run conclusion cannot distinguish *nothing was deployed* from *production
was deployed and is broken*, and `_settled` reads jobs and steps for exactly that reason; the
supersession predicate does not. So a future workflow revision whose rollout job carries an `if:`
that can be false on a push to `main` would produce a green run that never touched production.
**Clause 3 is what makes this latent rather than live:** such a revision would be untranscribed and
so `ATTESTS_UNKNOWN`, and the registry is transcribed-not-corrected, so a human classifying it would
be recording what it actually attests. Closing it costs a fifth GitHub read per failed rollout.

## Consequences

- The deploy watcher's nightly pass returns to exit 0 on today's population.
- Two records remain open in change-manager with no machine path to closing them.
- The kind vocabulary gains a guard: no reported kind may be a substring of another, because the
  report is read by substring — by this estate's own discriminating tests and by an operator's
  grep. It found one pre-existing collision on its first run
  (`rollout_did_not_succeed` ⊂ `a_closed_record_whose_latest_rollout_did_not_succeed`), exempted as
  named debt rather than renamed, since renaming a reported kind changes what an operator greps for.
- Four extra GitHub reads per **failed** rollout. Healthy rollouts ask nothing.
- `brain`'s `ci.yml` revision `7cf6ca2d…` is now transcribed. That was an independent live defect:
  `brain#62` moved the blob on 2026-09-08 and nothing re-transcribed it, so `change_proposer`
  would have refused the next `brain` Dependabot record with *"the rollout workflow revision …
  is not transcribed"*. Increment 2 is inert without it — clause 3 could never be satisfied for
  `brain` while its current revision was unclassified.

## Measurements taken while building

- **The `status=success` run listing served a stale page.** One request answered
  `total_count: 45` with a six-day-old run first; the same query at three other page sizes minutes
  later answered `total_count: 50` with the true newest first. The newest is therefore chosen
  locally by start time rather than taken from GitHub's ordering. It fails safe either way —
  missing a success under-reports supersession, which leaves the finding standing.
- **`concurrency: cancel-in-progress` is already in `c5c088719`**, so `concurrent_rollout_run`'s
  docstring claim that *"neither workflow declares a concurrency group"* is stale for `brain`.
  Pre-existing, out of this change's scope, recorded rather than fixed.

## Evidence

Live differential, both directions, 2026-09-09, `deploy-watcher watch --dry-run` against
production change-manager and GitHub:

- branch: the two brain items print `[exception]`, exit **0**;
- `main`: the same two print `[found] rollout_did_not_succeed`, exit **2**.

The SETTLED twin (`closed_record_production_has_moved_past`) is proven **by controls only, and
deliberately**: `--dry-run` returns before `_ledger_finding` is ever reached, and no closed record
with a failed rollout exists today — records 78 and 79 are `approved`. The condition goes live the
moment a person resolves one, which is precisely when a permanently-red finding would appear.

Mutation pass over the predicate, re-run after the LAST behavioural change: **19/19 killed, zero
survivors**, every anchor matched exactly once and every kill attributed to a named test. Three
survivors in the first run were each acted on rather than explained away — two were real gaps (an
unfinished run in the success listing, and the trigger branch being indistinguishable from a
hardcoded `main` given today's registry) and the third was a dead clause, split into a behavioural
guard and a type-narrowing assert. The nineteenth mutant is the unstarted-run clause the `datetime`
fix above added; it is killed by that fix's own control, and the two `github.py` anchors it moved
were re-pointed rather than left to match nothing.
