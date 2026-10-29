# Landing layer, Part 3a: a bad bump comes back out (inert repositories) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Dependabot bump the inert lane landed fails a check on the default branch, and the
failure survives one rerun while the commit before it passed, the SDS reverts the bump, holds the
dependency at the version before it, never lands that version again, and sends one alert.

**Architecture:** The orchestrator owns every judgment and every act, as it does for landing. A new
`landing_reverts` table is the state of each watched landing. The inert lander asks, each acting
pass, which recent landings are bad (`GET /api/v1/landing-watch`) and asks the orchestrator to act on
each (`POST /api/v1/landing-revert`). Acting means rerunning the failed jobs once, then opening a
revert pull request built through the git data API as the exact inverse of the bump's squash merge.
The revert pull request lands through the inert lane as its own subject class. In the same
transaction that records the revert landing, `services/membership/revert_hold.py` places the
ceiling hold. Every landing admission refuses a reverted version.

**Tech Stack:** Python 3 (`.python-version`), SQLAlchemy and Alembic, FastAPI, GitHub REST (git data,
Actions), pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, Part 3 and Decisions 3 to 8.
This plan covers the eleven inert repositories. Part 3b covers brain and change-manager (a revert
record in change-manager, the noisy rollout signal, and the image re-point both of them need).

## Findings this plan rests on (recon 2026-10-29)

- The Dispatch App holds `contents`, `pull_requests`, `actions` and `workflows` write
  (`docs/operations/credentials.md:145-150`), so it can build a commit, open a pull request and rerun
  a job. Its permissions are read from a mint response, never `/app`.
- Every member's required-check workflows run on push to `main` with no path filter.
  `landing_ledger/github.py:655::workflow_runs_at` reads whole runs; this plan reads jobs by name.
- Landings are squash merges, so a bump's inverse is the merge commit's parent tree on the paths the
  merge touched.
- In the last 30 days no inert repository had a failed `main` run at a bot landing.
- A single bump's from-version is in its title (`landing_ledger/titles.py::bump_of`); a grouped bump's
  is only in the pull request body table (`| Package | From | To |`), which nothing parses yet.

## Global constraints

- Work in `.worktrees/landing-p3a` with its own `.venv` and database `orchestrator_test_landing_p3a`.
  Never edit, check out or run `uv` in the main tree.
- Never put a secret in a prompt, tool argument or transcript.
- Alembic revision ids are at most 32 characters; the migration is `0044_landing_reverts`.
- Word guards (ADR-0051) apply under `src/orchestrator/`; reword rather than allowlist (`revert`,
  `rerun`, `hold` are free; `deploy`, `dispatch`, `auto_merge` are not).
- A new route joins the inventories in `test_scope_guards.py`, and an ingress POST route gets a row
  in `tests/idempotency/test_matrix.py`.
- Every Dispatch App call site stays bounded by a grant or allowlist check: every act here goes
  through the inert lane's grant term.
- No reported finding kind may be a substring of another.
- The membership writer rule changes in this plan: `test_membership_writers.py`, the CLAUDE.md
  invariant and `docs/operations/membership.md` change together.
- `make check` is green before each commit that touches logic.

## Review focus

1. **A flaky job.** A job that fails once and passes on rerun must never produce a revert or a hold.
   Pinned in Task 2.
2. **A failure that isn't the bump's.** A job that also failed on the merge commit's parent, or a
   job the pull request's own checks never ran, must not produce a revert. Pinned in Task 2.
3. **A revert that removes more than the bump.** If any path the bump touched changed on the
   default branch after it landed, no revert is opened; the landing is marked and alerted instead.
   Pinned in Task 3.
4. **A loop.** A revert is never watched, never reverted, and the reverted version never lands again
   even when no hold could be written. Pinned in Tasks 4 and 5.
5. **A second hold writer that widens.** The automatic writer refuses unless its own revert record
   has landed, writes only a ceiling at the recorded from-version, and never replaces or lifts a
   hold. Pinned in Task 4.

---

### Task 1: The revert record

**Files:**
- Modify: `src/orchestrator/persistence/models.py` (new `LandingRevert`)
- Create: `migrations/versions/0044_landing_reverts.py`
- Test: `tests/persistence/test_landing_reverts.py`

**Interfaces:**
- Produces `LandingRevert`, table `landing_reverts`, one row per watched landing that has gone bad:
  `id` (uuid), `repository` (lower-case), `pr_number`, `merge_commit_sha`, `parent_sha`,
  `failed_job_names` (JSON list), `failed_run_id` (`BigInteger`), `status`, `revert_pr_number`
  (nullable), `revert_head_sha` (nullable), `revert_merge_sha` (nullable), `bumps` (JSON list of
  `{ecosystem, directory, dependency, from_version, to_version}`, `from_version` nullable),
  `hold_outcome` (nullable text), `created_at`, `updated_at`.
- `status` vocabulary, a CHECK built by joining quoted members explicitly (never `f"{VOCAB!r}"`):
  `rerun_requested`, `revert_opened`, `revert_landed`, `refused_overlap`, `revert_failed`.
- Unique `(repository, pr_number)`.

- [ ] **Step 1: Write the failing tests:** `test_one_row_per_landing` (the unique constraint, re-read
  through a second session), `test_the_status_check_refuses_an_unknown_value`,
  `test_run_ids_above_two_to_the_thirty_one_are_stored`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** the model and the migration (`upgrade` and a `downgrade` that drops the
  table).
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "landing: a record for each landing that went bad"`

### Task 2: Judge a landing

**Files:**
- Create: `src/orchestrator/services/landing/revert_watch.py`
- Modify: the estate GitHub gateway in `services/landing/` (read jobs at a commit; rerun a run's
  failed jobs)
- Test: `tests/services/test_revert_watch.py`

**Interfaces:**
- `WATCH_HOURS: Final = 48` (a plain int with a real default).
- `Verdict` (frozen dataclass): `kind` in `{"clean", "pending", "flaky_retry", "bad", "unreadable"}`,
  `failed_job_names: tuple[str, ...]`, `run_id: int | None`, `detail: str`.
- `judge(gateway, landing: EstatePrMerge, record: LandingRevert | None) -> Verdict`, pure over what
  the gateway reads:
  - The watched jobs are the job names that concluded `success` on the pull request's head before it
    landed (read from the head's runs). A job the pull request never ran is never watched.
  - Read those jobs at `merge_commit_sha` on the default branch. Any still running is `pending`.
  - A watched job concluded `failure`: if no record exists, the verdict is `flaky_retry` (the caller
    reruns the run's failed jobs and records `rerun_requested`). If the record is `rerun_requested`
    and the rerun also failed, read the same jobs at `parent_sha`: all passed there means `bad`;
    otherwise `clean` (the failure is not the bump's).
  - Everything passed: `clean`.
- Gateway additions, both bounded by the caller's grant check: `jobs_at(repository, sha) -> list[Job]`
  (name, conclusion, run_id, attempt), and `rerun_failed_jobs(repository, run_id) -> None`.

- [ ] **Step 1: Write the failing tests:** `test_a_clean_merge_commit_is_clean`,
  `test_a_running_job_is_pending`, `test_a_first_failure_asks_for_one_rerun`,
  `test_a_failure_that_passes_on_rerun_is_clean`,
  `test_a_failure_that_survives_the_rerun_with_a_green_parent_is_bad`,
  `test_a_failure_the_parent_shares_is_not_the_bumps`,
  `test_a_job_the_pull_request_never_ran_is_not_watched`,
  `test_an_unreadable_read_is_unreadable_never_clean`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `judge` and the two gateway methods (each catching
  `(httpx.HTTPError, httpx.InvalidURL, ValueError)`).
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "landing: judge a landed bump by its own checks on the default branch"`

### Task 3: Build the exact inverse

**Files:**
- Create: `src/orchestrator/services/landing/revert_build.py`
- Modify: the estate GitHub gateway (git data API: compare, trees, commits, refs; open a pull request)
- Test: `tests/services/test_revert_build.py`

**Interfaces:**
- `REVERT_BRANCH_PREFIX: Final = "sds-revert/"`; the branch is `sds-revert/pr-<pr_number>`.
- `plan_revert(gateway, repository, merge_sha, parent_sha) -> RevertPlan | Overlap`:
  - The touched paths are the merge commit's changed files against its parent.
  - `Overlap(paths)` when any touched path differs between the merge commit and the default
    branch's current head (something landed on top); nothing is built.
  - Otherwise `RevertPlan(base_sha=<current head>, entries=...)`: each touched path set to its blob
    in `parent_sha`, or deleted where the parent lacks it.
- `open_revert(gateway, repository, plan, original) -> (pr_number, head_sha)`: creates the tree on
  `base_tree=<head tree>`, a commit with parent `<head>` and message
  `Revert "<original title>"\n\nReverts #<n>: <failed job names> failed on <merge sha>.`, the branch,
  and the pull request with the same title.
- `is_exact_inverse(gateway, repository, revert_head_sha, merge_sha, parent_sha) -> bool`: the
  revert's changes against its base touch exactly the merge's paths, each equal to the parent's blob.
  Used again at landing time.

- [ ] **Step 1: Write the failing tests:** `test_the_inverse_restores_each_touched_path_from_the_parent`,
  `test_a_file_the_bump_added_is_deleted`, `test_a_path_changed_since_the_landing_is_an_overlap`,
  `test_the_exact_inverse_check_refuses_an_extra_path`,
  `test_the_exact_inverse_check_refuses_a_different_blob`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** with the gateway behind a protocol and a recording double in the tests.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "landing: build a revert as the exact inverse of a squash merge"`

### Task 4: The revert lands, and the hold and never-again follow

**Files:**
- Modify: `src/orchestrator/services/landing/inert_landing_admission.py` (the revert subject class)
- Create: `src/orchestrator/services/membership/revert_hold.py`
- Modify: `src/orchestrator/services/landing/terms.py` (the never-again term), the inert and estate
  admissions (ask it), `src/inert_lander/cli.py` and `src/estate_lander/cli.py` (`Lane.exception`)
- Modify: `tests/architecture/test_membership_writers.py`, `tests/architecture/test_hold_paths.py`,
  `CLAUDE.md` (the "Only `services/membership/proposals.py` writes the member tables" bullet),
  `docs/operations/membership.md`
- Test: `tests/services/test_revert_landing.py`, `tests/services/test_revert_hold.py`

**Interfaces:**
- A **revert subject** is a pull request whose head branch starts with `sds-revert/`, authored by
  the Dispatch App, with a `LandingRevert` row in `revert_opened` naming it. Its terms: the grant
  term, open and on the default branch, checks clean, head current with base (a branch update may
  bring it current), and `is_exact_inverse` on its head. It skips the author, ecosystem, hold and
  never-again terms. Any other pull request from an `sds-revert/` branch is refused
  `landing_revert_not_recorded`.
- When the inert lane records a revert subject `merged`, in that transaction:
  `LandingRevert.status = "revert_landed"`, `revert_merge_sha` set, then
  `revert_hold.place(session, revert: LandingRevert) -> str` runs and its answer is stored in
  `hold_outcome`.
- `revert_hold.place` is the one automatic writer of the member tables. For each bump in
  `revert.bumps`: refuse unless `revert.status == "revert_landed"`; skip (and say so) when
  `from_version` is unknown or the directory is not a `/`-path; skip when a hold already exists on
  `(member, ecosystem, directory, dependency)`; otherwise insert a ceiling at `from_version` with
  reason `Reverted <repo>#<n>: <failed jobs> failed` and set-by `sds-revert`. It takes the same lock
  the person's confirmation path takes. It never updates or deletes a hold.
- `LANDING_VERSION_REVERTED: Final = "landing_version_reverted"`, asked by both lanes for a bump
  subject: refused when a `revert_landed` row's `bumps` holds the same `(ecosystem, directory,
  dependency, to_version)` for that repository. Both landers classify it as an exception.
- `test_membership_writers.py` names `revert_hold.place` and the inert merge path as its only caller;
  `test_hold_paths.py` classifies the revert subject as "not a bump".

- [ ] **Step 1: Write the failing tests:**
  - `test_revert_landing.py`: `test_a_recorded_exact_revert_is_admissible_without_the_bump_terms`,
    `test_an_unrecorded_sds_revert_branch_is_refused`,
    `test_a_revert_whose_head_is_no_longer_the_exact_inverse_is_refused`,
    `test_landing_the_revert_records_it_and_places_the_hold_in_one_transaction`,
    `test_a_reverted_version_is_refused_in_both_lanes`.
  - `test_revert_hold.py`: `test_the_hold_is_a_ceiling_at_the_from_version`,
    `test_an_unknown_from_version_places_no_hold_and_says_so`,
    `test_an_existing_hold_is_left_alone`, `test_a_revert_that_has_not_landed_places_nothing`,
    `test_the_writer_never_updates_or_deletes`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, and update the writer guard, the hold-path classification, the CLAUDE.md
  bullet and `membership.md` in the same commit.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "landing: a recorded revert lands, holds its dependency, and the version never lands again"`

### Task 5: The routes and the lander's pass

**Files:**
- Modify: `src/orchestrator/api/routes/landing.py`, `src/orchestrator/api/schemas/landing.py`
- Modify: `tests/architecture/test_scope_guards.py`, `tests/idempotency/test_matrix.py`
- Modify: `src/inert_lander/cli.py`, `src/inert_lander/orchestrator_client.py`,
  `tests/architecture/test_landing_edges.py`
- Modify: `src/lander/held_alerts.py` or a sibling `src/lander/revert_alerts.py` (builder's call;
  same `KeyedAlerts` sender)
- Test: `tests/api/test_landing_revert_api.py`, `tests/inert_lander/test_inert_lander.py`

**Interfaces:**
- `GET /api/v1/landing-watch` (SYSTEM): for the inert population, every `estate_pr_merge` row with
  `status == "merged"`, a Dependabot subject, and `created_at` within `WATCH_HOURS`, plus every
  `landing_reverts` row not yet resolved, each with its `Verdict` (or revert status) and
  `hold_outcome`. Reads only.
- `POST /api/v1/landing-revert` (SYSTEM, idempotency key), body `{repository, pr_number}`:
  re-judges inside its transaction and acts once: `flaky_retry` → rerun and record
  `rerun_requested`; `bad` → `plan_revert`, then `open_revert` (`revert_opened`) or record
  `refused_overlap`; anything else → no act. Refuses when the repository fails the inert lane's
  grant term.
- The inert lander's acting pass, after its landing pass: read the watch, POST each `flaky_retry` and
  `bad` subject, and send one keyed alert per revert, `landing-reverted:<repo>:<pr>`, with the
  failed jobs, the revert pull request, `hold_outcome`, and how to lift a hold (`sds-members stage`
  a lift, then confirm on `/review`). A `refused_overlap` raises `landing-revert-refused:<repo>:<pr>`.
  Each alert fires once and stays open until the record leaves the watch.
- The revert pull request itself is landed by the ordinary landing pass on a later pass.

- [ ] **Step 1: Write the failing tests:** the API tests for each verdict path and the grant refusal;
  the lander tests `test_an_acting_pass_asks_for_each_bad_landing`,
  `test_a_report_run_asks_for_nothing`, `test_a_revert_raises_one_alert_naming_the_hold`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, add both routes to the inventories, and add the routes to the inert row of
  the edge table.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "landing: the inert lander watches its landings and reverts a bad one"`

### Task 6: Record the decision

**Files:**
- Modify: `docs/decisions/0019-*.md` (an amendment: for inert repositories, a landing the SDS can tie
  a failure to is reverted automatically, per Devon's decisions of 2026-10-29), `docs/operations/landing-lanes.md`
  (the watch, the flake rule, the overlap refusal, the alert, how to lift the hold)

- [ ] **Step 1: Write the amendment and the operations section.**
- [ ] **Step 2: Commit.** `git commit -m "docs: inert landings that break are reverted and held"`

### Task 7: Ship, deploy and prove

- [ ] **Step 1: Review.** Final whole-branch review; fix what holds up.
- [ ] **Step 2: Land** the pull request; confirm `state == MERGED`.
- [ ] **Step 3: Deploy** with `docs/operations/deploy.md` (this deploy has a migration: the printed
  `vps_exec` alembic step runs before the swap). Pull the main tree between passes.
- [ ] **Step 4: Prove it** on the canary described in the decision below, if Devon approves one:
  a real Dependabot bump that passes its pull request checks and fails on `main`, landed by the inert
  lane, then rerun, reverted, landed, held and alerted with no hand step. Without a canary, prove the
  read half live (`GET /api/v1/landing-watch` returns today's inert landings as `clean`) and leave the
  act half proven by tests.
- [ ] **Step 5: Clean up.**
