# Bridge layer, Part 2: scope from the member record — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every bridge lane, and dispatch admission, takes its scope from the SDS member record's
grants instead of hand-kept lists and `factory-target.toml`.

**Architecture:** Admission reads grants in-process through `list_members(session)`; the lanes read
them through `sds_members` with the observer bearer, which each bridge launcher now fetches on every
run. One mapping turns a change class into the grant that covers it (`dependency-update` →
`dependency_updates`, anything else → `app_changes`), defined once on each side of the process
boundary and held equal by a test. `factory-target.toml` stops being read by anything that acts; its
parser moves to `membership_check`, its only remaining reader. The carrier's capability source gains
an age limit.

**Tech Stack:** Python 3 (`.python-version`), FastAPI service layer, pytest, Bash launchers.

**Spec:** `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`, Part 2 (and "Decisions Devon
made", "Anti-goals", "Constraints"). Part 1 shipped as #533.

## Global constraints

- Work in this worktree (`.worktrees/bridge-p2`) with its own `.venv` and its own
  `orchestrator_test_bridge_p2` database (`docs/operations/local-development.md`). Never edit, check
  out or run `uv` in the main tree.
- Never put a secret in a prompt, tool argument or transcript. Fetch bearers inside one process with
  `scripts/sds-bws.sh`; every parsed `bws` call passes `--color no`.
- Don't poll GitHub without a pause (`--watch --interval 30`).
- Compare repositories case-folded everywhere: the record stores GitHub's spelling
  (`AlobarQuest/brain`) and is unique on `lower(repository)`.
- A grant counts only in state `granted`, on a member whose state isn't `retired`
  (`sds_members.populations.granted` already does this; the orchestrator side must match).
- An unreadable record stops the pass with the lane's own unusable-input code (2 for all three lanes,
  per each module's header), proposes and registers nothing, and is never read as a smaller
  population. Part 1's launchers turn that exit into an alert-hub alert.
- No lane discovers a repository and acts on it: scope only narrows to what the record grants.
- Every new or moved file gets its row in `layers.toml`; a lane importing `sds_members` gets it in
  its row of `tests/architecture/test_out_of_process_isolation.py`.
- `make check` must be green before each commit that touches logic.

## Rulings made while planning

Each is a builder's call the spec left open, recorded with its cost if wrong.

1. **`factory-target.toml` stays in the target repositories but is no longer authority.** Admission
   and the carrier stop reading it. project-standards still reads it (`sds_checks.py`,
   `onboard_checks.py`, `validator.py`), so `membership_check` keeps its two rows comparing the file
   with `app_changes` (now with the record as the source) until a later change retires the file and
   moves those readers. Cost if wrong: one more declaration to keep equal by hand until then, and
   the daily check says when it isn't.
2. **The approval-policy grant and the `factory-policy.toml` known-good `target_repositories` list
   move to Part 3.** In Part 2 the bump proposer's population doesn't move (all three standing
   packages are `infraops-mcp-server`), and Part 3 widens both lists anyway as standing-authority
   releases. Cost if wrong: intent-packages and policy keep gating by hand-kept lists until Part 3.
3. **The watcher is unchanged.** Its only write is retirement, which it makes only when every unit of
   a record's work is complete (`work_watcher/cli.py::_consider`); units exist only for records the
   carrier registered, so its acting scope already follows from the carrier's. A record with no work
   is "not a finding" by its header. Cost if wrong: a stale report about an uncarried record in an
   ungranted repository, which is still true and still needs a person.
4. **A granted repository with no standing package is logged, not reported.** Until Part 3 creates
   packages, ten granted repositories have none; reporting each would alert daily (anti-goal: alert
   noise). A standing package for an ungranted repository isn't acted on and isn't re-reported:
   `membership_check` already reports it daily. Cost if wrong: none beyond Part 3's timing.
5. **The capability source's age limit is 48 hours.** `~/.portfolio/portfolio.json` is rewritten by
   `com.devon.portfolio-scan` daily at 03:00 (measured 2026-10-28: plist `StartCalendarInterval`
   Hour 3, file mtime 03:00:27). The carrier runs at 07:05, so 48 hours tolerates one missed scan
   and refuses after two. Cost if wrong: a carrier pass that holds records after two missed scans,
   loudly.
6. **The unreadable-record exit is the lane's own code 2, not `sds_scope`'s 3.** The spec quotes
   `sds_scope`; the CLAUDE.md invariant says codes are per launcher, and 2 means "unusable input" in
   all three lanes' headers. Either way Part 1's alarm fires.
7. **The carrier keeps reading `portfolio.json`, with an age limit.** The spec offers "the
   conformance kit's own result with a stated age limit", and the file's `factory` block is that
   result: project-standards' portfolio scan writes the conformance checks
   (`runner.caller`, `factory.secrets`, `factory.landing_known`, `factory.pat_access`) into it.
   Reading the checks live would re-implement the kit here. Cost if wrong: capability is up to two
   days stale, never stale beyond that.
8. **The other `membership_check` rows stay.** `ROLLOUT_WORKFLOWS` against `rollouts` and
   `_ROLLBACKS` against `rollouts` now report dead configuration (a transcription or rollback for a
   repository nobody granted) or a grant with no transcription, which is still worth a daily row.
   Only Ruling 1 touches that module's rows, and it keeps them too.
9. **`sds_members` becomes a library row.** Its isolation row already forbids sibling programs; with
   `library=True` added, the three lanes can import it and keep their own `no_sibling_lanes`.

## Review focus

1. **Case mismatch.** An envelope or record naming `alobarquest/brain` when the member record says
   `AlobarQuest/brain` must count as granted. Pinned in Tasks 1 and 2.
2. **A grant that isn't `granted`.** `remediating`, `withdrawn`, `not_assessed`, or a retired member
   must not admit or carry. Pinned in Task 1; the lane side is pinned by
   `tests/sds_members/test_populations.py` (`remediating` and `retired` fixtures), and Task 2 adds
   `withdrawn`.
3. **A unit with no `change_class`.** Admission falls back to `required_capability` (a capability
   name such as `repo.edit`), which maps to `app_changes`. Pinned in Task 1.
4. **An unreadable record.** Token unset, a 500, or a malformed body must stop the pass with code 2
   and write nothing. Pinned in Tasks 2, 4 and 5.
5. **A named repository outside the grant.** `--repository` naming an ungranted repository must
   refuse, not run. Pinned in Tasks 4 and 5.

---

### Task 1: Admission reads the grant

**Files:**
- Create: `src/orchestrator/services/execution/target_grant.py`
- Delete: `src/orchestrator/services/execution/factory_target.py`, `tests/services/test_factory_target.py`
- Modify: `src/orchestrator/services/execution/dispatch.py`, `src/orchestrator/api/routes/execution.py`
- Modify: `src/sds_members/populations.py` (lane-side copy of the mapping)
- Modify: `tests/services/target_doubles.py`, `tests/services/test_dispatch.py`,
  `tests/contract/test_factory_target_contract.py` (see Step 6)
- Test: `tests/services/test_target_grant.py`
- Docs: `docs/decisions/0058-*.md` (amendment), `docs/decisions/0015-*.md` (amendment pointing to
  it), `CLAUDE.md` (rewrite the "Dispatch admission reads `factory-target.toml`" bullet),
  `docs/operations/dispatch.md`, `docs/operations/driving-a-unit.md`,
  `docs/operations/onboarding-a-target.md` (step 1 and the known-good section),
  `docs/operations/credentials.md` (the dispatch App's reach ruling), `layers.toml`

**Interfaces:**
- Produces (orchestrator): `service_for_change_class(change_class: str) -> str` returning
  `"dependency_updates"` for `"dependency-update"` and `"app_changes"` for every other value;
  `class TargetGrantSource(Protocol)` with `is_granted(self, repository: str, service: str) -> bool`;
  `class MemberRecordGrants` implementing it from a `Session` through `list_members`;
  `TARGET_REPOSITORY_NOT_GRANTED = "target_repository_not_granted"` in `dispatch.py`.
- Produces (lanes): `sds_members.populations.service_for_change_class(change_class: str) -> str`,
  same contract.
- `dispatch_work_unit` and its helpers take `grant_source: TargetGrantSource` where they took
  `target_source: FactoryTargetSource`.

- [ ] **Step 1: Write the failing tests** in `tests/services/test_target_grant.py`:
  - `test_a_dependency_update_needs_the_dependency_updates_grant` and
    `test_every_other_change_class_needs_the_app_changes_grant` (classes `maintenance-remediation`,
    `software-delivery`, and the fallback capability `repo.edit`).
  - `test_the_lane_and_admission_mappings_agree`: for every class in `factory-policy.toml`'s
    `[admission] change_classes` plus `repo.edit` and `""`, both functions return the same service.
  - `test_member_record_grants_fold_case`: a member `AlobarQuest/brain` granted `app_changes`
    answers `is_granted("alobarquest/brain", "app_changes") is True`.
  - `test_only_a_granted_state_on_a_live_member_counts`: parametrized over `remediating`,
    `withdrawn`, `not_assessed`, `not_applicable`, and `granted` on a retired member; each is False.
  - Seed members through the fixtures' session by writing the ORM rows directly (only
    `proposals.py` writes them in production; tests may write around it).
- [ ] **Step 2:** In `tests/services/test_dispatch.py`, replace the two declaration tests with
  `test_admission_refuses_an_ungranted_target` (reason `target_repository_not_granted`) and
  `test_admission_reads_the_grant_for_the_units_change_class` (a `dependency-update` unit in a
  repository granted only `dependency_updates` passes this term; a `software-delivery` unit there is
  refused). Replace the declaration double in `target_doubles.py` with a grants double.
- [ ] **Step 3: Run them to verify they fail.**
  Run: `.venv/bin/pytest tests/services/test_target_grant.py tests/services/test_dispatch.py -q`
  Expected: FAIL (module not found, old reasons).
- [ ] **Step 4: Implement.** `target_grant.py` as above. In `dispatch.py::_envelope_reason`, replace
  the declaration read after `target_repository_missing` with the grant check, using
  `_change_class(envelope, unit.required_capability)`; delete both old constants. The new reason is
  blocked, as the old ones were (`_is_skipped_reason` lists only the window and the off switch). The
  route builds `MemberRecordGrants(session)`.
  Add `service_for_change_class` to `populations.py`.
- [ ] **Step 5: Run to verify they pass.** Same command. Expected: PASS.
- [ ] **Step 6: Retire the declaration pins.** Delete `tests/services/test_factory_target.py`. In
  `tests/contract/test_factory_target_contract.py`, delete the orchestrator-versus-carrier
  agreement; if the file also pins the parser to project-standards' copy, keep that pin and point it
  at `membership_check.declaration.parse` (created in Task 2 — so in this task, leave that one test
  importing `work_carrier.declaration` and let Task 2 repoint it).
- [ ] **Step 7: Docs.** The ADR-0058 amendment records decision 1 as implemented and Rulings 1 to 8
  with their costs. ADR-0015 gets a one-paragraph amendment pointing to it. Rewrite the
  CLAUDE.md admission bullet as the rule now stands (admission reads the member record's grant for
  the unit's change class, `target_repository_not_granted`, mapping held equal to the lanes' by
  `test_target_grant.py`). Onboarding step 1 becomes "grant the service on `/review`", keeping the
  `factory-target.toml` commit because project-standards and `membership_check` still read it.
  In `credentials.md`, the ruling that the dispatch App's wide installation reach is safe because
  every call site is bounded names the grant as admission's bound, replacing the declaration, and
  the CLAUDE.md bullet on that ruling changes "a declaration or allowlist check" to "a grant or
  allowlist check".
- [ ] **Step 8: `make check`, then commit.**
  `git commit -m "Admission reads the member record's grant, not factory-target.toml"`

### Task 2: The carrier reads the grant

**Files:**
- Modify: `src/sds_members/client.py` (environment reader)
- Move: `src/work_carrier/declaration.py` → `src/membership_check/declaration.py`
  (`git mv`; `layers.toml` row moves with it)
- Modify: `src/work_carrier/workability.py`, `src/work_carrier/cli.py`
- Modify: `src/work_watcher/github.py` (own `TOKEN_ENV`), `src/membership_check/cli.py`,
  `src/membership_check/consistency.py`
- Modify: `scripts/run-work-carrier.sh` (fetch the observer on every run)
- Modify: `tests/architecture/test_bridge_edges.py`, `tests/architecture/test_out_of_process_isolation.py`,
  `tests/membership_check/test_consistency.py`, `tests/contract/test_factory_target_contract.py`
- Test: `tests/work_carrier/test_workability.py`, `tests/work_carrier/test_cli.py` (or the existing
  carrier test files that cover `assess` and `run`), `tests/sds_members/test_client.py`,
  `tests/scripts/` launcher tests

**Interfaces:**
- Consumes: `sds_members.populations.service_for_change_class` (Task 1).
- Produces: `sds_members.client.OBSERVER_TOKEN_ENV: Final = "SDS_MEMBERS_OBSERVER_TOKEN"` and
  `read_members_from_environment(*, transport=None) -> list[Member]`, which raises
  `MembersUnreadable` when the variable is unset or empty and otherwise calls `read_members` with
  key id `orchestrator-observer`. Tasks 4 and 5 use it.
- `workability.assess(repository, granted: bool, portfolio)` replaces the `declaration` parameter;
  the term formerly "opts in" becomes "granted" and keeps that term's outcome.

- [ ] **Step 1: Write the failing tests.**
  - `test_read_members_from_environment_refuses_without_a_token` and
    `test_read_members_from_environment_uses_the_observer_key_id` (stub transport records the
    request).
  - Carrier: `test_a_record_in_an_ungranted_repository_is_not_workable`,
    `test_a_dependency_update_record_is_judged_by_dependency_updates` (repository granted only
    `dependency_updates`: workable on that term), `test_the_grant_check_folds_case`.
  - `test_an_unreadable_member_record_stops_the_pass_and_registers_nothing`: reader raises
    `MembersUnreadable`; `run([... "--register"])` returns 2, the registrar double saw no call, and
    the output names the cause. Also with the variable unset.
  - `test_an_operational_record_needs_no_grant`: a rotation record from
    `STANDING_OPERATIONAL_PACKAGES` is judged as today.
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/pytest tests/work_carrier tests/sds_members -q`
- [ ] **Step 3: Implement.** The carrier reads the members once per pass, before judging any record,
  builds the case-folded set per service, and looks up each record's
  `service_for_change_class(payload["profile"])` against its `target_repository(payload)`. Move the
  declaration module; give `work_watcher/github.py` its own `TOKEN_ENV = "WORK_CARRIER_GITHUB_TOKEN"`
  (the launcher variable doesn't change, because the watcher still needs it).
- [ ] **Step 4: Launcher.** `run-work-carrier.sh` fetches the observer bearer
  (`f793576f-e9aa-4f9d-8089-b4a000b9e2d5`) on every run and exports it as
  `SDS_MEMBERS_OBSERVER_TOKEN`. Its launcher test asserts the fetch happens without `--register`.
- [ ] **Step 5: Edges and isolation.** In `test_bridge_edges.py`, the carrier's row gains
  `("GET", "/api/v1/members")` and the observer bearer, its GitHub column empties, and its
  orchestrator predicate becomes the union of its own client's and `sds_members.client`'s. Repoint
  `test_control_a_widened_github_predicate_fails_the_table` at the watcher's GitHub predicate. Mark
  the `sds_members` isolation row `library=True` (Ruling 9) and add `sds_members` to the carrier's
  row. Point `membership_check` and its tests at `membership_check.declaration`. In
  `tests/sds_members/test_populations.py`, add a `withdrawn` grant to the fixture and assert
  `granted` excludes it.
- [ ] **Step 6: `make check`, then commit.**
  `git commit -m "work-carrier: carry by the member record's grant"`

### Task 3: The capability source has an age limit

**Files:**
- Modify: `src/work_carrier/workability.py`
- Test: the carrier's workability tests

**Interfaces:**
- Produces: `PORTFOLIO_MAX_AGE_HOURS: Final = 48` (a plain int with no off value).

- [ ] **Step 1: Write the failing tests.**
  `test_a_portfolio_older_than_the_limit_leaves_capability_undecided` (mtime 49 hours old: the
  capability and permission terms are `UNDECIDED`, the reason names the age and the limit, and the
  carrier exits 2) and `test_a_portfolio_inside_the_limit_is_read` (47 hours: judged as today). Set
  the mtime with `os.utime`; never sleep.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** in `load_portfolio` or `_checks`, wherever the age is already recorded,
  so a stale file yields `UNDECIDED`, never `workable`.
- [ ] **Step 4: Run to verify they pass; `make check`; commit.**
  `git commit -m "work-carrier: refuse a capability source older than 48 hours"`

### Task 4: The bump proposer selects by `dependency_updates`

**Files:**
- Modify: `src/bump_proposer/cli.py`, `scripts/run-bump-proposer.sh`
- Modify: `tests/architecture/test_bridge_edges.py`, `tests/architecture/test_out_of_process_isolation.py`
- Test: `tests/bump_proposer/test_cli.py` (or the existing file covering `_lane`), launcher test

**Interfaces:**
- Consumes: `sds_members.client.read_members_from_environment`, `sds_members.populations.granted`.

- [ ] **Step 1: Write the failing tests.**
  - `test_the_population_is_granted_repositories_with_a_standing_package`: packages for A and B,
    record grants A and C; the pass considers only A, prints a `[NO PACKAGE]` line for C and a
    `[NOT GRANTED]` line for B, and neither line is a finding (exit 0 when A is clean).
  - `test_an_unreadable_member_record_stops_the_pass` (exit 2, nothing submitted, dry run and
    `--submit` both).
  - `test_naming_an_ungranted_repository_refuses` (the existing `requested` path refuses B with
    exit 2).
  - `test_the_grant_check_folds_case`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** in `_lane`: read members once, intersect the standing packages'
  repositories with `granted(members, "dependency_updates")` case-folded. The inert check and
  everything downstream are unchanged.
- [ ] **Step 4: Launcher.** Fetch the observer bearer on every run into `SDS_MEMBERS_OBSERVER_TOKEN`;
  keep exporting `ORCHESTRATOR_API_*` only with `--submit`, as today. Use one fetch for both.
- [ ] **Step 5: Edges and isolation.** The row gains `("GET", "/api/v1/members")`; its orchestrator
  predicate becomes the union with `sds_members.client`'s; `sds_members` joins its isolation row.
- [ ] **Step 6: `make check`, then commit.**
  `git commit -m "bump-proposer: select repositories granted dependency_updates"`

### Task 5: The change proposer selects by `rollouts`

**Files:**
- Modify: `src/change_proposer/cli.py`, `scripts/run-change-proposer.sh`
- Modify: `tests/architecture/test_bridge_edges.py`, `tests/architecture/test_out_of_process_isolation.py`,
  the `OUTBOUND_ALLOWLIST` reason for `change_proposer` in `test_wsp21_invariant_scan.py` if it
  describes the surface
- Test: `tests/change_proposer/test_change_proposer.py`, launcher test

**Interfaces:**
- Consumes: `read_members_from_environment`, `granted`.

- [ ] **Step 1: Write the failing tests.**
  - `test_scope_is_rollouts_grant_with_workflow_and_rollback`: a transcribed repository with a
    rollback but no `rollouts` grant is out of scope.
  - `test_an_unreadable_member_record_stops_the_pass` (exit 2, nothing proposed).
  - `test_naming_an_ungranted_repository_refuses` (through `_resolve_scope`, exit 2, message names
    the grant).
  - `test_the_scope_sweep_still_audits_every_transcription` (`_scope_sweep` is unchanged).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** `_in_scope(members)` keeps the `ROLLOUT_WORKFLOWS` keys that have a
  rollback and are granted `rollouts`, case-folded; `_resolve_scope` takes the members and its
  refusal message names all three conditions.
- [ ] **Step 4: Launcher.** Fetch the observer bearer on every run into `SDS_MEMBERS_OBSERVER_TOKEN`.
- [ ] **Step 5: Edges and isolation.** The row gains `("GET", "/api/v1/members")` and the observer
  bearer; its orchestrator predicate (now `None`) becomes `sds_members.client`'s; `sds_members`
  joins its isolation row. Fix the stale `tests/change_proposer/test_change_proposer.py:98` text the
  spec measured if it still describes the old surface.
- [ ] **Step 6: `make check`, then commit.**
  `git commit -m "change-proposer: select repositories granted rollouts"`

### Task 6: Prove against production, ship and deploy

- [ ] **Step 1: Compare populations.** From the worktree's venv, with production read credentials
  fetched in-process, run each lane's dry run (no acting flag) and compare with the main tree's last
  logged pass: bump proposer `AlobarQuest/infraops-mcp-server` only; change proposer brain and
  change-manager; the carrier judges every approved `work` record as its last pass did. Any
  difference is explained or stops the ship.
- [ ] **Step 2: Compare admission.** With the observer bearer, read every non-terminal work unit
  that has a `target_repository` and the member record, and list each unit whose admission term
  changes (old: the repository's `factory-target.toml` declares `target = true`; new: the grant for
  its change class). A unit that the old rule admitted and the new rule refuses stops the ship. A
  unit that becomes admissible is reported in the PR body.
- [ ] **Step 3: Pull request.** Push, open it with the rulings and both comparisons in the body,
  watch CI with `gh pr checks --watch --interval 30`, confirm `Quality`'s collected-test count, and
  merge. Loop until the state is `MERGED` before building on it.
- [ ] **Step 4: Deploy the orchestrator** by the procedure in `docs/operations/deploy.md` (its deploy
  command owns the build; there is no migration). Verify the served revision equals the merge
  commit.
- [ ] **Step 5: Install.** Pull the main tree (`git -C ~/Projects/orchestrator pull --ff-only`); the
  plists' arguments are unchanged, so no installer runs. Confirm the next pass of each lane in its
  log reads the record (its population line) and its Healthchecks check stays up.
- [ ] **Step 6: Clean up.** Main tree `git status` clean; remove the worktree, the
  `orchestrator_test_bridge_p2` database and the merged branch.
