# Pre-work recovery

Phase-3 exit criterion 4 asks that every pre-work state have a defined recovery. This page covers
all four: an observation whose fact changed or was wrong (A), a proposal nobody reviewed (B), a
revision somebody turned down (C), and a write chain that half-happened (D). Everything below was
read from the code, and each row names what enforces it.

---

## A. Changed or withdrawn observations

**Rule (Devon, 2026-09-27, ruling A): an observation is never invalidated, rewritten or deleted.**
There is no route that does it, and none will be built. The full record is ADR-0026 amendment 2.

What to do depends on what went wrong and how far the work has got:

| situation | what to do | who |
|---|---|---|
| The fact changed (it failed yesterday and passes today) | Nothing by hand. The producer posts a new observation under a run-keyed `source_reference` (ADR-0022's `observation_key` shape), and every consumer reads the newest row. If a producer cannot do that, it has a design defect: fix the producer, not the rows. | producer |
| The signal was wrong, and its change record is still **pending** | Mark the change record **`wontfix`** in change-manager. Leave the observation alone; it is a true record of what the producer said. | a person |
| The signal was wrong, and the record is **approved** with a unit in flight | Stop the **unit** through its own lifecycle. A person can cancel a claimed or executing unit directly. A submitted one is failed by the verifier (`commands/fail`) first and then cancelled. The observation and the record stay as they are. | a person (and the verifier, for a submitted unit) |
| The work has already landed | Nothing. The chain records what happened. | — |
| A producer draws `observation_conflict` | Read the recovery text on the error. Two cases: **(1)** the producer keys a re-runnable subject by a non-run-keyed reference. That is a defect, and the fix is to change the key. **(2)** The producer keys an **immutable** subject, like the landing ledger's commits. Then the refusal is deliberate, because a changed fact about an immutable thing means something upstream is wrong. Find out what, and if a corrected reader re-derives rows that are already stored, exempt those rows from re-writing (`KNOWN_DEFECTIVE_METADATA_LANDINGS` is the worked example). Do not try to correct them. | the producer's owner |

**Why rewriting is never the answer.** An observation is a record of what a producer said, when it
said it. A later, different fact is a new record, not a correction of the old one. Consumers already
pick the newest row, so appending is all a change of fact ever needs.

## B. Generated but unreviewed proposals

These are things a machine generated that a person has not yet decided on. **None of them is
retired by a machine** (Devon, 2026-09-27, ruling B1). A person decides, with controls that already
exist.

### A stale `work` change record (change-manager)

A `work` record is **stale** when it is pending or approved and either:

- a newer revision's record exists for the same package (the carry can never register the old
  revision: it refuses on `revision_mismatch`), or
- the Dependabot pull request its reasoning names was **closed or merged by hand**, and the work it
  asked for was never built.

**How you find out.** The work watcher reports each one as a `[FINDING] … STALE: …` line in the
work-carrier pass (`scripts/run-work-carrier.sh`, 07:05 daily). That pass then exits **3**, the
lane's finding code. The dead-man check stays up; the log line is the signal. A record whose work
the factory *did* build is retired automatically before it is looked at, so a factory landing never
shows up as stale.

**The same superseded record may also appear in the bump-proposer pass** as `superseded`, but only on a morning when an open pull request still maps to its package. That is one record reported by two lanes, not two problems.

**What to do.** Retire the record in change-manager: `wontfix` if the change should not happen,
`resolve` if it already happened some other way. `resolved` is terminal by design; if the
underlying condition comes back, its producer raises it again. No machine credential can make this
move. The `work` pipeline's status is human-only (ADR-0028), and ruling B1 deliberately added no
retirement route for it.

**What it does not cover.** A record whose reasoning names no pull request (a hand-proposed one) is
printed as `staleness not assessed` and is not a finding. A pass with no GitHub credential
(`WORK_CARRIER_GITHUB_TOKEN`, from `gh auth token`) prints the same note and exits 2.

### A decomposition proposal that should go no further (`/review`)

Open the proposal (`/review/decomposition-proposals/{id}`) and use its **reject** control, with a
reason. Rejecting creates no work units.

### A registered package revision that should go no further (`/review`)

**No control retires a revision** (ruling B2, 2026-09-27; fixed in the queue text, not with a new
verb). The queue item used to say *"or decide it goes no further"*. Nothing does that. The item now
says what is possible:

- While a breakdown proposal for it is pending, the proposal is the queue item. Reject it as above.
- Once no proposal is pending and no breakdown is approved, the revision **comes back onto the
  queue** and stays there. Nothing records "declined" for a revision.

**Known permanent resident: `wsp211-conformance-kit` revision 1.** It is the only live subject of
the factory-policy grandfathering table. Breaking it down and settling it while that table ships
would make the policy artifact stop loading (see the grandfathering bullets in `CLAUDE.md`). It
stays on the queue until the table is removed, in the same change that settles it.

---

## C. Rejected intake revisions

Every rejection point already has a way back. None of them needed new machinery. The tests named
below pin that the way back works.

| Where | Decision | Recovery | Enforced / pinned by |
|---|---|---|---|
| intent-packages lifecycle | `ready_for_review → rejected` | `rejected → draft`, and `revise` is legal from `rejected` | `intent_packages/lifecycle.py` |
| change-manager work record | a human sets `wontfix` | `reactivate`: `wontfix → pending`, and only from `wontfix` | `app/transitions.py::reactivate` |
| change-manager work record, re-proposed | the producer proposes the same bump again | replays onto the declined record: 200, and the status stays `wontfix`. A differing assertion gets a 409. A **new** upstream version is a new package revision, and so a new pending record. That is how the world raises a declined condition again. | change-manager `test_a_proposal_replayed_onto_a_record_a_human_declined_leaves_the_decline_standing`; orchestrator `tests/bump_proposer/test_pass.py::test_a_replay_onto_a_record_a_human_declined_is_not_a_finding` |
| orchestrator breakdown | reject, or require revision, in `/review` | submit a corrected proposal. Only an **approved** breakdown bars another (`decomposition_already_approved`). The proposal that was turned down stays as the human left it. | `tests/services/test_decomposition.py::test_a_breakdown_resubmitted_after_a_human_turned_one_down_is_accepted_and_approvable` |
| orchestrator intake | a guard refuses registration | nothing is stored. Fix the cause and the next carry registers it. | `work_carrier` reports the refusal on every pass |

One thing is recorded but not changed: change-manager's `decide()` does not check which status a
record is moving from. Any such check lives in whichever transition function calls it
(`reactivate` has one). Whether `decide()` should check as well is not decided here.

---

## D. Partial failure in two-way write-back

Nearly every chain here is **act, then record**, and that order is deliberate. A record written
before a call that then fails is a lie. An act whose record is lost can be put right, because the
act happened and can be observed again.

A chain is two writes to two systems. The question for each is what the estate looks like when the
first write succeeds and the second does not, and what makes it right again.

| Chain | Order | First write succeeds, second fails | What heals it |
|---|---|---|---|
| `bump_proposer` → authoring repo → change-manager work record | revise + commit + publish the package, then propose the record | The package revision is published with no record | The next pass finds the revision already carries the bump and proposes again. `POST /api/work-changes` replays on identical asserted fields (200, unchanged). **Two known wedges:** a lifecycle failure halfway through `advance` leaves the checkout dirty, and `require_clean` then refuses every later pass (PROJECT.md backlog, P2). And the frozen `_reasoning` sentence is an asserted field, so editing it makes every replay 409 (PROJECT.md backlog, P2). |
| `work_carrier` → orchestrator intake | read the approved record, register the intake naming it | Nothing is stored if registration fails | The next pass carries the record again under the same key, `work-carry-<record>-<revision>` (`work_carrier/prepare.py`), and an identical payload replays. A guard refusal stores nothing, and the carrier reports it on every pass until somebody fixes the cause. |
| `work_watcher` → change-manager retirement | ask the orchestrator whether the record's work is complete, then retire the record | Nothing is stored if the retirement fails | The next pass asks again. A retirement that already happened replays as 200, and a record a human set `wontfix` is left exactly as it is. The watcher runs **before** the carrier, so the carrier never re-registers finished work. |
| `deploy_watcher` → change-manager deploy observation → orchestrator unit observation | record the rollout on the change record, then, if a work unit claims the landing, observe it against the unit | **The unit observation fails:** the next pass replays the change-manager write (its `observation_key` is idempotent) and tries the unit again. **Change-manager refuses (409):** the unit observation is **withheld, not attempted.** A 409 says something about the record itself, so the next pass hits it again. That is why the pass reports it as a finding rather than as incomplete. | The unit observation is built from change-manager's own verdict and production answer, and a refused record returns neither. Working them out locally would be a second copy of the reduction rule. So a refusal is reported as `change_manager_refused_the_observation`, and if a unit claims the landing, a second finding follows: `a_claimed_work_unit_went_unobserved_because_the_record_refused`, naming the unit. A person decides what to do. The pass does not guess. |
| `estate-pr-merge` / `inert-pr-merge` → GitHub → `estate_pr_merge` row | land the pull request, then write the row and its event in one commit | **The landing succeeds and the commit fails:** GitHub shows the pull request merged, and the table holds nothing | **The orchestrator does not heal it.** A retry finds the pull request closed and no row. Admission refuses it as `landing_pull_request_not_open`, both landers classify that as settled, and nothing is written. **The fact is not lost:** the landing ledger records every landing from GitHub independently. A deploying-lane landing carries `SDS-Change-Record` and records basis `change-record-policy`; an inert-lane landing carries `SDS-Inert-Landing-Policy` and records basis `inert-landing-policy`. **Nothing reports that the row is missing** (see below). |
| `pr-merge` (factory lane) → GitHub → `unit_pr_merge` row | land, then record | Same crash | This path checks for a landing before it calls GitHub: a pull request it finds already landed is recorded as `already_merged`. Admission is keyed on the unit and never refuses a closed pull request, so the branch is reachable. |
| `change_proposer` → change-manager retirement of a deploy record | observe that the pull request closed unmerged, then call `deploy-retirement` | Nothing is stored | The next pass observes the same fact and retires again. The route's vocabulary has one member (`pull_request_closed_unmerged`), so the server decides the outcome and a retry cannot choose a different one. |

### The residual of a lost landing row

Pinned by
`tests/landing_ledger/test_audit.py::test_a_lane_landing_with_no_orchestrator_row_is_recorded_and_reported_by_nothing`.

- **The ledger holds the fact.** The basis it records names the change record or the policy
  version, because the trailers are in the landing commit.
- **No audit arm reports the missing row.** `audit_landing` reads only the rule basis.
  `audit_inert_landing` re-reads only the stored row's version and checks, and never asks the
  orchestrator. Nothing audits the `change-record-policy` basis at all. Only the factory basis is
  checked against the orchestrator's own record.
- **The pace rule can admit one extra landing in that window.** `_pace_term` counts
  `estate_pr_merge` rows, so a landing whose row was lost does not count, and a second pull request
  in the same repository can land in the same window. The inert lane has no pace rule, so there the
  cost is only the missing row.

No detector for this exists. Building one is a decision for HQ, and nobody has asked for one.
