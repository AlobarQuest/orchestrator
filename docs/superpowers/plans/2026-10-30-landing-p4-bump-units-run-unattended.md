# Landing layer, Part 4: a bump unit runs with no hand work — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a repository granted `dependency_updates` whose landing is inert, a Dependabot bump that fails its checks reaches `main` with no person acting. The SDS decomposes it, approves the decomposition by policy, dispatches, verifies and merges it, then watches it like any other landing.

**Architecture:**
- A new `bump-driver` lane, running as SYSTEM, moves bump work forward. It decomposes registered dependency-update intakes, dispatches ready bump units, and calls `pr-merge` on completed ones.
- It builds each decomposition with intent-packages' own generator against a checkout the lane owns, and never executes repository code: `factory decompose --no-execute`.
- The orchestrator gains two graduations, both scoped to standing dependency-update work in a granted repository:
  - a policy approval of the decomposition, beside the rotation one;
  - a merge admission that accepts a known-good recognition plus the grant in place of the exact-fingerprint human approval.
- The `rotation-verifier` lane widens to verify bump units. It files the repository's required checks as named-check evidence.
- Part 3a's watch widens to the factory's merges, so a factory-fixed bump that breaks `main` comes back out.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy and Alembic on Postgres, httpx, Typer. In intent-packages: Python, plus the `factory` CLI. launchd and Healthchecks for the lane.

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, "Part 4: A bump unit runs with no hand work". Decisions 1 and 2 are Devon's, from 2026-10-29.

## Findings this plan rests on

Recon ran on 2026-10-30 against `4fff3341`. It read the code, read production through the observer bearer and read-only SQL, and read GitHub. Nothing was written.

- **Volume.**
  - 4 of 88 Dependabot bumps on member repositories failed their checks in 30 days (4.5%). All four were in the orchestrator, across two distinct bumps.
  - orchestrator#506 (httpx2) and #508 (sqlalchemy) became records 170 and 171, and their intakes were registered on 2026-10-28. Since then nothing has moved them: each has no proposal and no unit.
  - Both need source changes: 11 pyright errors, and one failing CSRF test.
- **The hand steps.** Between a registered intake and a merge there are five:
  1. Decompose. No program does it; the rotation decomposer handles rotations only.
  2. Approve the decomposition. `decomposition.py::_policy_approval` exists for rotations, and returns None at `:288` for any revision approved by a `policy:` approver. Every standing bump revision is approved as `policy:dependency-update@vN`.
  3. Dispatch. No scheduled caller exists.
  4. Verify. `rotation_verifier/cli.py:38-51` selects only `constraints.rotation_unit`. A unit with no named-check evidence falls to `awaiting_review`, where a person completes it. The named-check POST needs a `dispatch_id`, which no VERIFIER read serves, and a job-name `check_name`, which nothing records.
  5. Merge. `pr_merge_admission.py:310,343` requires `Approval.id == unit.authority_approval_id` with the exact fingerprint. A unit dispatched on a known-good recognition has `authority_approval_id = NULL`, so it can never merge as the code stands.
- **The dispatch side already graduated.** `dispatch._blocked_reason` lets a known-good recognition stand in for the authority approval (`dispatch.py:206,228`). Every dependency-update known-good pattern already carries `github.pr.merge` (`factory-policy.toml:224,256,285,360,397`). The profile's single criterion is AC-001 `automated_check`, approver `policy`, with no `human_review`.
- **The generator only reads.** In intent-packages, pin-site discovery and `build_envelope` read files from a checkout. Only `dry_run_mutation` executes anything: it runs `uv add`, `uv lock`, `npm install` and `npm ci`, including npm lifecycle scripts. `conformance-claim` reads the project-standards compliance matrix.
- **The watch misses factory merges.** `revert_act._watched_landings` reads `EstatePrMerge` rows, the landers' own landings. The factory's merges are `UnitPrMerge` rows and are never watched.
- **Required checks are job names,** and some repositories have several. orchestrator requires `Quality` and `Runner consumer compatibility`. security-standards requires `Lint, type-check, and test` and `scan`.
- **Scope.** All 14 members grant `dependency_updates`. No standing package targets a deploying repository (brain, change-manager). A factory pull request in a deploying repository is filed as `factory-delivery`, which is outside deploy policy, so its change record waits for a person. Deploying repositories are out of this part's scope, as the following section says.

## Decisions

- **Devon, 2026-10-30:** the decomposition is built in-process. The SDS never runs `factory decompose`'s dry run on the operator machine, because that installs the bumped dependency, lifecycle scripts included, where infrastructure credentials live. Cost accepted: there is no local proof. A bad envelope fails its attempts, the bump stays held, and an alert fires.
- **Builder's calls** (the spec leaves each to the builder, or recon settled it):
  - One `bump-driver` lane does decompose, dispatch and merge as SYSTEM. Verification stays with the VERIFIER lane, so the two identities stay separate. This mirrors the rotation lanes. The spec put dispatch and merge in "the lander"; a separate lane keeps the landers single-purpose.
  - The policy approver recorded is the proposing SYSTEM identity, as the rotation path records it. No new registry identity is created.
  - The binding check (ADR-0028 amendment 1) lives orchestrator-side, at the policy approval. It parses each mutation command per tooling. intent-packages already emits matching values, so the binding needs no intent-packages authority release.
  - The scheduled `pr-merge` gets an off switch, `ORCHESTRATOR_BUMP_MERGE_ENABLED`, which defaults to true and gates only the policy path. Human-approved merges ignore it.
  - Inert repositories only. Deploying repositories need a change-manager class for factory bump pull requests, and none has needed factory bump work. That waits until one does.

## Global constraints

- **Worktrees.** Work in `.worktrees/landing-p4`, with its own `.venv` and database `orchestrator_test_landing_p4`. intent-packages work goes in its own worktree. Never edit, check out or run `uv` in a main tree; the scheduled lanes run the main tree's working copy.
- **Secrets.** Never put a secret in a prompt, tool argument or transcript. Every parsed `bws` call passes `--color no`. Fetch credentials in-process, using `scripts/sds-bws.sh` and `scripts/sds-token.sh`.
- **Word guards** (ADR-0051) apply under `src/orchestrator/`. `dispatch`, `deploy`, `coolify`, `workflow_dispatch`, `factory-runner`, `auto_merge`, `merge_pull_request` and the rest may not appear in code identifiers or bare strings there. Reword; don't allowlist.
- **Route inventories.** A new route joins the POST/GET inventories in `test_scope_guards.py`, and an ingress POST joins `tests/idempotency/test_matrix.py`. A changed `response_model` is extended in the same change as its service.
- **Lane clients.** A lane's clients are POST-only for writes, with an exact path allowlist, and are pinned in `tests/architecture/test_landing_edges.py` or the lane's own surface test. A file that imports an HTTP client is listed in `OUTBOUND_ALLOWLIST`.
- **Launchers.** A LaunchAgent installer sources `scripts/sds-install.sh` and calls `sds_refuse_linked_worktree`. A producer that shells out to `uv` resolves it with `shutil.which`, falling back to `~/.local/bin`.
- **Holds.** Every path that lands or proposes a bump honours the member record's holds through the shared matcher, classified in `tests/architecture/test_hold_paths.py`.
- **Authority.** Never add `automated_test` or `external_attestation` to a deterministic set. Envelopes are write-once.
- **Gate.** `make check` must be green before each commit that touches logic. Mutation-test every new guard and report the count; clear `__pycache__` around each mutate and restore.

## Review focus

1. **A policy approval that reaches past bumps.** A revision whose profile isn't `dependency-update`, isn't `standing`, or was approved by a person must never be policy-approved by the bump branch. Nor may a target repository without the grant, or an envelope with any capability or command outside a dependency-update known-good pattern. Pinned in Task 2.
2. **A mutation that bumps something else.** An envelope whose `mutation_commands` name another dependency, another version or another repository is refused at the policy approval. That covers a different `uv add` target, a `sed` that rewrites another pin, an `npm install` of a second package, and a scoped npm name. Pinned in Task 2.
3. **A merge path that widens.** The recognition-plus-grant merge must refuse any other change class, an ungranted repository, a unit whose envelope a known-good pattern no longer recognises (a policy narrowed since dispatch), a unit with an open reconciliation condition, and any unit while the off switch is off. A human-approved merge must keep working with the switch off. Pinned in Task 3.
4. **A verifier that asserts what it didn't observe.** The verifier lane files named-check evidence only for the repository's required checks. A check that hasn't concluded is "not yet" (wait), not a failure. A repository with no required checks is refused and alerted, never verified. Pinned in Task 4.
5. **A factory merge that escapes the watch.** A factory-merged bump whose push run fails on `main` gets the same rerun, revert, hold and never-again path as a Dependabot landing. The hold uses the unit's `profile_fields` dependency and `from_version`, not a Dependabot block. Pinned in Task 6.

---

### Task 0: Measure before building

Read-only. The result goes to `docs/superpowers/plans/2026-10-30-landing-p4-measurements.md`, committed.

- [ ] **Step 1:** Read `services/lifecycle/verifier_evaluators.py` and record how an `automated_check` criterion is decided when it has several named-check evidence rows: does every row count, the latest, or any? Task 4 files one row per required check. If the evaluator doesn't require every filed row to succeed, Task 4 Step 6 makes it do so.
- [ ] **Step 2:** For each member, read App Brain's `default-branch-landing` value and the required status checks on `main`. Record the inert members, and any member with zero required checks.
- [ ] **Step 3:** In an intent-packages worktree, run the generator for records 170 and 171 against a fresh clone at `origin/main`, with the dry run skipped and `--out`. Do this by hand, before Task 1: call `build_proposal` from a Python shell. Check that the envelopes `_recognises` would accept them under factory-policy schema 9 (run `factory_policy.known_good_recognition` locally). This is the replay that proves Task 2's predicate admits real bumps.
- [ ] **Step 4:** Record where `CODE_BRAIN_KEY` and `INFRA_BRAIN_KEY` live in BWS (names and UUIDs only), and which identity can read them.
- [ ] **Step 5:** Commit the measurements file.

### Task 1: `factory decompose --no-execute` (intent-packages)

**Files:**
- Modify: `intent-packages/src/intent_packages/factory/decompose.py` (`run`) and its Typer command
- Test: `intent-packages/tests/factory/test_decompose.py`

**Interfaces:**
- Produces: `factory decompose ... --no-execute --out <file>`. It writes the proposal JSON and never calls `dry_run_mutation` or `assert_pin_sites_moved`. `--no-execute` together with `--submit` is refused with exit 1 and the message `--no-execute writes a proposal for its caller to post; it never submits`.

- [ ] **Step 1:** Write `test_no_execute_writes_the_proposal_without_running_any_command`. With a dry-run double that fails if it's called, it asserts the output file exists and its envelope equals the one an executing run produces for the same tree.
- [ ] **Step 2:** Write `test_no_execute_refuses_submit`: exit 1 and the message.
- [ ] **Step 3:** Run both; they fail.
- [ ] **Step 4:** Implement the flag. The rationale string gains ` built without a local run (no-execute).` so the proposal says what it skipped.
- [ ] **Step 5:** Both tests pass, and intent-packages' `make check` is green. Open the PR. It lands before Task 5 ships.

### Task 2: A bump decomposition is approved by policy

**Files:**
- Create: `src/orchestrator/services/intake/bump_binding.py`
- Modify: `src/orchestrator/services/intake/decomposition.py` (`_policy_approval`, `:272-330`)
- Test: `tests/services/test_bump_decomposition_policy.py`, `tests/services/test_bump_binding.py`
- Docs: ADR-0006 amendment (human gates graduate for standing dependency-update decompositions in granted repositories; basis: Devon's decision 1)

**Interfaces:**
- Produces: `bump_binding_violations(envelope: Mapping[str, Any], profile_fields: Mapping[str, Any]) -> tuple[str, ...]`. It returns the empty tuple when every `mutation_commands` entry is a mutation of exactly `profile_fields.package` to exactly `profile_fields.to_version`, in the tooling that the package's ecosystem names. The tooling's own dependency-free commands are allowed beside it: `uv lock`, or `uv lock --frozen` where the generator emits it. `constraints.target_repository` must also equal `profile_fields.target_repo`, case-folded.
- Produces: in `_policy_approval`, a second branch, `_bump_policy_approval(...)`, that returns the same type the rotation branch returns.

The bump branch approves only when all of these hold:
- the actor is SYSTEM;
- `revision.approved_by` starts with `policy:dependency-update@`;
- the revision profile is `dependency-update` and `profile_fields.standing is True`;
- there is exactly one proposed unit, with no dependencies;
- `max_attempts` is at or below the envelope budget;
- `human_authority_gate` reports no refusals, and `recognised_by` names only dependency-update known-good patterns;
- `MemberRecordGrants(session).is_granted(target, "dependency_updates")`;
- `bump_binding_violations` is empty.

Each refusal returns None, so a person decides on `/review` as today. Each refusal also records its reason in the proposal's events under the code `decomposition.policy_not_applied`.

- [ ] **Step 1:** In `test_bump_binding.py`, write one test per tooling where the envelope from Task 0's replay passes. Then write failing cases:
  - uv: `uv add 'other>=1'`.
  - pip: a `sed` whose pattern names another package.
  - npm: `npm install a@1 b@2`.
  - npm: a scoped name mismatch.
  - any: the version off by a patch.
  - any: the target repository differs.
  - any: an empty `mutation_commands`.
- [ ] **Step 2:** In `test_bump_decomposition_policy.py`, write `test_a_standing_bump_in_a_granted_repository_is_approved_by_policy`. Then one refusal test per condition in the list, each asserting `decomposition_approved_by is None` and the event code. Finally, `test_the_rotation_branch_is_unchanged`, which reruns the existing rotation policy tests unchanged.
- [ ] **Step 3:** Run; they fail.
- [ ] **Step 4:** Implement both functions. Update the `_policy_approval` docstring: it no longer names the rotation decomposer as the only SYSTEM proposer.
- [ ] **Step 5:** The tests pass. Run a mutation pass over every condition and the binding parser, then run `make check`.
- [ ] **Step 6:** Write the ADR-0006 amendment. In CLAUDE.md, rewrite two invariants. The one on rotation's one click must state both policy branches. The write-once invariant ("dry-run every unit's commands against the real target repo") must say that a standing bump's decomposition is built without a local run, by Devon's decision of 2026-10-30, and that a session-authored decomposition still dry-runs. Commit.

### Task 3: A bump merges on its recognition and its grant

**Files:**
- Modify: `src/orchestrator/services/landing/pr_merge_admission.py` (the approval term, `:300-345`)
- Modify: `src/orchestrator/config.py` (add `bump_merge_enabled`, env `ORCHESTRATOR_BUMP_MERGE_ENABLED`, default true, parsed as the other lane switches are)
- Modify: `src/orchestrator/services/landing/pr_merge.py` (module docstring: the no-off-switch ruling is void for the policy path; name the switch)
- Test: `tests/services/test_pr_merge_bump_policy.py`
- Docs: ADR-0025 amendment 3 (the amendment-2 gates graduate for standing dependency-update units in granted inert repositories; basis: decision 1)

**Interfaces:**
- Produces: the approval term accepts, in place of the bound human approval, a unit where all of these hold:
  - `authority.change_class == "dependency-update"`;
  - the revision profile is `dependency-update` and `standing`;
  - `human_authority_gate(unit, revision)` reports no refusals, and `recognised_by` names only dependency-update patterns, judged at merge time against the policy as loaded now;
  - the `dependency_updates` grant holds;
  - `settings.bump_merge_enabled` is true.
- Produces: the term records the event `authority.human_gate_not_required` with `{"at": "merge", "recognised_by": [...]}`, as dispatch does.
- Produces: refusal codes. `merge_policy_path_off` when the switch is off. When a human approval is absent, `authority_approval_not_bound` keeps its meaning. A refusal must not be a substring of another finding kind.

- [ ] **Step 1:** Write `test_a_recognised_bump_in_a_granted_repository_merges_without_an_approval`.
- [ ] **Step 2:** Write the refusals:
  - another change class;
  - not `standing`;
  - an ungranted repository;
  - a policy that no longer recognises the envelope (load a narrowed policy fixture);
  - an open reconciliation condition, where the existing term still refuses;
  - the switch off, which refuses `merge_policy_path_off`;
  - `test_a_human_approved_merge_ignores_the_switch`.
- [ ] **Step 3:** Run; they fail.
- [ ] **Step 4:** Implement. The term now reads "a bound approval, or the policy path". Every other term is unchanged.
- [ ] **Step 5:** The tests pass. Run a mutation pass over the new branch and `make check`.
- [ ] **Step 6:** Write the ADR-0025 amendment and rewrite the CLAUDE.md invariant that describes pr-merge's approval. Commit.

### Task 4: The verifier verifies bump units

**Files:**
- Modify: `src/orchestrator/api/schemas/lifecycle.py` and the `GET /work-units/{id}` service. `WorkUnitDetailResponse` gains `latest_dispatch_id: UUID | None`, the newest dispatch row for the unit, readable by every role that can read the unit.
- Modify: `src/rotation_verifier/cli.py`, `src/rotation_verifier/client.py`
- Create: `src/rotation_verifier/required_checks.py`
- Test: `tests/rotation_verifier/test_bump_units.py`, `tests/rotation_verifier/test_client_surface.py`, `tests/api/test_work_unit_detail.py`

**Interfaces:**
- Produces: `required_checks(repository: str, *, token: str) -> tuple[str, ...]`. It reads the `contexts` of `GET /repos/{repository}/branches/main/protection/required_status_checks`, sorted. It raises `RequiredChecksUnreadable` on any non-200, and returns an empty tuple only on a 200 with no contexts.
- Produces: the lane selects units in `submitted` or `verifying` whose `authority.change_class == "dependency-update"`, beside rotation units. For each bump unit, it files one `POST /work-units/{id}/verifier-evidence/named-check` per required check, using `latest_dispatch_id`, the bound PR and the armed head, then calls `/verify`. A 409 `named_check_not_concluded` is `waiting`, which isn't a finding. Zero required checks is the finding `bump_unit_no_required_check`, and the unit isn't verified.
- Produces: the client allowlist adds exactly that POST path.

- [ ] **Step 1:** Write the detail-route test: `latest_dispatch_id` is served, the newest of two dispatch rows wins, and a unit never dispatched serves null.
- [ ] **Step 2:** Write `test_required_checks_*`: sorted contexts, 404 raises, and an empty 200 returns `()`.
- [ ] **Step 3:** Write lane tests:
  - a bump unit files each required check, then verifies;
  - a not-concluded check waits and doesn't verify;
  - zero required checks is the finding;
  - rotation units behave exactly as before;
  - the surface test pins the new path, and refuses every other.
- [ ] **Step 4:** Run; they fail.
- [ ] **Step 5:** Implement. The lane takes its GitHub token from `gh auth token`, as the landers do.
- [ ] **Step 6:** If Task 0 Step 1 found the evaluator doesn't require every filed row: make `automated_check` decide `met` only when every named-check row filed for the criterion at the armed head concluded success. Add `test_one_failed_required_check_fails_the_criterion`.
- [ ] **Step 7:** The tests pass. Run a mutation pass and `make check`, then commit.

### Task 5: The bump-driver lane

**Files:**
- Create: `src/bump_driver/__init__.py`, `cli.py`, `client.py`, `mirror.py`, `decompose.py`
- Create: `scripts/run-bump-driver.sh`, `scripts/install-bump-driver-launchd.sh`, `scripts/com.devon.bump-driver.plist`
- Modify: `pyproject.toml` (console script `bump-driver`), `layers.toml`, `.cred-consumers.toml` (SYSTEM bearer consumer), `OUTBOUND_ALLOWLIST`
- Test: `tests/bump_driver/test_pass.py`, `tests/bump_driver/test_client_surface.py`, `tests/bump_driver/test_mirror.py`, `tests/scripts/test_launchd_installers.py`

**Interfaces:**
- Consumes: Task 1's `factory decompose --no-execute --out`, Task 2's policy approval, Task 3's merge path and Task 4's verification.
- Produces: one pass, `run_pass(...) -> list[Outcome]`, in this order:
  1. **Decompose.** For each approved change-manager `work` record whose package is a standing dependency-update package, the lane reads the record's intake. It skips any revision that already has a unit or proposal; one a person rejected, sent back or superseded is never re-proposed (the rotation decomposer's `_existing`). Then it calls `mirror.fresh(repository) -> Path`, a shallow clone at `origin/main` under `~/Library/Application Support/sds/bump-driver/mirrors/<owner>/<repo>`, fetched and `git reset --hard origin/main`, with no hooks and no install. It runs `factory decompose --no-execute --out <tmp>` with the revision's `profile_fields`, and posts the proposal through its own confined client with idempotency key `bump-driver:{revision}`.
  2. **Dispatch** each `ready` unit whose change class is `dependency-update`, with idempotency key `bump-driver:dispatch:{unit}:{version}`. This skips any unit with a live run, one run per repository at a time. `dispatch_disabled` is `waiting`.
  3. **Merge** each `completed` dependency-update unit with a bound, open PR through `POST /work-units/{id}/pr-merge`. `merge_policy_path_off` is `waiting`; any other refusal is reported.
- Produces: keyed alerts through `lander.held_alerts`'s pattern (sender `sds-bridge`):
  - `bump-unit-failed:{repo}:{unit}` when a unit ends `failed`;
  - `bump-decompose-refused:{repo}:{revision}` when the generator refuses or the proposal isn't policy-approved.
  Each resolves when its subject clears.
- Produces: the launcher is an outcome alarm (`sds_outcome_begin`/`sds_outcome_end`) with Healthchecks check `sds-bump-driver`, hourly at :50. It reads the SYSTEM bearer through the narrow identity, the change-manager READ bearer through the broad one, and the brain keys Task 0 located, each into its own variable.

- [ ] **Step 1:** Write the pass tests with doubles:
  - a registered intake is decomposed and posted once;
  - a rejected proposal is never re-proposed;
  - a ready bump unit is dispatched once per version;
  - a non-bump unit is never touched;
  - a completed unit is merged;
  - the off switch waits;
  - a failed unit alerts once, and the alert resolves when the unit is cancelled.
- [ ] **Step 2:** Write the mirror tests. A dirty mirror is reset. `fresh` runs only `git` (assert the subprocess argv list). A repository outside the member record is refused.
- [ ] **Step 3:** Write the surface test: exactly the GETs and POSTs the pass makes, and every other path refused.
- [ ] **Step 4:** Run; they fail.
- [ ] **Step 5:** Implement. `factory` is resolved with `shutil.which`, falling back to `~/.local/bin`, as `uv` is.
- [ ] **Step 6:** The tests pass. Run a mutation pass over selection and refusal classification, then `make check`. Commit.

### Task 6: The watch covers the factory's merges

**Files:**
- Modify: `src/orchestrator/services/landing/revert_act.py` (`_watched_landings` and the landing type it yields), `revert_build.py`, `revert_hold.py` call site, `terms.py` (never-again)
- Test: `tests/services/test_revert_factory_merges.py`, `tests/architecture/test_hold_paths.py`

**Interfaces:**
- Produces: the watch yields, beside `EstatePrMerge` landings, each `UnitPrMerge` of a dependency-update unit in a repository whose landing is inert. Its landing facts are the merge commit, the PR number and the repository. The dependency facts come from the unit revision's `profile_fields`: `package` and `from_version`, with the ecosystem from `ecosystem_of_package_id`. The rerun, revert, hold and never-again path is Part 3a's, unchanged. The revert lands through the inert lane.
- Produces: the hold is placed by `revert_hold.place` at `from_version`, and the never-again term refuses `to_version` for good, exactly as for a Dependabot landing.

- [ ] **Step 1:** Write `test_a_factory_merged_bump_that_fails_on_main_is_reverted_and_held`, end to end with gateway doubles: merged, run failed, rerun failed, revert opened, revert landed, hold, alert.
- [ ] **Step 2:** Write `test_a_factory_merge_of_another_change_class_is_not_watched`, and `test_a_factory_merge_in_a_deploying_repository_is_not_watched`.
- [ ] **Step 3:** Run; they fail.
- [ ] **Step 4:** Implement, and classify the factory-merge revert path in `test_hold_paths.py`.
- [ ] **Step 5:** The tests pass. Run a mutation pass and `make check`. Commit.

### Task 7: Ship and prove

- [ ] **Step 1:** Run the final whole-branch review, and fix what it finds.
- [ ] **Step 2:** Merge intent-packages Task 1 and pull its main tree.
- [ ] **Step 3:** Merge the orchestrator and deploy it with `scripts/deploy_orchestrator.py`. Expect no migration unless Task 4's field needs one. Confirm that `openapi.json` serves `latest_dispatch_id`.
- [ ] **Step 4:** Install the `bump-driver` LaunchAgent and register the `sds-bump-driver` Healthchecks check, hourly. Reinstall the verifier lane if its plist changed.
- [ ] **Step 5: The live proof is the real backlog.** Records 170 and 171 (orchestrator#506 and #508) run end to end with no person: proposed, policy-approved, dispatched, verified, merged and watched. Record each step's time and actor in the measurements file. If one fails its attempts, the proof is the `bump-unit-failed` alert, and the bump stays held.
- [ ] **Step 6:** Clean up the worktrees, databases and branches.

## Out of scope

- Deploying repositories (brain, change-manager). They need a change-manager class for factory bump pull requests and a standing package; none exists yet.
- Part 5 (records stay true).
- The settled-landing change to the watch (Devon, 2026-10-30: leave it for now).
