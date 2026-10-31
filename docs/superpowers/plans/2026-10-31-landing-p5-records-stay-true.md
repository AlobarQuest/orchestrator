# Landing Part 5: records stay true implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One rule picks the update bot's commit, an unknown landing outcome gets settled on every landing path and is watched, and a landed record whose failed rollout production has moved past stops reading `approved`.

**Architecture:** Three independent changes. change-manager gains one narrow retirement route and `deploy_watcher` calls it. The orchestrator's hold term adopts the gate's commit rule, held to the ledger's copy by an agreement test. A `refused` row of unknown outcome stops reading as settled, and the lander's replay of the landing reconciles it.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Postgres, pytest; change-manager (FastAPI, SQLAlchemy).

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, "Part 5: Records stay true".

## What recon found

- **Update-bot commit.** `terms.py::_update_bot_message` takes the one bot-authored commit anywhere, unverified. `landing_ledger/github.py::_gate_message` takes `commits[0]`, bot author, verified. `bump_proposer` already uses the ledger's rule through `update_metadata`, so only the term moves. `PullRequestCommit` already carries `verified`.
- **Terminal `refused`.** Writing no row instead isn't safe. A lost success would then lose `on_landed`, which places a revert's hold, and the next pass would see `landing_pull_request_not_open` and never ask again. The row stays and becomes recoverable. Today's row reads `landing_already_recorded`, which is in `SETTLED`, so the lander never re-asks.
- **Records.** The spec expected a growing set. It isn't one: exactly 9 records stay `approved` (items 78, 79, 89, 94, 104, 115, 117, 118, 125; brain#59 to #89, the latest landed 2026-10-10), and every later record resolves. Each landed with `verdict=failed`, which ADR-0022 won't settle, and later rollouts have passed.

## Decisions

- Devon, 2026-10-31: a machine retires these records once a later green rollout is ahead of the failed one. This narrows ADR-0022 and needs an amendment.
- The new route is separate from `deploy-retirement`: that route belongs to the `propose` scope, and the watcher holds `observe`. Adding it to `observe` would let the watcher assert `pull_request_closed_unmerged`.
- After retirement, a terminal record with a superseded rollout reports nothing. Otherwise both exceptions repeat every pass for ever (`SETTLED_SUPERSEDED_ROLLOUT` and the twin from `outcome.exceptions`), and the record's event already holds the fact.

## Global Constraints

- Read-only until Devon approves this plan. Every edit goes in a worktree with its own venv and its own `orchestrator_test_<ws>` database; change-manager has its own worktree too.
- Check that no rotation unit is ready or claimed before merging change-manager: a merge redeploys it.
- `resolved` is terminal by design and is never made recoverable.
- A retirement can only remove permission: one outcome, `resolved`, chosen by the server. The caller states a fact and never a status.
- change-manager's `STATUS_MOVING_ROUTES` must list the new route, because it moves a status.
- The watcher's write allowlist stays explicit, and every change-manager client sends a named User-Agent.
- Secrets stay out of the transcript. Every parsed `bws` call passes `--color no`. Poll GitHub only with `--watch --interval 30`.

## Out of scope

- The settled-landing change to the watch (Devon, 2026-10-30: leave it for now). This plan doesn't touch `landing-watch`.
- A `refused` row where the remote answered and said it hadn't merged. That outcome is known.
- The two held bumps (#563, #564). They need code changes and stay a person's decision.

## Review Focus

1. A failed rollout whose later green run is not ahead of it, or a green run that isn't the same rollout workflow: the record stays `approved`. The watcher sends only what `superseded_exception` found. Pinned in Task 2.
2. The server sees a current observation of `success`, or none: the route refuses with 409 even if the caller claims supersession. Pinned in Task 1.
3. A failed rollout with no later green run stays a finding, exactly as now. Pinned in Task 2.
4. A reconciliation that finds a revert landed places the hold, once, and a second replay places nothing. Pinned in Task 4.
5. The confirming read fails again: the row stays, the lander reports `unreadable`, and nothing lands. Pinned in Task 4.

---

### Task 1: change-manager retires a superseded failed rollout

**Files:**
- Modify: `app/deploy_retirement.py`, `app/api.py`, `app/schemas.py`, `app/scopes.py`
- Test: `tests/test_api_deploy_supersession.py`, `tests/test_auth_scopes.py`

**Interfaces:**
- Produces: `POST /api/items/{item_id}/deploy-supersession`, `observe` scope, body `DeploySupersessionIn(pull_request_number: int, superseding_run_id: int, superseding_commit: str, actor: str)`, returns the item dict; 409 on refusal; 200 unchanged when already terminal.

- [ ] **Step 1: Write the failing tests** in `tests/test_api_deploy_supersession.py`: `test_a_failed_rollout_superseded_resolves_the_record` (status `resolved`, event `superseded` naming run and commit), `test_a_terminal_record_answers_200_unchanged`, `test_another_pull_request_is_refused`, `test_a_non_deploy_record_is_refused`, `test_a_successful_rollout_is_refused`, `test_no_observation_is_refused`, `test_the_failed_commit_cannot_supersede_itself`. In `tests/test_auth_scopes.py`, assert `observe` reaches the route and not `deploy-retirement`, and the route is in `STATUS_MOVING_ROUTES`.
- [ ] **Step 2:** Run them. Expected: FAIL, route not found.
- [ ] **Step 3: Implement `supersede_deploy_change(db, item, *, pull_request_number, superseding_run_id, superseding_commit, actor) -> bool`** in `app/deploy_retirement.py`, with `SUPERSEDED_EVENT = "superseded"`. It reads `deploy_observations.current_observation`, and refuses unless that observation exists with a verdict other than success, names a merge commit, and that commit differs from `superseding_commit`. Status and event follow `retire_deploy_change`. Extend the module docstring with the argument.
- [ ] **Step 4:** Add the schema, route and scope entries. Tests and `make check` pass.
- [ ] **Step 5:** Commit and open the pull request. It lands before Task 2.

### Task 2: `deploy_watcher` retires what `observe` found superseded

**Files:**
- Modify: `src/deploy_watcher/change_manager.py`, `src/deploy_watcher/cli.py`, `src/deploy_watcher/observe.py`
- Modify: orchestrator `docs/decisions` ADR-0022 and ADR-0044 (amendments), CLAUDE.md if an invariant names the old behaviour
- Test: `tests/deploy_watcher/test_cli.py`, the watcher client's confinement test

**Interfaces:**
- Consumes: Task 1's route; `superseded_exception(outcome)`.
- Produces: `ChangeManagerClient.supersede(item_id, *, pull_request_number, superseding_run_id, superseding_commit) -> dict[str, Any]`; `SUPERSEDING_ACTOR = "deploy-watcher"`.

- [ ] **Step 1:** Read `observe.py::superseded_by`. If the finding doesn't carry the superseding run and commit as data, add them to a small frozen `Supersession` on `Outcome`, set in `_settled`, and have the cli read that. Parsing them out of the finding's text is not acceptable.
- [ ] **Step 2: Write the failing tests:** `test_a_superseded_failed_rollout_retires_the_record`, `test_a_failed_rollout_with_no_later_green_run_retires_nothing` (the finding stays), `test_a_terminal_superseded_record_reports_nothing`, `test_a_refused_retirement_is_a_finding`, and `test_only_the_two_writes_are_allowed`.
- [ ] **Step 3:** Run them. Expected: FAIL.
- [ ] **Step 4: Implement.** Allow `^/api/items/[0-9]{1,9}/deploy-supersession$`. In the pass, a non-terminal record with a supersession gets `supersede`, after its observation is recorded, and only under the pass's existing write mode. Delete `SETTLED_SUPERSEDED_ROLLOUT`'s branch and suppress the superseded exception for a terminal record. Keep `SETTLED_ROLLOUT_NOT_SUCCESS`. Rewrite the `_ledger_finding` docstring, including the paragraph calling the condition latent.
- [ ] **Step 5:** Write the amendments: ADR-0022 (a machine may close a landed record whose failed rollout a later green rollout passed, citing Devon's decision of 2026-10-31) and ADR-0044 (the exception twin is gone). Run `make check`, then commit.

### Task 3: One rule picks the update bot's commit

**Files:**
- Modify: `src/orchestrator/services/landing/terms.py`, `src/landing_ledger/github.py` (docstring only), `docs/operations/landing-lanes.md` ("Dependabot's update block"), CLAUDE.md
- Test: `tests/contract/test_update_bot_commit_agreement.py`, the existing term tests

**Interfaces:**
- Produces: `terms._update_bot_message` keeps its signature, with the gate's rule inside.

- [ ] **Step 1: Write the failing agreement test.** Parametrise over commit lists: one verified bot commit; unverified; a person's commit first, then the bot's; two bot commits; the bot's commit under a merge commit; an empty list; a bot commit with no message. Feed each to `_gate_message` through a fake reader that honours `per_page` and to `_update_bot_message` through a fake gateway. Assert `(gate or None) == term`, and assert the expected message per case. This follows `test_hold_agreement.py`.
- [ ] **Step 2:** Run it. Expected: FAIL on unverified, person-first and two-bot.
- [ ] **Step 3: Implement.** Take `commits[0]`; return its message only when `author_login == UPDATE_BOT_LOGIN` and `verified`. Update the existing tests that pinned "exactly one bot commit", and both docstrings (`version_ceiling_term`, `_gate_message`).
- [ ] **Step 4:** Census, read-only: for every open update-bot pull request in the member record's granted repositories, record whether the two rules agree. Put the counts in the measurements file. A disagreement stops the task and goes to Devon.
- [ ] **Step 5:** Run a mutation pass over the two conditions and `make check`, then commit.

### Task 4: An unknown landing outcome gets settled

**Files:**
- Modify: `src/orchestrator/services/landing/interfaces.py`, `lane_act.py`, `estate_landing_admission.py`, `inert_landing_admission.py`, `src/lander/core.py`, `docs/operations/landing-lanes.md`, CLAUDE.md
- Test: the lane act tests, `tests/lander/test_lanes.py`

**Interfaces:**
- Produces: `LANDING_OUTCOME_UNKNOWN: Final = "landing_outcome_unknown"`; `outcome_is_unknown(status: str, reason_code: str | None) -> bool`, true for status `refused` with a reason starting `f"{MERGE_REFUSED_BY_REMOTE}:"`.

- [ ] **Step 1: Write the failing tests:** `test_an_unknown_row_answers_outcome_unknown_not_recorded` (both admissions), `test_a_replay_that_finds_it_landed_records_already_merged_and_runs_on_landed`, `test_a_second_replay_runs_on_landed_nothing`, `test_a_replay_that_finds_it_unlanded_removes_the_row_and_acts_normally`, `test_a_replay_whose_read_fails_leaves_the_row`, `test_a_confirmed_refusal_row_stays_terminal`, and in the lander `test_outcome_unknown_replays_the_landing`, `test_outcome_unknown_with_no_submit_acts_on_nothing`, `test_a_still_unknown_replay_reports_unreadable`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement admission.** Where each admission appends `LANDING_ALREADY_RECORDED`, an unknown row appends `LANDING_OUTCOME_UNKNOWN` instead. Register the code wherever the vocabulary guards require it.
- [ ] **Step 4: Implement `_resettle` in `lane_act.py`,** called from `_land`'s `existing` branch when `outcome_is_unknown` holds. Build the re-read from the row's repository and number. `None`: return the row. Landed: set `already_merged`, the merge commit and `reason_code=None`, write an event, run `on_landed`, return the row. Confirmed not landed: delete the row, write an event, flush, and fall through to the ordinary path. The row's permission columns stay as written on the landed branch. Correct the comment claiming the ledger settles it, and the model docstring's "one row, ever" to allow this one removal.
- [ ] **Step 5: Implement `consider`.** Before the `SETTLED` test, an answer with `landing_outcome_unknown` and `submit` calls `client.land` with the answer's head and `landing_key`. Status `already_merged` gives `settled`; `merged` gives `landed`; a row still `refused` gives `unreadable`. A dry run reports `unreadable` with the detail. Report the head being absent as `unreadable`.
- [ ] **Step 6:** Run a mutation pass over `_resettle`'s three branches and `outcome_is_unknown`, and `make check`, then commit.

### Task 5: The factory merge settles too, and a reconciled landing is watched

Beyond the spec's wording, on Devon's question of 2026-10-31. Part 4 made `pr-merge` unattended, and it has the same terminal row. The watch selects only `status == "merged"` with `landing_inert` on its event, so a policy landing whose response was lost (`already_merged`) is unwatched today, and a bad bump would stay in production.

**Files:**
- Modify: `src/orchestrator/services/landing/pr_merge.py`, `revert_act.py`, `src/bump_driver/cli.py`, `docs/operations/landing-lanes.md`, ADR-0025, CLAUDE.md
- Test: the pr-merge tests, the watch tests, `tests/bump_driver`

**Interfaces:**
- Consumes: Task 4's `outcome_is_unknown`.

- [ ] **Step 1: Write the failing tests:** `test_a_replay_that_finds_it_landed_records_already_merged`, `test_a_replay_that_finds_it_unlanded_removes_the_row_and_acts_normally`, `test_a_replay_whose_read_fails_leaves_the_row`, `test_a_lost_response_policy_landing_is_watched`, `test_a_settled_unknown_policy_landing_is_watched`, `test_an_already_merged_found_before_the_call_is_never_watched` (somebody else's act), and in the driver `test_an_unknown_outcome_row_is_waiting_not_refused`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** `_act`'s `existing` branch resettles as Task 4 does, using `_landed_after_all`. A landing reconciled after our own call, by either route, records the estate's `landing_inert` on its event and the `authority.human_gate_not_required` event the policy path records, resolving the deferred Task 3 minor. The `already_merged` written before the call records neither. The watch's selection accepts `merged` and `already_merged` and still requires `landing_inert`, so that stamp alone separates our act from a person's.
- [ ] **Step 4:** In `bump_driver`, an unknown row reports as waiting on its first pass and raises `bump-act-refused` only when a replay leaves it unknown. Its hourly replay is the re-ask, so no new caller is needed.
- [ ] **Step 5:** Run a mutation pass over the watch's selection and the resettle branches, and `make check`, then commit.

### Task 6: Ship and prove

- [ ] **Step 1:** Run the whole-branch review for each pull request.
- [ ] **Step 2:** Confirm no rotation unit is ready or claimed. Merge change-manager and confirm its revision is served.
- [ ] **Step 3:** Merge the orchestrator pull request and deploy with the deploy command. No migration is expected; confirm `alembic current` equals `heads`. Order matters: both landers read `/landing-unknown-outcomes` every pass, and `deploy_watcher` calls `deploy-supersession`, so change-manager must serve its route first, and the image must serve its route soon after the merge. The lanes activate main on their next pass, so merge straight after a lander pass, deploy at once, and confirm `openapi.json` serves the route. A pass in the gap reports `unreadable` and alerts only after 24 hours, so a short gap costs one report line.
- [ ] **Step 4: Live proof.** The next `deploy-watcher` pass retires all 9 records, each with a `superseded` event naming a run. The next estate pass inside the landing hours considers only live records, with 0 `settled`. A second watcher pass reports none of the 9. Record times and actors in the measurements file.
- [ ] **Step 5:** `_resettle` and the factory's resettle have no live proof: the state was never observed and can't be produced without a fault in GitHub. The tests and mutation pass are its evidence; say so in the final report.
- [ ] **Step 6:** Remove the worktrees, databases and branches, update memory, and list every ledger ruling with its cost in the final message.
