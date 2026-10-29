# Landing layer, Part 2: the grant is the authority — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No landing path acts on a repository unless the SDS member record grants the service that
path serves, and `membership_check` reports a granted repository that no landing path reaches.

**Architecture:** One shared term in `services/landing/terms.py` reads the member record through the
existing `target_grant.MemberRecordGrants` (the same reader dispatch admission uses). The inert and
estate admissions ask it, so every act built on them (land, branch update, held close) is bounded by
it; `pr_merge_admission` asks it with the service its change class maps to. The landers classify the
new refusal as an exception: a repository the record doesn't grant is not a held pull request but a
membership disagreement, which `membership_check` reports. `membership_check` gains the reverse
direction.

**Tech Stack:** Python 3 (`.python-version`), SQLAlchemy, FastAPI services, pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, Part 2. This plan covers Part 2
only.

## Global constraints

- Work in `.worktrees/landing-p2` with its own `.venv` and database `orchestrator_test_landing_p2`
  (`docs/operations/local-development.md`). Never edit, check out or run `uv` in the main tree.
- Never put a secret in a prompt, tool argument or transcript. Every parsed `bws` call passes
  `--color no`.
- This part changes `src/orchestrator/`, so it ships as a release image and a deploy
  (`docs/operations/deploy.md`); the deploy command owns the build. No migration.
- Word guards (ADR-0051) apply to code under `src/orchestrator/`; reword rather than allowlist.
- No reported finding kind may be a substring of another
  (`test_no_reported_kind_is_a_substring_of_another`).
- Module-level string collections used in membership tests are registered or marked
  `# not-a-vocabulary: <reason>`.
- `make check` is green before each commit that touches logic.

## Review focus

1. **A fork sync in a fork granted only one of its two services.** It must be refused; a fork's sync
   needs both `security_patches` and `dependency_updates`. Pinned in Task 1.
2. **A held pull request in a repository whose grant was withdrawn.** The held-close act keys on the
   ceiling refusal "whatever else refuses"; it must not close a pull request in an ungranted
   repository. Pinned in Task 1.
3. **Repository spelling.** The record stores GitHub's spelling; landers fold to lower case. A
   mixed-case repository must still read as granted. Pinned in Task 1.
4. **A retired member, or a grant in a state other than `granted`.** Both read as not granted.
   Pinned in Task 1.
5. **An ungranted repository raising a held-PR alert every day.** The lander must classify the new
   refusal as an exception (not held), so `lander.held_alerts` never fires for it. Pinned in Task 1.

---

### Task 1: The landing lanes ask the grant

**Files:**
- Modify: `src/orchestrator/services/landing/terms.py` (the term and its refusal code)
- Modify: `src/orchestrator/services/landing/inert_landing_admission.py`,
  `src/orchestrator/services/landing/estate_landing_admission.py`
- Modify: `src/orchestrator/services/landing/held_close.py` (only if the close act would otherwise
  proceed past a grant refusal)
- Modify: `src/inert_lander/cli.py:157` and `src/estate_lander/cli.py:128` (`Lane.exception`)
- Test: `tests/services/test_inert_landing_admission.py`, `tests/services/test_estate_landing_admission.py`,
  `tests/services/test_held_close.py`, `tests/inert_lander/test_inert_lander.py`,
  `tests/estate_lander/test_estate_lander.py`

**Interfaces:**
- Consumes: `orchestrator.services.execution.target_grant.MemberRecordGrants(session).is_granted(repository: str, service: str) -> bool`,
  `DEPENDENCY_UPDATES_SERVICE`.
- Produces, in `terms.py`:
  - `LANDING_REPOSITORY_NOT_GRANTED: Final = "landing_repository_not_granted"`
  - `SECURITY_PATCHES_SERVICE: Final = "security_patches"` (if no constant exists already; reuse one
    if it does)
  - `grant_term(grants: TargetGrantSource, repository: str, services: Iterable[str]) -> Term`:
    met only when every named service is granted; otherwise one refusal,
    `LANDING_REPOSITORY_NOT_GRANTED`.
- The services each subject needs:

  | Lane and subject | Services |
  |---|---|
  | Inert lane, a Dependabot pull request | `dependency_updates` |
  | Inert lane, a wholesale author (fork upstream sync) | `security_patches` and `dependency_updates` |
  | Estate lane | `dependency_updates` |

- Both landers add `"landing_repository_not_granted"` to `Lane.exception`.

- [ ] **Step 1: Write the failing tests.**
  - `test_inert_landing_admission.py`: `test_an_ungranted_repository_is_refused_not_granted`,
    `test_a_fork_sync_needs_both_security_patches_and_dependency_updates` (parametrised: neither,
    only `security_patches`, only `dependency_updates` refuse; both admit),
    `test_a_mixed_case_repository_reads_as_granted`,
    `test_a_retired_member_or_a_grant_not_in_state_granted_is_refused`.
  - `test_estate_landing_admission.py`: `test_an_ungranted_repository_is_refused_not_granted`.
  - `test_held_close.py`: `test_a_held_pull_request_in_an_ungranted_repository_is_not_closed`
    (the hold breached and the grant withdrawn: no close call reaches the gateway).
  - Each lander's test file: `test_an_ungranted_repository_is_an_exception_not_held`.
  Use the member-record fixtures the existing hold tests already build (`holds_for` tests) to grant
  and withdraw.
- [ ] **Step 2: Run them and see them fail.**
  `.venv/bin/pytest tests/services/test_inert_landing_admission.py tests/services/test_estate_landing_admission.py tests/services/test_held_close.py tests/inert_lander tests/estate_lander -q`
- [ ] **Step 3: Implement** `grant_term`, ask it in both admissions (the inert one after the author is
  known, so a wholesale author gets both services), make the close act refuse when it refuses, and
  add the code to both `Lane.exception` sets.
- [ ] **Step 4: Run `make check`.** Expected: green, including the substring guard.
- [ ] **Step 5: Commit.** `git commit -m "landing: the inert and estate lanes act only on a granted repository"`

### Task 2: The factory merge asks the grant

**Files:**
- Modify: `src/orchestrator/services/landing/pr_merge_admission.py` (`admission_for`)
- Test: `tests/services/test_pr_merge_admission.py`, `tests/api/test_pr_merge_admission_api.py`

**Interfaces:**
- Consumes: `grant_term` (Task 1), `target_grant.service_for_change_class(change_class: str) -> str`,
  the envelope's `change_class` as `admission_for` already reads it.
- Produces: `MERGE_REPOSITORY_NOT_GRANTED: Final = "merge_repository_not_granted"` in
  `pr_merge_admission.py`, appended to `refusals` and joined into `satisfied` beside the other
  terms. Map the term's refusal to this code; don't reuse the landing code, so the two lanes'
  vocabularies stay apart.

- [ ] **Step 1: Write the failing tests.**
  `test_a_unit_targeting_an_ungranted_repository_is_refused` (dependency-update class, repository
  without `dependency_updates`), `test_the_grant_follows_the_change_class` (an app-change class needs
  `app_changes`; `dependency_updates` alone refuses), and, at the API level,
  `test_the_refusal_is_served_in_the_admission_answer`.
- [ ] **Step 2: Run them and see them fail.**
  `.venv/bin/pytest tests/services/test_pr_merge_admission.py tests/api/test_pr_merge_admission_api.py -q`
- [ ] **Step 3: Implement** the term in `admission_for`.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "pr-merge: a unit merges only into a repository granted its change class"`

### Task 3: `membership_check` compares both directions

**Files:**
- Modify: `src/membership_check/consistency.py` (`check`)
- Test: `tests/membership_check/test_consistency.py`
- Modify: `docs/operations/membership.md` (one sentence on the new row), `CLAUDE.md` (the dispatch
  admission invariant becomes "Dispatch admission and every landing admission read the member
  record's grant …", one bullet, kept short, below `<!-- code-standards:end -->`)

**Interfaces:**
- Consumes: the inert and deploying populations `check` already receives from the landing policy, and
  the `dependency_updates` grant set it already computes.
- Produces: a disagreement for each non-retired member granted `dependency_updates` that is in neither
  population, worded as "granted dependency_updates, but no landing path reaches it".

- [ ] **Step 1: Write the failing tests.**
  `test_a_granted_repository_no_landing_path_reaches_is_a_disagreement`,
  `test_a_retired_member_is_not_required_to_be_reached`, and
  `test_the_live_population_agrees` extended to cover the reverse direction.
- [ ] **Step 2: Run them and see them fail.** `.venv/bin/pytest tests/membership_check -q`
- [ ] **Step 3: Implement** the reverse row and the doc edits.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "membership_check: a granted repository no landing path reaches is a disagreement"`

### Task 4: Ship, deploy and prove

- [ ] **Step 1: Review.** Final whole-branch review; fix what holds up.
- [ ] **Step 2: Land.** Open the pull request (plan and Part 2), watch it with
  `gh pr checks --watch --interval 30`, merge on green, and confirm `state == MERGED`.
- [ ] **Step 3: Pull the main tree** between inert passes (not at :35) and outside 02:15 to 05:15. No
  new console script, so no `uv sync`.
- [ ] **Step 4: Deploy** the merge commit with the deploy command in `docs/operations/deploy.md`
  (it owns the build), then confirm `https://sds.alobar.net` serves the new revision. No dispatched
  run may be live; check first.
- [ ] **Step 5: Prove it.**
  - Ask `GET /api/v1/inert-pr-merge-admission` with the SYSTEM bearer (fetched in-process) for a pull
    request in a repository that isn't a member, and confirm `landing_repository_not_granted` is
    among the refusals. Read-only.
  - Confirm the next inert pass's report is unchanged for orchestrator#506 and #508 (all 13 members
    are granted), and run `scripts/run-membership-check.sh`: exit 0.
- [ ] **Step 6: Clean up.** Check the main tree's `git status`, then remove the worktree, its test
  database and the merged branch.
