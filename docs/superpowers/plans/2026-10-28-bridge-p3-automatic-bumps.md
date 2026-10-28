# Bridge layer, Part 3: automatic dependency updates up to intake — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed Dependabot bump in an inert repository granted `dependency_updates` becomes a
registered intake, with a policy-approved record and a known-good envelope, with no human click; a
bump the factory has no tooling for, or a unit that fails, sends one keyed alert.

**Architecture:** Three repositories change and ship in a fixed order. change-manager approves a
`work` record by a pinned policy when the record is a standing dependency-update package for a
repository on its own inert list. intent-packages gains a command that creates an approvable standing
package, reads its dependency-update grant's targets from the SDS member record, and puts merging in
scope. The orchestrator's known-good patterns cover npm, pip and uv dependency-update envelopes in
any repository (admission's grant term from Part 2 bounds the repository), the bump proposer creates
packages and sends keyed alerts, and the watcher alerts on a failed unit. Both alert senders share
one alert-hub client extracted from the rotation worker.

**Tech Stack:** Python 3 in all three repositories; FastAPI and Alembic (change-manager, Postgres in
production, migrated on boot by `entrypoint.sh`); httpx; pytest; Bash launchers; alert-hub's
`POST /v1/alerts`.

**Spec:** `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`, Part 3 (with "Decisions Devon
made", "Anti-goals", "Constraints", "Order"). Part 2 shipped as #537.

## Global constraints

- **Worktrees, one per repository, never the main checkouts.** The scheduled lanes run the
  orchestrator main tree, and the bump proposer commits and pushes from `~/Projects/intent-packages`
  itself, so neither is ever edited. Orchestrator: `~/Projects/orchestrator/.worktrees/bridge-p3`
  (own `.venv`, own `orchestrator_test_bridge_p3` database). intent-packages and change-manager:
  `~/Projects/<repo>/.worktrees/bridge-p3` (`.worktrees/` is gitignored in both), each with its own
  `uv sync --frozen` venv and that repository's own test command from its `Makefile`/`CLAUDE.md`.
- Never put a secret in a prompt, tool argument or transcript. Fetch credentials inside one process;
  every parsed `bws` call passes `--color no`.
- Don't poll GitHub without a pause (`--watch --interval 30`).
- Scope is inert repositories granted `dependency_updates`. The proposer's `not-declared-inert`
  refusal for brain and change-manager stays until Part 4 (holds) ships; nothing in this plan adds a
  `live_estate` pattern, a `live_estate` grant reach or a deploying-repository package.
- No created package carries a `human_review` criterion, the "merging is left for a person" text,
  or a reach other than `[source_repository]`.
- One alert per condition: a sender fires a key once, remembers it in a state file, sends `resolved`
  when the condition clears, and fires again only after that. It handles alert-hub's 429.
- Standing-authority changes (the grant, known-good patterns, policy approval of `work` records) are
  releases; the dependency-update budget stays equal in its five places
  (`scripts/check_profile_budget_agreement.py`).
- In change-manager, new `WorkChangeIn` fields are passed as explicit keyword arguments and never
  join `_ASSERTED_FIELDS` (a field added to an asserted tuple 409s on every stored row).
- Every new orchestrator file gets a `layers.toml` row; new outbound modules join
  `OUTBOUND_ALLOWLIST`; new edges join `tests/architecture/test_bridge_edges.py`.
- Each repository's full gate is green before each commit that touches logic (orchestrator:
  `make check`).

## Rulings made while planning

1. **Part 3 covers inert repositories only.** The spec orders holds (Part 4) before automatic bumps
   reach repositories that deploy. Keeping brain and change-manager out removes every `live_estate`
   change (a `live_estate` known-good pattern, a per-repository grant reach, the change window).
   Cost if wrong: two repositories wait for Part 4.
2. **change-manager keys policy approval of `work` records on its own inert list, not the grant.**
   It can't read the member record; `membership_check` already holds every inert repository granted
   `dependency_updates`, and admission (Part 2) checks the grant again. The source-level property (c)
   stays on `POLICY_APPROVED_SOURCES`; `work` gets a per-record policy, because a source-wide one
   would make rotation and app-change records unapprovable by anyone (the recorded reason in
   `app/sources.py`). Cost if wrong: a record for an inert repository whose grant was withdrawn is
   approved, then refused at admission.
3. **intent-packages reads the grant's targets from the member record** (grant schema 2), through its
   existing `factory/credentials.py` seam: `SDS_MEMBERS_OBSERVER_TOKEN` first, then BWS by the
   observer UUID in its `.bws-secrets.toml`. It fails closed when unreadable. Cost if wrong: policy
   approval depends on the orchestrator being reachable, which the proposer already needs.
4. **Known-good schema 9 makes `target_repositories` optional.** Absence means "no repository
   objection from this pattern"; an empty list stays invalid. The dependency-update patterns omit it
   and rely on admission's grant term. Cost if wrong: an authority approval stops being asked for a
   dependency-update envelope in an ungranted repository, which admission then refuses anyway.
5. **One key grammar for alerts:** `dependency-bump:<repository lowercased>:<ecosystem>:<dependency>`
   for both senders, under one new alert-hub sender, `sds-bridge`. The watcher derives the key from
   the record's package (`target_repo`, `package` and the ecosystem in the package id). Cost if wrong:
   a key rename re-fires every open alert once.
6. **Passing Docker bumps alert too.** The cascade excludes Docker and nothing else handles them, so
   an alert is the only way they aren't silent. Cost if wrong: one alert per open Docker bump.
7. **Requirement-range bumps get a separate parse in the proposer.** The title regex is pinned to
   the estate lander's, which must not start admitting ranges. A range bump in pip goes to the alert
   path, because pip's mutation command matches `==` lines only. Cost if wrong: pip ranges alert
   instead of being fixed.
8. **A failed unit alerts from the watcher**, keyed as in Ruling 5, and resolves when the record is
   retired or superseded. A failed unit stays failed until a person retries or cancels it, so the
   alert is true while it lasts. Cost if wrong: an alert for a unit someone is about to retry.
9. **Registering the `sds-bridge` sender is your one task.** alert-hub shows a sender's token once,
   on its dashboard, and stores only a hash. The generated task stores it in BWS from the clipboard
   and prints only the secret's id. Cost if wrong: none; there's no machine path.

## Review focus

1. **An alert after you close it.** A daily lane must not open a new alert every morning for a
   condition you closed. Pinned in Task 6.
2. **One dependency in two ecosystems in one repository.** `discover()` keys on repository and
   dependency and raises on a duplicate, so the proposer must not create a second package for the
   same pair; it alerts instead. Pinned in Task 7.
3. **The member record unreadable during approval.** `approve --by-policy` must refuse, and the
   proposer reports the package as refused, never approves. Pinned in Task 3.
4. **A `work` record that isn't a standing dependency-update package for an inert repository**
   (rotation, app change, a deploying repository, missing fields) stays pending for a person.
   Pinned in Task 1.
5. **A requirement-range bump in pip** must reach the alert path, never a package whose mutation
   command can't apply. Pinned in Task 7.

---

### Task 1: change-manager approves standing dependency-update `work` records by policy

Repository: change-manager (`~/Projects/change-manager/.worktrees/bridge-p3`).

**Files:**
- Modify: `app/schemas.py` (`WorkChangeIn`), `app/work_changes.py`, `app/models.py`
  (`human_may_approve`), `app/sources.py` (docstring), `app/api.py` (serve the new fields)
- Create: `app/work_policy.py`; an Alembic migration adding nullable `target_repository` and
  `change_class` columns to the item table
- Test: `tests/test_work_changes.py`, `tests/test_work_policy.py`

**Interfaces:**
- Produces: `WorkChangeIn` optional `target_repository: str | None`, `change_class: str | None`.
- Produces: `work_policy.objections(item) -> list[str]` (empty means approve) and
  `WORK_POLICY_ACTOR = "work-policy"`. A record conforms when all hold: `change_class ==
  "dependency-update"`; `target_repository.lower()` is in the current deploy policy's
  `inert_landing.repositories`; `package_source_repository` is intent-packages; `risk == "caution"`.
- The proposer (Task 7) sends both fields on every proposal.

- [ ] **Step 1: Write the failing tests.** `test_a_conforming_work_record_is_approved_by_policy`
  (status `approved`, `decided_by == "work-policy"`, an `approved` event naming the policy);
  parametrized `test_a_nonconforming_work_record_stays_pending` (other class, deploying repository,
  missing fields, other risk, other package source); `test_a_replay_with_the_fields_approves_a_pending_record`;
  `test_the_new_fields_are_not_asserted` (a replay that changes only them doesn't 409);
  `test_a_person_may_still_approve_a_nonconforming_work_record`; `test_rotation_records_are_unaffected`.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.** Policy approval runs inside `propose_work_change`'s transaction, for a
  new record and for a pending one on replay; it never revokes. Keep the source-set test's
  assertions true; rewrite the `sources.py` docstring to state the per-record rule. Write the
  migration with a `downgrade()` and run it up and down against Postgres, not only SQLite.
- [ ] **Step 4: Run them to verify they pass; run the repository's full gate; commit.**

### Task 2: intent-packages creates an approvable standing package

Repository: intent-packages (`~/Projects/intent-packages/.worktrees/bridge-p3`).

**Files:**
- Modify: `src/intent_packages/cli.py` (new `create-standing` subcommand under `python -m
  intent_packages`), `src/intent_packages/profiles/dependency_update.py` (the standing template)
- Test: `tests/test_create_standing.py`

**Interfaces:**
- Produces: `python -m intent_packages create-standing --target-repo <owner/name> --package <dep>
  --ecosystem <npm|pip|uv> --from <v> --to <v> [--out packages]` → writes
  `packages/<name>-<ecosystem>-<dep-slug>/package.yaml` and `lineage.yaml` (status `draft`,
  revision 1, hash from `canonical.package_hash`), prints the package path, and refuses when the
  directory exists. The package: `standing: true`; `reach: [source_repository]`; AC-001 only,
  `automated_check`, `approver: policy`; budgets from `BUDGETS`; authority matching the grant after
  Task 4; `risk.max_impact: low`; scope and done text that include merging.
- Consumed by Task 7, which then runs the existing `transition --to ready_for_review` and
  `approve --by-policy`.

- [ ] **Step 1: Write the failing tests.** `test_create_standing_writes_a_package_that_validates`;
  `test_the_created_package_is_approvable_by_policy` (run approve-by-policy against it with a member
  double granting the repository); `test_create_standing_refuses_an_existing_directory`;
  `test_create_standing_refuses_an_ecosystem_without_tooling`;
  `test_the_created_package_has_no_human_review_criterion`.
- [ ] **Step 2: Run to verify they fail. Step 3: Implement. Step 4: Pass, full gate, commit.**

### Task 3: intent-packages reads the grant's targets from the member record

Repository: intent-packages.

**Files:**
- Modify: `src/intent_packages/approval_policy.py` (schema version 2, `targets_from`),
  `approval-policy.toml`, `src/intent_packages/factory/credentials.py` (observer token),
  `.bws-secrets.toml` (observer UUID `f793576f-e9aa-4f9d-8089-b4a000b9e2d5`)
- Create: `src/intent_packages/members.py` (a confined GET of `/api/v1/members`, injectable
  transport, named User-Agent)
- Test: `tests/test_approval_policy.py`, `tests/test_members.py`, a contract test pinning the three
  fields it reads (`members[].repository`, `members[].state`, `members[].grants[<service>].state`)

**Interfaces:**
- The dependency-update grant replaces `target_repositories = [...]` with `targets_from =
  "sds-member-record:dependency_updates"`. A target qualifies when a non-retired member's
  `dependency_updates` grant is `granted`, compared case-folded. Schema 1 grants (other profiles)
  are unchanged.
- An unreadable record (no token, transport error, non-2xx, bad shape) is a refusal naming the
  cause, never an approval.

- [ ] **Step 1: Write the failing tests.** `test_a_granted_target_is_approved`,
  `test_an_ungranted_target_is_refused`, `test_the_target_check_folds_case`,
  `test_an_unreadable_member_record_refuses`, `test_a_retired_member_is_refused`,
  `test_schema_1_grants_still_load`; the contract test against the orchestrator's members response
  model fields.
- [ ] **Step 2–4:** fail, implement, pass, full gate, commit.

### Task 4: intent-packages puts merging in scope

Repository: intent-packages.

**Files:**
- Modify: `src/intent_packages/profiles/dependency_update.py` (`CAPABILITIES` gains
  `"github.pr.merge": "allowed"`), `approval-policy.toml` (`merge_to_main` moves from
  `authority_requires_approval` to `authority_allowed`), the three
  `packages/infraops-mcp-server-npm-*` packages (authority and the merging text, revised through the
  lifecycle so each lineage records the change), `tests/fixtures/package_hashes.json`
- Test: the profile's and the policy's existing tests, updated

- [ ] **Step 1:** update the tests to expect `github.pr.merge` in decomposed envelopes and
  `merge_to_main` allowed; run them to fail.
- [ ] **Step 2:** change the profile and the grant; revise each standing package (`revise`, edit,
  `transition --to ready_for_review`, `approve --by-policy` with a member double or the real
  record), keeping their versions; refresh the hash fixture.
- [ ] **Step 3:** run the full gate; commit. The intent-packages pull request carries Tasks 2 to 4.

### Task 5: Known-good patterns cover npm, pip and uv in any repository

Repository: orchestrator.

**Files:**
- Modify: `src/orchestrator/factory_policy.py` (`SUPPORTED_SCHEMA_VERSIONS = {9}`,
  `target_repositories` optional), `src/orchestrator/factory-policy.toml` (version 9; the uv pattern
  loses `target_repositories`; add npm and pip patterns; all three carry `github.pr.merge`),
  `scripts/check_profile_budget_agreement.py` (every `dependency-update` pattern, found by
  `change_class`, not by one name), `tests/services/test_authority_known_good.py`
  (`PROFILE_CAPABILITIES`), `tests/contract/test_profile_budget_agreement.py`
- Test: `tests/services/test_factory_policy.py`, `tests/services/test_authority_known_good.py`

**Interfaces:**
- The npm pattern's `command_prefixes` are exactly the commands the profile generates for npm
  (`npm install`, `npm run build`, `npm ci`, `npx eslint`, `npx prettier`, `npm test`, `grep -q`);
  pip's are `sed -i` and `grep -qx`. Read them from `dependency_update.py`, never by hand.
- [ ] **Step 1: Write the failing tests.** `test_a_pattern_without_target_repositories_recognises_any_repository`;
  `test_an_empty_target_repositories_list_is_still_invalid`; one envelope per tooling, generated by
  intent-packages' `build_envelope` for an inert repository, is recognised;
  `test_an_envelope_with_an_unlisted_command_is_not_recognised`; the budget script fails when any
  dependency-update pattern's capabilities differ from the profile's.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 6: One alert-hub client for the lanes

Repository: orchestrator.

**Files:**
- Create: `src/alert_hub_client/__init__.py` (a library row in the isolation table), extracted from
  `src/rotation_worker/notify.py`
- Modify: `src/rotation_worker/notify.py` (uses it; behaviour unchanged), `.bws-secrets.toml` (the
  `sds-bridge` sender token's UUID, added once Devon's task gives it)
- Test: `tests/alert_hub_client/test_client.py`; the rotation worker's notify tests stay green

**Interfaces:**
- Produces: `class KeyedAlerts(token: Callable[[], str], state_path: Path)` with
  `sync(firing: Mapping[str, Alert]) -> None`, where `Alert(title, message, link)` and keys follow
  the alert-hub key grammar. `sync` fires each new key once, sends `resolved` for each remembered key
  no longer firing, persists the remembered set atomically, and retries a 429 once after its
  `Retry-After` before raising. An unwritable state file is an error, never a silent re-fire.

- [ ] **Step 1: Write the failing tests.** `test_a_key_fires_once_across_passes`;
  `test_a_cleared_key_is_resolved_and_forgotten`; `test_a_key_fires_again_after_it_resolved`;
  `test_a_429_is_retried_after_retry_after`; `test_an_unwritable_state_file_raises`;
  `test_the_token_never_appears_in_an_error`.
- [ ] **Step 2–4:** fail, implement, move the rotation notifier onto it, pass, `make check`, commit.

### Task 7: The bump proposer creates packages and alerts on what it can't route

Repository: orchestrator.

**Files:**
- Modify: `src/bump_proposer/cli.py`, `src/bump_proposer/standing.py` (`create`, and `publish`
  adding a new package directory), `src/bump_proposer/titles.py` (range parse beside, not inside,
  `BUMP_PATTERN`), `scripts/run-bump-proposer.sh` (fetch the `sds-bridge` token; state directory
  `~/Library/Application Support/sds/bump-proposer/`)
- Modify: `tests/architecture/test_bridge_edges.py` (an `alert_hub` column; the proposer's row gains
  `("POST", "/v1/alerts")` and the sender token's bearer), `OUTBOUND_ALLOWLIST`, the isolation row
- Docs: ADR-0025 amendment (policy approval reaches standing dependency-update `work` records on the
  inert list, Rulings 1–2), ADR-0028 amendment (the proposer creates the package; merging is in
  scope), the CLAUDE.md bullet "A `work`-source change record's status moves only by a human click"
  rewritten as the rule now stands, `docs/operations/landing-lanes.md`
- Test: `tests/bump_proposer/test_pass.py` (the `lifecycle()` call-order pins move),
  `tests/bump_proposer/test_titles.py`, launcher tests

**Interfaces:**
- Consumes: `create-standing` (Task 2), `KeyedAlerts` (Task 6), the change-manager fields (Task 1).
- Ecosystem mapping from the branch segment: `npm_and_yarn` → `npm`, `pip` → `pip`, `uv` → `uv`;
  anything else has no tooling.
- Per failed bump in scope (inert, granted): with tooling and no standing package → create, then the
  existing advance and propose; with tooling and a package → as today; no tooling, a pip range, or a
  same-dependency package in another ecosystem → alert. A passing Docker bump also alerts
  (Ruling 6). A key stops firing when its pull request merges or closes.
- Every proposal sends `target_repository` and `change_class = "dependency-update"`.
- New statuses `would-create`/`created` and `would-alert`/`alerted` are not findings.

- [ ] **Step 1: Write the failing tests.** `test_a_failed_bump_without_a_package_creates_one_then_proposes`
  (call order: create, transition, approve, hash, commit, push, propose; the commit adds the new
  directory); `test_a_range_bump_is_classified` (npm `>=1.2.0` to `>=1.3.0`);
  `test_a_pip_range_bump_alerts`; `test_a_docker_bump_alerts_whether_or_not_it_passes`;
  `test_a_github_actions_failure_alerts`; `test_a_second_ecosystem_for_one_dependency_alerts_not_creates`;
  `test_the_proposal_carries_target_repository_and_change_class`;
  `test_a_dry_run_reports_would_create_and_would_alert_and_writes_nothing`; launcher tests that the
  sender token is fetched and exported (dry run and `--submit`), with a mutation run proving each.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 8: The watcher alerts on a failed unit

Repository: orchestrator.

**Files:**
- Modify: `src/work_watcher/cli.py`, `scripts/run-work-carrier.sh` (state directory
  `~/Library/Application Support/sds/work-watcher/`, the same sender token),
  `tests/architecture/test_bridge_edges.py` (watcher row), the isolation row
- Test: `tests/work_watcher/test_work_watcher.py`, launcher tests

- [ ] **Step 1: Write the failing tests.** `test_a_record_with_a_failed_unit_alerts_once`;
  `test_the_alert_resolves_when_the_record_is_retired`; `test_a_record_that_is_not_a_standing_package_never_alerts`;
  `test_the_key_matches_the_proposers_for_the_same_bump`.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 9: Measure, ship in order, prove

- [ ] **Step 1: Your task (Ruling 9).** Generate it with exact steps: on `https://alerts.alobar.net/`
  create the source `sds-bridge`, copy the token, then run a given command that stores the clipboard
  in BWS project `SDS Operator` with `--color no` and prints only the new secret's id. Shape-check the
  saved value in-process before using it.
- [ ] **Step 2: Measure before anything ships.** From the orchestrator worktree, with
  `BUMP_PROPOSER_PACKAGES_CHECKOUT` pointed at the intent-packages worktree, run the bump proposer's
  dry run against production and count `would-create`, `would-propose` and `would-alert` per
  repository. If the alert count is more than five, report the number and the bumps to Devon before
  the first acting pass.
- [ ] **Step 3: Ship change-manager.** Pull request, CI, merge, deploy (it migrates on boot); verify
  `/api/work-changes` accepts the new fields and the served revision.
- [ ] **Step 4: Ship intent-packages, then the orchestrator immediately.** Merge intent-packages;
  then merge the orchestrator pull request with nothing landing between (the budget-agreement check
  reads intent-packages' `main`). Deploy the orchestrator by `docs/operations/deploy.md` (the patterns
  are in the image).
- [ ] **Step 5: Update the local checkouts.** Only when no bridge pass is due: fast-forward
  `~/Projects/intent-packages` and run `uv sync --frozen` in it, then fast-forward the orchestrator
  main tree.
- [ ] **Step 6: The first acting pass.** Run the bump proposer through launchd, then the carrier.
  Confirm from production: each created package is on intent-packages' `main`, each record is
  `approved` by `work-policy`, each carried record has a registered intake whose units' envelopes are
  recognised by known-good, and each alert is open once in alert-hub.
- [ ] **Step 7: Clean up** the three worktrees, the test database and the merged branches; check
  each main checkout's `git status`.
