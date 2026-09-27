# Pre-work recovery — what heals a partial failure before work runs

Phase-3 exit criterion 4 asks that every pre-work state have a defined recovery. This page covers
two of them: a write chain that half-happened, and a revision somebody turned down. Everything
below was read from the code, and each row names what enforces it.

Nearly every chain here is **act, then record**, and that order is deliberate. A record written
before a call that then fails is a lie. An act whose record is lost can be put right: the act
happened, so it can be observed again.

---

## 1. Bidirectional write chains

A chain is two writes to two systems. The question for each is what the estate looks like when the
first write succeeds and the second does not, and what makes it right again.

| Chain | Order | First write succeeds, second fails | What heals it |
|---|---|---|---|
| `bump_proposer` → authoring repo → change-manager work record | revise + commit + publish the package, then propose the record | The package revision is published with no record | The next pass finds the revision already carries the bump and proposes again. `POST /api/work-changes` replays on identical asserted fields (200, unchanged). **Two known wedges:** a lifecycle failure halfway through `advance` leaves the checkout dirty, and `require_clean` then refuses every later pass (PROJECT.md backlog, P2). And the frozen `_reasoning` sentence is an asserted field, so editing it makes every replay 409 (PROJECT.md backlog, P2). |
| `work_carrier` → orchestrator intake | read the approved record, register the intake naming it | Nothing is stored if registration fails | The next pass carries it again under the same idempotency key. A guard refusal stores nothing, and the carrier reports it on every pass until somebody fixes the cause. |
| `work_watcher` → change-manager retirement | ask the orchestrator whether the record's work is complete, then retire the record | Nothing is stored if the retirement fails | The next pass asks again. A retirement that already happened replays as 200, and a record a human set `wontfix` is left exactly as it is. The watcher runs **before** the carrier, so the carrier never re-registers finished work. |
| `deploy_watcher` → change-manager deploy observation → orchestrator unit observation | record the rollout on the change record, then, if a work unit claims the landing, observe it against the unit | **The unit observation fails:** the next pass replays the change-manager write (its `observation_key` is idempotent) and tries the unit again. **Change-manager refuses (409):** the unit observation is **withheld, not attempted.** | The unit observation is built from change-manager's own verdict and production answer, and a refused record returns neither. Working them out locally would be a second copy of the reduction rule. So a refusal is reported as `change_manager_refused_the_observation`, and if a unit claims the landing, a second finding follows: `a_claimed_work_unit_went_unobserved_because_the_record_refused`, naming the unit. A person decides what to do. The pass does not guess. |
| `estate-pr-merge` / `inert-pr-merge` → GitHub → `estate_pr_merge` row | land the pull request, then write the row and its event in one commit | **The landing succeeds and the commit fails:** GitHub shows the pull request merged, and the table holds nothing | The next attempt finds the pull request closed and no row. Admission reads the landing commit's own trailer (`SDS-Change-Record: <this record>` for the deploying lane, `SDS-Inert-Landing-Policy: <n>` for the inert one; each lane is its trailer's only writer). If it matches, admission answers `landing_act_unrecorded` in place of `landing_pull_request_not_open`. The lander then asks the act route, and the route records `already_merged`, with the reason `landing_act_unrecorded` and the version **the commit carries**. After that the subject reads `landing_already_recorded` and settles. A landing nobody can attribute stays `not open`. A commit that cannot be read answers `landing_pull_request_unreadable`, which is a finding, not a settle. |
| `pr-merge` (factory lane) → GitHub → `unit_pr_merge` row | land, then record | Same crash | This path checks for a landing before it calls GitHub: a pull request it finds already landed is recorded as `already_merged`. Admission is keyed on the unit and never refuses a closed pull request, so the branch is reachable. |
| `change_proposer` → change-manager retirement of a deploy record | observe that the pull request closed unmerged, then call `deploy-retirement` | Nothing is stored | The next pass observes the same fact and retires again. The route's vocabulary has one member (`pull_request_closed_unmerged`), so the server decides the outcome and a retry cannot choose a different one. |

### Two residuals in the landing recovery

They are named here rather than fixed, because both are about when the recovery gets a chance to
run, not about whether it is correct.

- **The deploy watcher can settle the record first.** The landers only ask about `approved`
  records. The deploy watcher resolves a record once production confirms the rollout. If that
  happens before the lander's next pass, nothing ever asks about the pull request again, and the
  lost row stays lost. The landing ledger still records the landing with its full basis, because
  the trailers are in the commit. What is lost is this repository's own row, and with it the
  count the pace rule reads.
- **A late row counts against the wrong window.** `_pace_term` counts rows whose `created_at` falls
  inside the current occurrence of the window. A row recovered during a **later** window counts
  against that window, which bars one legitimate landing for one night in that repository. The
  error is in the conservative direction. Changing what pace counts is a policy question, so it is
  left alone.

---

## 2. A revision somebody turned down

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
