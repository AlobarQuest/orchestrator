# Bridge layer: automatic dependency bumps for repositories that deploy — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed Dependabot bump in a repository that redeploys on landing and is granted
`dependency_updates` becomes a registered intake with a policy-approved record and a known-good
envelope, with no human click. This is the path inert repositories got in Part 3.

**Architecture:** Four holdouts move, and nothing else is built:

- The bump proposer admits repositories the landing policy declares deploying, and creates their
  standing packages with reach `[live_estate]`.
- intent-packages' `create-standing` takes a reach, and its dependency-update grant (schema 3)
  accepts either single-member reach.
- The orchestrator's `factory-policy.toml` gains the uv, npm and pip dependency-update patterns under
  `live_estate`.
- change-manager's work policy approves dependency-update records for deploying repositories as well
  as inert ones.

The factory's own pull request in those repositories already gets a `factory-delivery` deploy record
from `change_proposer`. As today, it merges only through `pr-merge`, with a person's authority
approval bound to the exact fingerprint and the `live_estate` change window.

**Tech Stack:** Python 3 in three repositories (orchestrator, intent-packages, change-manager); pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`, Part 3, "Coverage that widens:
Repositories that deploy". Its ordering condition (holds first) shipped in #540.

## Measured scope (2026-10-28)

The lane is "landing policy deploying set ∩ `dependency_updates` grant", not two named repositories.
Measured live:

| Source | Deploying | Inert |
|---|---|---|
| change-manager landing policy (`repositories` / `inert_landing.repositories`) | brain, change-manager | 11 repositories |
| App Brain `default-branch-landing` | brain, change-manager (and 7 non-members) | the same 11 |
| Member record `dependency_updates` | all 13 members | |

So the widened lane is exactly `alobarquest/brain` and `alobarquest/change-manager`. The deploying
`landing` block excludes `github_actions` only.

`rollouts` is granted to 10 members, 8 of them inert (orchestrator, intent-packages, rtk and others).
It means "the factory watches this repository's rollouts", **not** "landing deploys", so nothing in
this plan keys on it.

## Global constraints

- Worktrees only:
  - `~/Projects/orchestrator/.worktrees/bridge-widen` (exists; branch `bridge-widen-deploying`). It
    needs its own `.venv` and its own `orchestrator_test_bridge_widen` database.
  - `~/Projects/intent-packages/.worktrees/bridge-widen`.
  - `~/Projects/change-manager/.worktrees/bridge-widen`, created from a fresh `origin/main` (the
    local clone is behind).

  Never edit the main checkouts: the scheduled lanes run the orchestrator's, and the bump proposer
  commits from intent-packages'.
- Never put a secret in a prompt, tool argument or transcript. The measurement script reads tokens
  in-process (`scripts/sds-bws.sh` from the main tree, `_SDS_BWS_PYTHON` set).
- A known-good pattern, the grant and policy approval of `work` records are standing authority, so
  each change is a release. The dependency-update budget stays equal in its five places
  (`scripts/check_profile_budget_agreement.py` scans every reach row).
- Each repository's gate must be green before a commit that touches logic: orchestrator `make
  check`, intent-packages `make check`, change-manager its `make check`.

## Rulings made while planning

1. **Reach for a deploying target is `[live_estate]`,** as the spec states ("`live_estate`, not
   `source_repository`"). Only `live_estate` patterns are then needed, because `authority_refusals`
   asks a pattern in every declared member. Dispatch's `estate_refusal` passes, since it refuses only
   a redeploying target whose reach omits `live_estate`. Cost if wrong: the reach no longer names the
   source repository; members only narrow, and the target is still in the envelope.
2. **Which repositories deploy has one producer-side source and one enforcing source.** The bump
   proposer chooses the reach from change-manager's landing policy, and change-manager's work policy
   reads the same document. App Brain, read by dispatch (`estate_refusal`) and merge
   (`_landing_terms`), is the truth that enforces it. If the two disagree, the outcomes are:
   - Inert in App Brain but `live_estate` in the package: more restraint (change window, longer
     lease).
   - Redeploying in App Brain but `source_repository` in the package: dispatch refuses
     `reach_contradicts_estate`, visible on the unit.

   Neither direction lands anything. They agree today (measured). No `membership_check` leg reads App
   Brain, because no lane holds an App Brain credential; dispatch is the net.
3. **intent-packages' grant accepts a set of exact reaches (schema 3),** not one. The new field is
   `reach_options`, an array of arrays (`[["source_repository"], ["live_estate"]]`).
   `_reach_refusal` passes when the package's `reach` equals one option exactly, so `[]`, a superset
   and a mixed set are still refused. Intent-packages doesn't decide which option fits a target:
   App Brain at dispatch does (Ruling 2). Schema 3 drops `reach`, the loader still reads 1 and 2,
   and the approver string becomes `approval-policy@v3` (nothing in the orchestrator names `@v2`).
4. **change-manager's work policy accepts inert ∪ deploying,** and the objection becomes
   `target_repository_not_in_policy`. A test holds the two populations disjoint for every policy
   version the module serves (the open P2 backlog item), so a repository can't be both.
5. **The proposer's "does the cascade still have it" question uses each repository's own block:**
   the inert block's `excluded_ecosystems` for inert repositories, and the `landing` block's for
   deploying ones (`github_actions` only). A passing bump in a deploying repository stays with the
   estate lander, as today; only a failed one, or one in an excluded ecosystem, is this lane's.
6. **ADR-0025's risk is amended, not ignored.** A machine-authored dependency fix to a deploying
   repository carries no human-judgment criterion. Two human gates stand between it and production:
   - the decomposition approval;
   - `pr_merge_admission`'s authority term (`exact_authority_approval(unit)`, bound to the exact
     fingerprint).

   The merge term is unconditional: its docstring says the known-good substitute admission allows
   "has no counterpart here" (verified in `pr_merge_admission.py:27-34, 297`). Task 3's patterns
   therefore remove only the dispatch-time approval, as they did for inert repositories. The
   `live_estate` change window and the approved change record also gate the merge. Graduating
   either human gate stays with the Admission and Landing passes.
7. **The change window stays `00:00–23:59`,** the build-phase widening. When it narrows, dispatch
   and merge for these two repositories become night-only. This is recorded in the ADR, not changed.

## Review focus

1. **A package whose reach is `[]`, `["source_repository","live_estate"]` or any other set** must be
   refused by the grant. Pinned in Task 1.
2. **A repository in both landing populations** is refused by change-manager's disjointness test.
   The proposer treats it as deploying (the more restrained reach). Pinned in Tasks 2 and 4.
3. **A passing bump in a deploying repository** must stay with the estate lander and never become a
   package. Pinned in Task 4.
4. **An unreadable landing policy, or one with no deploying block,** must stop the proposer (exit 2)
   and never shrink the lane silently to inert. Pinned in Task 4.
5. **A `live_estate` envelope differing from the profile's command shapes** must not be recognised.
   Pinned in Task 3.

---

### Task 1: intent-packages creates deploying packages; schema 3 grant accepts either reach

Repository: intent-packages. Model: sonnet.

**Files:**
- Modify: `src/intent_packages/cli.py` (`create-standing --reach`, choices `source_repository` and
  `live_estate`, default `source_repository`).
- Modify: `src/intent_packages/factory/scaffolds.py::create_standing` (new keyword `reach: str`).
- Modify: `src/intent_packages/profiles/dependency_update.py::render_standing_package` (writes
  `reach: [<reach>]`; the inert-premise text near lines 445, 501, 605 and 617 becomes
  reach-dependent, and a `live_estate` package's rollback reads "revert the merge; the revert
  redeploys").
- Modify: `src/intent_packages/approval_policy.py` (schema 3, `reach_options`).
- Modify: `approval-policy.toml` (`version = 3`, `reach_options`, rationale names Ruling 2's
  enforcement at dispatch).
- Test: the existing approval-policy and create-standing test modules.

**Interfaces:**
- Produces: `intent-packages create-standing ... --reach live_estate`, which writes `reach:
  ["live_estate"]`. Task 4 calls it.
- Produces: `Grant.reach_options: tuple[tuple[str, ...], ...]`. Schemas 1 and 2 load their `reach`
  as a one-option tuple.

- [ ] **Step 1: Write the failing tests.**
  - `test_create_standing_live_estate_writes_that_reach`: the package's `reach == ["live_estate"]`,
    and its rollback text names a redeploy.
  - `test_create_standing_defaults_to_source_repository`.
  - `test_schema_3_accepts_each_option`: both single-member reaches pass `refusals_for`.
  - `test_schema_3_refuses_other_reaches`: `[]`, `["source_repository","live_estate"]`,
    `["external_system"]` and a missing `reach` each give `REACH_NOT_PERMITTED`.
  - `test_schema_2_document_still_loads`.
  - `test_schema_3_rejects_reach_field`: a schema 3 grant carrying `reach` fails to load.
  - `test_approver_is_v3`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `make check` and confirm it's green.** Mutation-check each new test, then commit.

### Task 2: change-manager approves deploying repositories' dependency-update records

Repository: change-manager. Model: sonnet.

**Files:**
- Modify: `app/work_policy.py`. `objections` accepts `repository in inert.repositories |
  current().repositories`, case-folded as today, and the objection renames to
  `target_repository_not_in_policy`. The docstrings change to match.
- Modify: `app/work_changes.py` (detail and docstring wording).
- Test: the work policy tests.

**Interfaces:**
- Produces: the objection string `target_repository_not_in_policy`. Grep orchestrator for
  `target_repository_not_inert` and update any reader in Task 4.

- [ ] **Step 1: Write the failing tests.**
  - `test_deploying_repository_record_is_approved`: a conforming record for `AlobarQuest/brain` gets
    `work-policy` approval.
  - `test_record_outside_both_populations_stays_pending`: the objection is
    `target_repository_not_in_policy`.
  - `test_landing_populations_are_disjoint`: for each policy version the module defines,
    `repositories ∩ inert_landing.repositories` is empty.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gate and confirm it's green.** Mutation-check, then commit.

### Task 3: Known-good dependency-update patterns under `live_estate`

Repository: orchestrator. Model: sonnet.

**Files:**
- Modify: `src/orchestrator/factory-policy.toml`. Copy the three `[[reach.source_repository.known_good]]`
  rows (uv, npm, pip: names, capabilities including `github.pr.merge`, budgets, `conformance_status
  = "green"`, the exact `command_shapes`, no `target_repositories`) to
  `[[reach.live_estate.known_good]]`. Names may repeat across rows, as the rotation patterns already
  do. A comment says the two copies must move together.
- Test: `tests/services/test_authority_known_good.py`; `tests/contract/test_profile_budget_agreement.py`.

- [ ] **Step 1: Write the failing tests.**
  - `test_live_estate_dependency_update_envelope_is_recognised`: parametrized over uv for
    `AlobarQuest/brain` and `AlobarQuest/change-manager`, plus npm and pip. The envelope is built the
    way the existing `source_repository` tests build it, with reach `["live_estate"]`, and is
    recognised.
  - `test_live_estate_refuses_the_attack_commands`: every attack command Part 3's tests refuse under
    `source_repository` is refused under `live_estate`.
  - `test_source_and_live_estate_dependency_patterns_are_equal`: the three rows are identical apart
    from their table, so the copies can't drift.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Confirm `make check` and `scripts/check_profile_budget_agreement.py` are green.**
  Mutation-check, then commit.

### Task 4: The bump proposer admits deploying repositories

Repository: orchestrator. Model: opus (lane logic, populations and docs).

**Files:**
- Modify: `src/bump_proposer/landing_policy.py`:
  - Rename `InertLanding` to `LandingPolicy`.
  - Add `deploying: frozenset[str]` (top-level `repositories`, lowercased) and
    `deploying_excluded_ecosystems: frozenset[str]` (from the `landing` block). An empty or missing
    deploying set is a `LandingPolicyError`, like the inert one.
  - Add `declares(repository) -> bool` (inert or deploying).
  - Add `deploys(repository) -> bool`.
  - Change `permits(repository: str, ecosystem: str | None) -> bool`, which picks the block per
    Ruling 5.
  - Rename `read_inert_landing` to `read_landing_policy`, and update every caller, including
    `membership_check/cli.py`.
- Modify: `src/bump_proposer/cli.py`:
  - `_lane` keeps grant ∩ (declared ∪ packaged).
  - `not-declared-inert` becomes `not-declared-landing`. Check it against
    `test_no_reported_kind_is_a_substring_of_another` and `unread_statuses`.
  - `_consider` calls `permits(repository, ecosystem)`.
  - Rewrite the docstrings that say "inert only".
- Modify: `src/bump_proposer/standing.py::create`, which gains `reach: str` and passes `--reach`.
  The caller passes `"live_estate"` when `rule.deploys(repository)`, else `"source_repository"`.
- Modify: `src/membership_check/consistency.py`, which gains
  `_require("the deploying landing population", deploying, updates, "dependency_updates")` beside
  the inert row. `check()` gains `deploying: frozenset[str]`.
- Docs:
  - New: ADR-0028 amendment 3 (Rulings 1, 2, 3, 5).
  - New: ADR-0025 amendment 2 (Rulings 6, 7).
  - Rewrite: the CLAUDE.md work-policy bullet ("inert list" becomes "inert or deploying
    population").
  - Rewrite: `docs/operations/landing-lanes.md` near lines 364–385.
  - Rewrite: the `pr_merge_admission.py` module docstring near lines 20–23 and 38–41.
  - Rewrite: `change_proposer/cli.py`'s human-criterion comment near lines 190–197.

**Interfaces:**
- Consumes: Task 1's `--reach`. Task 2's objection string appears only in record details.

- [ ] **Step 1: Write the failing tests.**
  - `test_failed_bump_in_deploying_repository_creates_live_estate_package`: a fake intent-packages
    `create-standing` call receives `--reach live_estate`, and the pass reports `created` and
    `proposed`.
  - `test_failed_bump_in_inert_repository_still_creates_source_repository_package`.
  - `test_passing_bump_in_deploying_repository_is_skipped`.
  - `test_github_actions_bump_in_deploying_repository_is_this_lanes`.
  - `test_docker_bump_in_deploying_repository_is_left_to_the_lander`.
  - `test_repository_in_neither_population_is_not_declared_landing`.
  - `test_policy_without_deploying_population_is_unreadable`: exit 2.
  - `test_repository_in_both_populations_deploys`.
  - `test_membership_check_reports_a_deploying_repository_without_dependency_updates`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement, including the docs.**
- [ ] **Step 4: Run `make check` and confirm it's green.** Mutation-check, then commit.

### Task 5: Measure, ship in order, prove

- [ ] **Step 1: Measure the lane.** Re-run the population measurement (landing policy, App Brain,
  grants), and stop if it differs from the table in "Measured scope". Then dry-run the proposer from
  the worktree with the intent-packages worktree as its checkout. Count `would-create`,
  `would-propose` and `would-alert` for brain and change-manager. More than five alerts must be
  reported before the acting pass.
- [ ] **Step 2: Ship change-manager.** Its merge redeploys it; verify the served revision.
- [ ] **Step 3: Ship intent-packages, then the orchestrator.** The proposer calls `--reach`, so
  fast-forward and sync the local intent-packages checkout before pulling the main tree. Deploy the
  orchestrator, since the patterns live in the image.
- [ ] **Step 4: Run the first acting pass through launchd.** Confirm any created package's reach,
  the record's `work-policy` approval and the carried intake. Run `membership_check` once and
  confirm it passes with the new row.
- [ ] **Step 5: Clean up** the worktrees, test database and branches.
