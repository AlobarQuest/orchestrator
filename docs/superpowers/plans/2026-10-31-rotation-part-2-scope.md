# Rotation Part 2: act by grant, observe by membership — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Withdrawing a `credential_rotation` grant stops new rotation work and a running rotation, and the membership check reports every disagreement between grants, flagged rows, and registry files.

**Architecture:** The executor, proposer, and decomposer each read the member record and refuse on a missing grant. `REGISTRY_REPOSITORIES` stays as the ceiling, so a grant can only narrow it. The executor reads the grant before a claim and before each step, and a read failure there never spends an attempt.

**Tech Stack:** Python 3.14, httpx through `ConfinedClient`, `sds_members`, Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-rotation-layer-design.md`, Part 2, and Devon's decisions 1 and 2.

## Global Constraints

- Worktree `.worktrees/rotation-p2`, `TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1/orchestrator_test_rotation_p2`. Absolute `cd` in every Bash call. Never touch the main tree.
- A grant counts only in state `granted` on a non-retired member, case-folded, matching `target_grant`.
- The grant only narrows. An orchestrator answer can't add a repository, credential, or destination.
- An unreadable grant and an alert-hub outage never fail a unit or spend an attempt. An explicit withdrawal stops a claimed unit, as a handed-back flag does.
- A stored copy in a member with no grant is allowed and is never compared (decision 2).
- No code changes under `src/orchestrator/`: `GET /api/v1/members` already admits every authenticated role, so no image deploy.
- `make check` needs `ORCHESTRATOR_DATABASE_URL` and `SECURITY_STANDARDS_DIR=$HOME/Projects/security-standards`.

**Differences from the spec, each Claude's call:**

- The spec's live proof names `sds-canary`, but that member holds no registry, so a withdrawal there proves nothing. Task 1 adds a read-only `rotation-worker grants` command instead, which proves each instance's identity reads the record.
- The first `membership_check` row compares handed-over rows through `rotation_worker.registry.rotated_by_sds`, read once from the lane's own mirror.

## Review Focus

- The member record answers 503 during an orchestrator restart that a rotation itself caused: the claimed unit carries on.
- A member record whose body isn't the expected shape: unread, never "not granted".
- A grant spelled in a different case from the registry repository: it matches.
- A retired member with a `granted` row: not granted.
- A pass with no due credential or no ready unit: no member read at all.

---

### Task 1: The executor reads the grant

**Files:**
- Modify: `src/rotation_worker/orchestrator_client.py`, `src/rotation_worker/worker.py`, `src/rotation_worker/cli.py`, `tests/rotation_worker/test_client_surface.py`
- Create: `src/rotation_worker/grants.py`
- Test: `tests/rotation_worker/test_grants.py`

**Interfaces:**
- Produces: `OrchestratorClient.members() -> dict[str, Any]`, one route `GET /api/v1/members` added to `permits`.
- Produces: `grants.rotation_granted(body: Mapping[str, Any], repository: str) -> bool`, raising `OrchestratorError` on any shape it can't read. A repository absent from the record answers `False`.
- Produces: `worker._granted(setup, repository) -> bool`, and `Setup`-free use in `_before_step` through a value carried on `_Recorder` or the run, holding the answer read before the claim.
- Produces: `cli.grants(config) -> int`, a read-only command printing each `REGISTRY_REPOSITORIES` entry's answer, exit `EXIT_UNREADABLE` if unread.

Behavior:
- In `_take`, immediately after `check_unit`, read the grant for `approved.registry.repository`. Not granted: log `unit_waiting`, record `Wait(f"{repository} has no credential_rotation grant")` on the ordinary clock, return 0, and claim nothing. An `OrchestratorError` falls to the existing `TRANSIENT` skip.
- In `_before_step`, beside the flag, read the grant again. `False` raises `ContractError(f"{credential_id}: the credential_rotation grant was withdrawn mid-rotation")`. An `OrchestratorError` logs `grant_unread` and keeps the answer from before the claim.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_granted_repository_answers_true`, `test_the_match_folds_case`, `test_a_retired_member_is_not_granted`, `test_an_absent_repository_is_not_granted`, `test_a_malformed_record_is_unread_not_ungranted`, `test_no_grant_waits_unclaimed_and_starts_the_clock`, `test_an_unread_grant_before_the_claim_skips_the_unit`, `test_a_withdrawn_grant_stops_before_the_next_step`, `test_an_unread_grant_mid_rotation_carries_on`, `test_a_grant_outside_the_ceiling_adds_nothing`, `test_no_ready_unit_reads_no_members`, `test_grants_command_prints_each_registry_repository`.

- [ ] **Step 2: Run, fail. Step 3: Implement; existing fake clients gain `members()`. Step 4: Run `tests/rotation_worker`, `tests/architecture`, pass.**
- [ ] **Step 5: Mutate each guard, with a green baseline and `__pycache__` cleared, and report the count and every survivor.**
- [ ] **Step 6: Commit.**

### Task 2: The proposer refuses an ungranted credential

**Files:**
- Modify: `src/rotation_proposer/cli.py`, `src/rotation_proposer/outcome.py`, `tests/architecture/test_out_of_process_isolation.py`
- Test: `tests/rotation_proposer/test_grant.py`

**Interfaces:**
- Consumes: `sds_members.read_members(token=, credential_key_id=OBSERVER_KEY_ID, base_url=)`, `sds_members.populations.granted`.
- Produces: `"ungranted"` in `FINDING_STATUSES`.

Behavior: on `--submit`, read the member record once, lazily, the first time a handed-over due credential needs it, using `_once`. `MembersUnreadable` exits 2 through the existing unusable path. A due credential whose `entry.repository` has no grant reports `ungranted` before any package write, generation, or record. A dry run prints that the grant wasn't read and carries on.

- [ ] **Step 1: Write failing tests**

Tests: `test_an_ungranted_credential_writes_nothing`, `test_a_granted_credential_proceeds`, `test_an_unreadable_record_exits_2_and_writes_nothing`, `test_nothing_due_reads_no_members`, `test_a_dry_run_says_the_grant_was_not_read`, `test_ungranted_raises_its_finding_alert`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run `tests/rotation_proposer`, `tests/architecture`, pass. Step 5: Commit.**

### Task 3: The decomposer refuses an ungranted credential

**Files:**
- Modify: `src/rotation_decomposer/client.py`, `src/rotation_decomposer/cli.py`, `tests/rotation_decomposer/test_client_surface.py`
- Test: `tests/rotation_decomposer/test_grant.py`

**Interfaces:**
- Produces: `Orchestrator.members() -> list[dict[str, Any]]`, with `orchestrator_permits` admitting `GET /api/v1/members`.

Behavior: `_revision` reads the record once per pass, through `_once`, only when it reaches a revision it would propose. No grant gives `Outcome(subject, "refused", ...)`. A `CallError` gives the existing `unread` outcome. Grant matching follows the Global Constraints rule; share `rotation_worker.grants.rotation_granted`, which the decomposer's isolation row already permits.

- [ ] **Step 1: Write failing tests**

Tests: `test_an_ungranted_credential_is_refused_and_not_proposed`, `test_a_granted_credential_is_proposed`, `test_an_unread_record_is_unread`, `test_no_candidate_reads_no_members`.

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run `tests/rotation_decomposer`, `tests/architecture`, pass. Step 5: Commit.**

### Task 4: The membership check compares both ways

**Files:**
- Modify: `src/membership_check/consistency.py`, `src/membership_check/cli.py`
- Test: `tests/membership_check/test_rotation_scope.py`

**Interfaces:**
- Produces: `check(...)` gains `flagged_repositories: Iterable[str]`, `registry_files: Iterable[str]`, and `registries_read: bool = True`.

Behavior: three new outcomes, beside the existing tuple row:
- `<repo>: a rotated_by_sds registry row names it, but the member record does not grant credential_rotation`.
- `<repo>: granted credential_rotation, but no rotated_by_sds registry row is there`, skipped when `registries_read` is false, so an unread mirror never reads as an empty population.
- `<repo>: holds .cred-consumers.toml, outside rotation_worker REGISTRY_REPOSITORIES`, for any non-retired member, read through the same GitHub path `_declarations` uses. A 404 is absent; any other failure is a gap, never a disagreement.

- [ ] **Step 1: Write failing tests**

Tests: `test_a_flagged_row_without_a_grant_disagrees`, `test_a_grant_without_a_flagged_row_disagrees`, `test_an_unread_registry_skips_the_reverse_row`, `test_a_registry_file_outside_the_tuple_disagrees`, `test_a_failed_file_read_is_a_gap`, `test_a_retired_member_is_not_scanned`, `test_a_stored_copy_is_never_compared`, `test_todays_estate_agrees` (four repositories, four grants).

- [ ] **Step 2: Run, fail. Step 3: Implement. Step 4: Run `tests/membership_check`, pass. Step 5: Commit.**

### Task 5: Docs and the whole gate

**Files:**
- Modify: `docs/operations/rotation-executor.md`, `docs/operations/rotation-proposer.md`, `docs/operations/membership.md`, `CLAUDE.md`

- [ ] **Step 1: Write the docs.** State where each lane reads the grant, that withdrawing it mid-rotation fails the unit as a handed-back flag does and can leave an overlap open, and the runbook step for that. Correct `rotation-proposer.md`'s claim that the grant is checked at onboarding and its prose "four registry repositories" where it is a rule. Rewrite the `CLAUDE.md` rotation scope sentence, stated once.
- [ ] **Step 2: Run `make check`** into `/private/tmp/claude-501/-Users-devon-Projects-orchestrator/9e28ea29-0ae6-4126-b510-9ad6a925a3d9/scratchpad/p2-check.log`.
Expected: exit 0, read from the log with its test totals.
- [ ] **Step 3: Whole-branch review by a fresh reviewer, then `/code-review`, then one fix wave.**
- [ ] **Step 4: Commit, and open the pull request.**

### Task 6: Deploy and live proof

This task writes to production. It runs only after Devon approves this plan.

- [ ] **Step 1: Confirm no rotation unit is ready, claimed, or executing.** Merge and confirm `MERGED`.
- [ ] **Step 2: Reinstall the VM** with `provision.sh` at the full commit, and the Mac with its installer from the main tree. Confirm both log the new `revision`.
- [ ] **Step 3: Run `rotation-worker grants` on each instance.** Expected: all four repositories `granted`. This proves each identity reads the record.
- [ ] **Step 4: Run one membership pass** through its launcher. Expected: no new disagreement.
- [ ] **Step 5: Run one proposer and one decomposer pass.** Expected: both exit as before. With nothing due, neither reads the record, so state that plainly.
- [ ] **Step 6: Record the proof, update the SDS Layer Map and year plan, and list every ruling with its cost if wrong.**

**No live proof in this part, stated plainly:** a refusal on a real withdrawal (`ungranted`, the unit waiting, and the stop before a step). Proving those means withdrawing a real grant during a canary rotation, which needs Devon's say-so.

## Live proof, recorded 2026-10-31

Merged as `013507cf` (#572). Both instances run that commit. No orchestrator image changed.

- **Grant read:** `rotation-worker grants` on the VM and the Mac each printed all four registry repositories as `granted`.
- **Membership check:** one pass through its launcher exited 0 on the new code, with no new disagreement.
- **Proposer and decomposer:** one pass of each exited as before. With nothing due, neither read the record.

Not proven live: `ungranted`, a decomposer refusal, a unit waiting on a grant, and a stop before a step. Proving those means withdrawing a real grant.
