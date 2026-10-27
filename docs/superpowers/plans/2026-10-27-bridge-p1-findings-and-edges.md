# Bridge layer, Part 1: findings and edges — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every finding a bridge lane makes reaches alert-hub, and one test states every bridge lane's outbound surface.

**Architecture:** The three bridge launchers (`run-bump-proposer.sh`, `run-change-proposer.sh`,
`run-work-carrier.sh`) stop arming the dead-man switch and become outcome alarms, the pattern
`run-membership-check.sh` already uses: every non-zero exit pings `/fail` with the report as its body,
on a check routed to the `alert-hub` channel. A new architecture test tabulates each lane's
change-manager, orchestrator and GitHub routes and its bearers, and probes each lane's own route
predicates against a shared corpus. `membership_check` gains two rows.

**Tech Stack:** Bash launchers, Python 3 (`.python-version`), pytest, Healthchecks v3 API.

**Spec:** `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`, Part 1 (and "What was measured").
This plan covers Part 1 only. Parts 2 to 5 each get their own plan after the previous part lands,
because each builds on the code the previous part changes.

## Global constraints

- Work in a worktree with its own `.venv` and its own `orchestrator_test_<ws>` database
  (`docs/operations/local-development.md`). Never edit, check out or run `uv` in the main tree.
- Never put a secret in a prompt, tool argument or transcript; a ping URL goes to `curl` on stdin
  (`--config -`), never as an argument. Every parsed `bws` call passes `--color no`.
- Don't poll GitHub without a pause (`--watch --interval 30`).
- Every new file gets a row in `layers.toml` (`tests/architecture/test_layer_manifest.py`).
- A launcher that doesn't arm the dead-man switch must not contain the string `sds_deadman_arm`
  (`tests/scripts/test_sds_deadman.py` keys on it).
- `make check` must be green before each commit that touches logic.

## Review focus

1. **A dry run pings the alarm.** A person running a launcher by hand without its acting flag
   (`--submit` or `--register`) expects no Healthchecks ping. Pinned in Task 2.
2. **The lane log goes blank.** The lanes log to `~/Library/Logs/<lane>.log` through the plist's
   `StandardOutPath`; redirecting output into the report must not lose it. Pinned in Task 2.
3. **The carrier pass reports only one program.** `run-work-carrier.sh` runs `work-watcher` then
   `work-carrier`; the report must carry both outputs and the ping must use the worst code. Pinned in
   Task 2.
4. **An unresolved check blocks the lane.** If Healthchecks is unreachable, the lane must still run.
   Pinned in Task 2.
5. **A predicate widens and the table follows silently.** The edge test must fail when a lane's
   predicate accepts a corpus path its row doesn't list, proven by a control. Pinned in Task 3.

---

### Task 1: A shared outcome ping and check upsert

**Files:**
- Modify: `scripts/sds-deadman.sh` (add `sds_outcome_ping`)
- Modify: `scripts/run-membership-check.sh`, `scripts/run-fork-sync-watch.sh` (use it)
- Modify: `scripts/sds-install.sh` (add `sds_upsert_check`)
- Modify: `scripts/install-membership-check-launchd.sh`, `scripts/install-fork-sync-watch-launchd.sh`
- Test: `tests/scripts/test_sds_deadman.py`, `tests/scripts/test_run_fork_sync_watch.py`,
  `tests/scripts/test_launcher_scope.py`

**Interfaces:**
- Produces: `sds_outcome_ping <suffix> [report-path]` — pings `${SDS_DEADMAN_PING_URL}<suffix>` with
  the URL on curl's stdin config and the report (if given) as `--data-binary @<path>`; does nothing
  when `SDS_DEADMAN_PING_URL` is empty; always returns 0.
- Produces: `sds_upsert_check <payload-json>` — POSTs the payload to the Healthchecks v3 checks API
  with the API key (BWS `51509d5e-64f5-4781-ae82-b4ed00b644e1`) fetched in-process and passed through
  `--config -`; prints `created` (201) or `updated` (200); returns non-zero otherwise.

- [ ] **Step 1: Write the failing tests.** In `test_sds_deadman.py`, add
  `test_the_outcome_ping_sends_the_report_as_the_body_and_the_url_on_stdin` (stub `curl` records its
  argv and stdin; assert the URL is absent from argv, present in stdin, and `--data-binary @<report>`
  is in argv) and `test_the_outcome_ping_does_nothing_without_a_resolved_url`. Add
  `test_the_upsert_passes_the_key_on_stdin_and_reports_created_or_updated` beside the existing
  installer test in `test_run_fork_sync_watch.py`.
- [ ] **Step 2: Run them and see them fail.** Run `.venv/bin/pytest tests/scripts/test_sds_deadman.py tests/scripts/test_run_fork_sync_watch.py -q`. Expected: the new tests fail (function not defined).
- [ ] **Step 3: Implement both helpers.** Lift `sds_outcome_ping` from `membership_ping`
  (`run-membership-check.sh:63-71`), which is byte-identical to `watch_ping`. Lift `sds_upsert_check`
  from the two installers' upsert code. Replace the local copies in all four scripts with calls.
- [ ] **Step 4: Run the script tests.** Run `.venv/bin/pytest tests/scripts -q`. Expected: all pass,
  including the unchanged membership-check and fork-sync-watch launcher tests.
- [ ] **Step 5: Commit.** `git commit -m "scripts: share the outcome ping and the check upsert"`

### Task 2: The bridge launchers become outcome alarms

**Files:**
- Modify: `scripts/run-bump-proposer.sh`, `scripts/run-change-proposer.sh`, `scripts/run-work-carrier.sh`
- Modify: `scripts/install-bump-proposer-launchd.sh`, `scripts/install-change-proposer-launchd.sh`,
  `scripts/install-work-carrier-launchd.sh`
- Modify: `docs/operations/local-development.md`, `docs/operations/package-intake.md:107-109`
- Modify: `docs/decisions/` — an amendment to ADR-0021 recording that bridge-lane findings reach
  alert-hub through outcome alarms
- Create: `tests/scripts/test_bridge_launchers.py`
- Modify: `layers.toml` (place the new test in the `bridge` layer)

**Interfaces:**
- Consumes: `sds_outcome_ping`, `sds_upsert_check`, `sds_deadman_resolve` (Task 1 and existing).
- Produces: each bridge launcher pings `<check>/start` before the program and, after it, success with
  no body on exit 0 or `/fail` with the report on any non-zero exit. Check names are unchanged:
  `sds-bump-proposer`, `sds-change-proposer`, `sds-work-carrier`.

Behaviour every bridge launcher must have:
- It pings only on an acting run: `--submit` for `bump-proposer` and `change-proposer`, `--register`
  for `work-carrier`. Without that flag it runs the program and pings nothing.
- The program's combined output is written to a `mktemp` report and then copied to stdout, so the
  lane log keeps it. In `run-work-carrier.sh` both `work-watcher` and `work-carrier` append to the
  same report, and the ping uses `WORST_CODE` from the existing `_rank`.
- A credential failure (each existing `exit 1` path) writes its message to the report and pings
  `/fail` with it, the way `tool_fail` does in `run-membership-check.sh:83-87`.
- An unresolved check never stops the lane.
- The header's EXIT CODES block keeps its meanings, drops the dead-man text, and states that 1, 2 and
  3 all ping `/fail`.

Each installer upserts its check with `sds_upsert_check` before bootstrapping, using these payloads
(`"channels": "alert-hub"`, `"unique": ["name"]`, `"tz": "America/New_York"` in all three):

| Check | Schedule | Grace (s) |
|---|---|---|
| `sds-bump-proposer` | `"schedule": "50 6 * * *"` | 14400 |
| `sds-work-carrier` | `"schedule": "5 7 * * *"` | 14400 |
| `sds-change-proposer` | `"timeout": 3600` (it runs on `StartInterval`) | 1800 |

Verify each schedule against its plist before writing it.

- [ ] **Step 1: Write the failing tests.** In `tests/scripts/test_bridge_launchers.py`, parametrised
  over the three launchers and mirroring `test_run_fork_sync_watch.py`'s stubs:
  - `test_an_acting_clean_pass_pings_start_then_success`
  - `test_every_other_exit_pings_fail_with_the_report_as_its_body`, over `[3, 2, 1, 127]`
  - `test_a_run_without_the_acting_flag_pings_nothing`
  - `test_the_report_is_also_written_to_the_lane_log` (stdout carries the program's output)
  - `test_an_unresolved_check_never_gates_the_pass`
  - `test_the_launcher_does_not_arm_the_dead_man_switch` (`"sds_deadman_arm" not in` the script)
  - `test_a_missing_credential_pings_fail_with_its_reason`
  - `test_the_installer_upserts_its_check_to_alert_hub` (asserts name, `channels == "alert-hub"`,
    `unique == ["name"]`, and the schedule or timeout in the table)

  And, for `run-work-carrier.sh` only,
  `test_the_carrier_report_holds_both_programs_and_pings_the_worst_code` (watcher exits 3, carrier
  exits 0: report holds both outputs and the ping is `/fail`).
- [ ] **Step 2: Run them and see them fail.** Run `.venv/bin/pytest tests/scripts/test_bridge_launchers.py -q`. Expected: fail.
- [ ] **Step 3: Rewrite the three launchers and installers** to the behaviour listed above. Fix the
  stale `install-bump-proposer-launchd.sh:15-18` header ("does not push") while you're in it.
- [ ] **Step 4: Fix the docs.** Replace `package-intake.md:107-109`'s claim that a person resolves a
  carried record: `work-watcher --retire` retires it (ADR-0029). In `local-development.md`, state that
  the bridge launchers are outcome alarms. Write the ADR-0021 amendment.
- [ ] **Step 5: Run the script suites.** Run `.venv/bin/pytest tests/scripts tests/work_watcher/test_launcher_orchestration.py -q`.
  Expected: all pass. If `test_sds_deadman.py::test_the_two_schemes_are_both_present_so_neither_test_above_is_vacuous`
  fails, the remaining armed launchers no longer cover both groups; read it before changing anything.
  Ten launchers stay armed: activation-sweep, deploy-watcher, estate-landing, inert-landing,
  landing-ledger, pin-watcher, revision-watcher, rotation-proposer, rotation-verifier and
  tool-installer.
- [ ] **Step 6: Run `make check`.** Expected: green.
- [ ] **Step 7: Commit.** `git commit -m "bridge launchers: findings ping /fail to alert-hub"`

### Task 3: One test states every bridge lane's surface

**Files:**
- Create: `tests/architecture/test_bridge_edges.py`
- Modify: `layers.toml`
- Modify: `tests/change_proposer/test_change_proposer.py:90-106` (rename and re-document the two stale
  tests)
- Modify: `tests/architecture/test_wsp21_invariant_scan.py:544-551` (the stale
  `change_proposer/change_manager.py` reason)

**Interfaces:**
- Consumes: each lane's existing route predicates. Read their exact names and signatures in
  `src/bump_proposer/change_manager.py`, `src/bump_proposer/landing_policy.py`,
  `src/bump_proposer/orchestrator_client.py`, `src/change_proposer/change_manager.py`,
  `src/work_carrier/change_manager.py`, `src/work_carrier/orchestrator_client.py`,
  `src/work_carrier/declaration.py`, `src/work_watcher/change_manager.py`,
  `src/work_watcher/orchestrator_client.py`, `src/work_watcher/github.py`, and whatever module
  `work_watcher` lists approved records through (trace it from `work_watcher/pipeline.py`).
- Produces: `EDGES: dict[str, Edge]`, where `Edge` is a frozen dataclass with
  `change_manager: frozenset[tuple[str, str]]` (method, path), `orchestrator: frozenset[tuple[str, str]]`,
  `github: frozenset[tuple[str, str]]` and `bearers: frozenset[str]` (BWS UUIDs). Paths use a concrete
  id (`44`) where the route takes one.

The table, from the 2026-10-27 recon (confirm each against the predicates; a mismatch is a finding to
report, not to paper over):

| Lane | change-manager | orchestrator | GitHub | Bearers |
|---|---|---|---|---|
| `bump_proposer` | `POST /api/work-changes`, `GET /api/items`, `GET /api/landing-policy` | `POST /api/v1/observations` | reads through `landing_ledger.github` | `acccb346-…`, `314f276d-…`, `f793576f-…` |
| `change_proposer` | `POST /api/deploy-changes`, `POST /api/items/44/deploy-retirement`, `GET /api/items` | none | reads through `deploy_watcher.github` | `acccb346-…` |
| `work_carrier` | `GET /api/items` | `POST /api/v1/package-intakes`, `GET /api/v1/change-records/44/work` | `GET /repos/o/r/contents/factory-target.toml` | `314f276d-…`, `221a48d5-…` |
| `work_watcher` | `POST /api/items/44/work-retirement`, `GET /api/items` (listed by `work_watcher/pipeline.py` through `work_carrier.change_manager`) | `GET /api/v1/change-records/44/work` | `GET /repos/o/r/pulls/44` | `acccb346-…`, `314f276d-…`, `221a48d5-…` |

Use the full UUIDs from the launchers. A lane whose GitHub access goes through another package's
reader is pinned by naming that import, not by probing.

- [ ] **Step 1: Write the tests.**
  - `test_each_lane_accepts_exactly_its_routes_among_the_corpus`, parametrised over lanes. The corpus
    is the union of every row's paths plus these probes: `/api/items/44/approve`, `/claim`,
    `/resolve`, `/reactivate`, `/outcome`, `/handoff`, `/deploy-observation`, `/api/sync`,
    `/api/deploy-policy`, `/api/items/`, `/api/items/44`, each listed path with a trailing `/` and
    with an `x` suffix, and `/api/items/../44/work-retirement`. For every (method, path) in the
    corpus, the lane's predicates accept it exactly when its row lists it.
  - `test_each_launcher_fetches_exactly_its_bearers`: the UUIDs assigned in the lane's launcher
    (`run-work-carrier.sh` serves both `work_carrier` and `work_watcher`) equal the union of its rows'
    `bearers`.
  - `test_the_orchestrator_column_agrees_with_the_observe_only_classification`: for each lane, the
    row's orchestrator writes equal `CLASSIFICATION[lane].writes` in
    `tests/architecture/test_external_content_observes_only.py`.
  - `test_the_table_is_the_bridge_layer_population`: `EDGES`'s keys equal the bridge programs in
    `layers.toml`.
  - `test_control_a_widened_predicate_fails_the_table`: monkeypatch one lane's write predicate to also
    accept `/api/items/44/approve` and assert the comparison helper reports it.
- [ ] **Step 2: Run them.** Run `.venv/bin/pytest tests/architecture/test_bridge_edges.py -q`.
  Expected: pass, including the control. Then mutate one predicate in `src/` by hand (accept a
  trailing slash), confirm the test fails, and restore it, clearing `__pycache__` around the mutation.
- [ ] **Step 3: Fix the stale descriptions.** In `test_change_proposer.py`, rename
  `test_the_only_write_is_the_proposal_ingress` to `test_the_proposal_ingress_is_anchored` and
  `test_the_producer_has_no_read_surface_at_all` to `test_the_producer_writes_nothing_outside_its_two_routes`,
  rewriting their docstrings to the two writes and one read; point them at `test_bridge_edges.py` for
  the full surface. Update the `OUTBOUND_ALLOWLIST` reason the same way.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "architecture: one table states every bridge lane's surface"`

### Task 4: `membership_check` covers the bridge's own lists

**Files:**
- Modify: `src/membership_check/consistency.py` (`check`), `src/membership_check/cli.py`
- Modify: `tests/architecture/test_out_of_process_isolation.py` (the `membership_check` row's
  allowed imports gain `change_proposer`, if it lacks it)
- Test: `tests/membership_check/test_consistency.py`

**Interfaces:**
- Consumes: `change_proposer.criteria._ROLLBACKS` (keys are lowercased `owner/repo`);
  `bump_proposer.standing.discover(root)` and `checkout_root()`, whose values carry
  `target_repository`.
- Produces: `check(...)` takes two new keyword arguments, `rollbacks: Iterable[str]` and
  `standing_update_repositories: Iterable[str]`, and adds, with the existing `_require`:
  - `_require("the rollback table", rollbacks, rollouts, "rollouts")`
  - `_require("a standing dependency-update package", standing_update_repositories, updates, "dependency_updates")`

  An unreadable packages checkout (`StandingError`, or a missing directory) is a gap (exit 3), never
  an empty list.

- [ ] **Step 1: Write the failing tests.**
  `test_a_rollback_for_a_repository_without_rollouts_is_a_disagreement`,
  `test_a_standing_package_for_a_repository_without_dependency_updates_is_a_disagreement`,
  `test_the_live_population_agrees` (brain and change-manager granted `rollouts`; standing packages
  only for `infraops-mcp-server`, which is granted), and
  `test_an_unreadable_packages_checkout_is_a_gap_not_an_empty_list` (CLI level, exit 3).
- [ ] **Step 2: Run them and see them fail.** Run `.venv/bin/pytest tests/membership_check -q`.
- [ ] **Step 3: Implement** the two rows and the CLI reads.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit.** `git commit -m "membership_check: compare the rollback table and standing packages with the record"`

### Task 5: Ship and install

- [ ] **Step 1: Review.** Run `/code-review` on the branch diff; fix what holds up.
- [ ] **Step 2: Open the pull request** (spec, plan and Part 1 together) and watch it with
  `gh pr checks --watch --interval 30`. Merge on green and confirm `state == MERGED`.
- [ ] **Step 3: Install.** While no bridge pass is due (bump 06:50, carrier 07:05, change-proposer
  hourly), `git pull` in the main tree (`docs/operations/local-development.md` #176; no new console
  script, so no `uv sync`), then run the three bridge installers. Each prints `created` or `updated`
  for its check. The membership-check and fork-sync-watch launchers pick up Task 1 by path; their
  checks didn't change, so don't rerun their installers.
- [ ] **Step 4: Prove it.** Read each check through the Healthchecks API (key in-process; print
  name, channels, schedule, status only) and confirm `alert-hub` routing. Run one acting
  `run-change-proposer.sh` pass and confirm its check received `/start` and the right outcome ping.
- [ ] **Step 5: Clean up.** Check the main tree's `git status`, then remove the worktree, its test
  database and the merged branch.
