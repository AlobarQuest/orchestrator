Outcome: done

# Report: retire the Mac's lane plumbing (ADR-0059 increment 6)

Worker W-271. Branch `hq/lanes-inc-6-retire-mac-plumbing`, base `b006bbfc`. PR
[#788](https://github.com/AlobarQuest/orchestrator/pull/788), ready (not a draft), not merged.
Merging is HQ's act, `lanes-inc-6-merge`. The PR's head is the commit that adds this report. The
code commit is `aa919fa2`.

I made the changes HQ's answer to stop point 1 approved. That answer is in
`docs/superpowers/reports/lanes-inc-6-retire-mac-plumbing-question-answered.md`, which HQ wrote
and I didn't commit.

## What I built

**Deleted (30 files):**
- **Plists:** `scripts/com.devon.<label>.plist` for the 15 moved lanes. The labels are
  bump-driver, bump-proposer, change-proposer, deploy-watcher, estate-landing, execution-watch,
  inert-landing, landing-ledger, sds-membership-check, pin-watcher, revision-watcher,
  rotation-decomposer, rotation-proposer, unit-verifier and work-carrier.
- **Installers:** `scripts/install-<lane>-launchd.sh` for the same lanes.
- **Manifest:** their 30 `layers.toml` entries.

**Kept:**
- The plists and installers of `com.devon.activation-sweep` and `com.devon.rotation-worker`.
- All 18 `run-*.sh` launchers.
- All six `sds-*.sh` helpers, unchanged except for comments.

**Launchers.** Each of the 15 moved launchers loses its Mac path:
- the activation block, including the packages-checkout activation;
- the Keychain reads and `source sds-token.sh`;
- `gh auth token`;
- the Mac-only defaults, dead branches, and the Mac's `~/.factory/chain-db.env` switch handling;
- the Mac prose.

**Refusal placement.** Each launcher keeps its existing host test. Before it sources
`sds-deadman.sh`, arms its alarm or reads any secret, it refuses outside its container with the
`run-follow-up-mint.sh` shape: `FATAL: no BWS_ACCESS_TOKEN: the <lane> lane runs only in its
container, through lane-run` (the broker lanes say "no secrets-broker socket or
BWS_ACCESS_TOKEN"), then exits 1.

| Launcher | Guard line | Dead-man source or arm |
|---|---|---|
| `run-bump-driver.sh` | 103 | 112 |
| `run-bump-proposer.sh` | 170 | 189 |
| `run-change-proposer.sh` | 84 | 100 |
| `run-deploy-watcher.sh` | 56 | 64 |
| `run-estate-landing.sh` | 109 | 119 |
| `run-execution-watch.sh` | 40 | 48 |
| `run-inert-landing.sh` | 114 | 123 |
| `run-landing-ledger.sh` | 42 | 50 |
| `run-membership-check.sh` | 64 | 70 |
| `run-pin-watcher.sh` | 53 | 62 |
| `run-revision-watcher.sh` | 60 | 70 |
| `run-rotation-decomposer.sh` | 52 | 61 |
| `run-rotation-proposer.sh` | 113 | 121 |
| `run-unit-verifier.sh` | 44 | 51 |
| `run-work-carrier.sh` | 147 | 164 |

HQ's answer said "through `sds_outcome_fail` once `sds_outcome_begin` has run". I put every guard
before arming, so `sds_outcome_begin` never runs on a refused pass, and plain `exit 1` is the
answer's shape for that case. I chose this because a run armed outside the container takes the
dead-man helper's lookup by name. That reads the broad Keychain item and pings the production
lane's real check (`/start`, `/fail`) from the Mac. `run-follow-up-mint.sh` refuses at the same
point for the same reason.

**Execution watch (answer item 3).** The unguarded activation is gone, and no comment speaks of
activation any more.

**Tests:**
- **Keep-list pin.** `tests/scripts/test_launchd_installers.py` adds
  `test_only_the_staying_jobs_have_a_plist_and_an_installer` (the plists and the installers are
  each exactly the two staying jobs) and `test_no_container_lane_has_a_plist_or_an_installer`
  (no lane under `lanes/` has either). The old `>= 11` floor is gone.
- **Refusal tests.** Each moved launcher has a test that it refuses outside its container: exit 1,
  the FATAL line, no program run, an empty `bws` log and an empty ping log.
- **Re-homed tests of kept Mac code** (answer item 2):
  - `sds_upsert_check`: a new file, `tests/scripts/test_sds_install.py`, with four tests. It covers
    the created and updated answers, any other status, an unreadable key, and that the key reaches
    `curl` only on stdin. Its only test before was in the deleted installer section of
    `test_bridge_launchers.py`.
  - The activation sweep's `activate-checkout.sh` activation: `tests/scripts/test_run_activation_sweep.py`.
    An acting pass activates with `REPO_ROOT` and `$0`, a dry run doesn't activate, and a missing
    helper logs its line and the pass still runs. Its only test before was the deleted
    `test_packages_checkout_activation.py`.
  - These still have surviving tests:
    - The lookup by name in `sds-deadman.sh`: each lane's `*without_a_ping_url_secret*` test.
    - `sds-token.sh` (which the sweep sources): `test_run_activation_sweep.py` and the sweep tests
      in `test_launcher_scope.py`.
    - `sds_refuse_linked_worktree`: `test_launchd_installers.py`, on the two kept installers.
- **Removed from the sweep's tests.** The sweep's test fixture also gained a `curl` stub and a broad
  identity stand-in. Before, an acting pass in that test read the real broad Keychain item through
  `/usr/bin/security` and reached the real Healthchecks host.
- **New container tests that replace a deleted Mac-path check:**
  - `test_an_ambient_chain_variable_never_reaches_the_program` in `test_run_bump_proposer.py`.
  - `test_an_ambient_chain_variable_never_reaches_a_program` in `test_run_work_carrier.py`.

**Documents:**
- **`docs/operations/deploy.md`:**
  - One statement, in "The lane phases": a moved lane has no rollback to the Mac (#788), the change
    window's is increment 8's, and the follow-up mint never had one.
  - The lines that offered a rollback (the `--tag` bullet, the "Every lane is current" refusal and
    the `--token` close) now say only the change window can take one.
  - The per-lane "on the Mac … still read" sentences and the bump driver's `:50` timing line are
    rewritten.
  - The dated cut-over procedures are left as written.
- **`docs/operations/credentials.md`:** the holders table, the inert-landing, estate-landing,
  bump-driver and rotation-proposer paragraphs, and the two worked examples of the two-identity trap
  (re-pointed to `sds-deadman.sh`).
- **Lane pages:**
  - `landing-lanes.md`: `lane-run bump-driver --dry-run`. "Stop it" now uses `lanes-prepare --lane
    bump-driver --schedule false`, and the Mac bootout is marked done at the lane's cut-over.
  - `landing-audit.md`, `package-intake.md` and `rotation-executor.md`: `lane-run <lane>` with the
    same flags.
  - `rotation-proposer.md`: the container's crontab.
- **Root `CLAUDE.md` line 293:** narrowed to the activation sweep.
- **`PROJECT.md` line 227:** only the LaunchAgent clause is reworded.

**Comments:**
- `scripts/sds-deadman.sh`: the lookup by name is the activation sweep's.
- `scripts/sds-bws.sh`: names the scripts that read a second identity.
- `src/bump_driver/conformance.py`: the docstring sentence about the Mac.

**`.cred-consumers.toml` (note text only, 14 lines):**
- The 12 observer, verifier and work-carrier bearer rows name the lane's container, schedule and
  token instead of a moved plist and the Keychain (lines 256, 298, 380, 387, 394, 401, 415, 422,
  429, 436, 443 and 457).
- Line 1700, `factory-events-append-password`, names the container only.
- Line 1770, the `bws-bump-proposer-token` keeper, says there is no reader in this repository and
  that retiring the item is Devon's act.
- No `kind`, `ref`, `healthchecks_check` or other field changed.
- The 15 `bws-sds-operator-token` `runtime-fetch` rows are unchanged (answer item 8a).

## Tests and checks run

- **`make check`** on `orchestrator_test_hq_w_271` (migrated to head, `PGTZ=UTC`), after the
  commit. Exit 0: lint, format and pyright clean; 14,621 tests collected, 14,620 passed, 1 skipped.
  The first run, before two fixes, had 2 failures. Both were in `tests/work_carrier/test_workability.py`,
  which pinned the carrier's Mac `gh` text. I fixed them, as the table in the following section shows.
- **Keep-list pin mutation proof:**
  1. I backed up `com.devon.rotation-worker.plist` to a `mktemp` directory and moved it out of
     `scripts/`: 1 failed, 1 passed.
  2. I restored it with `cp`. `cmp` against the backup was identical.
  3. I copied the sweep plist to `scripts/com.devon.bump-driver.plist`: 2 failed.
  4. I moved it out again. Control: 2 passed. `git status` showed no stray plist.
- **Security scan** from the repository root: 0 BLOCK, 2 WARN. Neither warning is from this change.
  - `bws.secret-files-gitignored`: `*-migration/` isn't covered.
  - `bws.manifest-matches-usage`: `aaff45ca-…` is undeclared. Earlier reports at the base already
    record it (for example `adr-0059-a12-rotation-state-report.md` line 81), and the UUID isn't in
    this diff.
- **`code-standards risk`:** `normal`, before and after the commit, so I ran no vendor review.
- **`/code-review` (low)** on `b006bbfc...HEAD`: no findings.
- **`shellcheck`** on all 15 moved launchers: clean.

## Deleted tests and what still covers each

"Mac-only" means the behavior itself is gone, so nothing covers it.

| File | Deleted test | Covered by |
|---|---|---|
| Every `tests/scripts/test_run_<lane>.py` (15) | Plist assertions (`PLIST`), and the plist half of each crontab test | Mac-only (the plists are gone). The crontab half of each test stays. |
| `tests/scripts/test_run_bump_driver.py` | `test_the_keychain_and_gh_are_reached_only_after_the_container_block`, `test_on_the_mac_the_identities_and_gh_are_as_before_and_nothing_is_minted` (2), `test_on_the_mac_a_preset_github_token_is_used_as_before` | Mac-only |
| `tests/scripts/test_run_bump_driver.py` | `test_on_the_mac_the_programs_code_is_the_passs` | `test_the_programs_code_is_the_passs` |
| `tests/bump_driver/test_launcher.py` | `test_it_runs_hourly_at_fifty_minutes_past` | The crontab test in `test_run_bump_driver.py` |
| `tests/bump_driver/test_launcher.py` | `test_each_identity_reads_its_own_secrets_and_none_is_ambient`, `test_it_gives_the_generator_what_a_conformance_claim_needs` (Mac PATH lines) | Mac-only; the container's PATH is covered by the factory and code-standards shim tests |
| `tests/scripts/test_run_bump_proposer.py` | `test_the_keychain_and_gh_are_read_only_on_the_macs_branch`, `test_on_the_mac_nothing_is_minted_or_cloned_and_the_checkout_is_its_own` | Mac-only |
| `tests/scripts/test_run_change_proposer.py` | The Mac harness tests `test_a_submitting_run_reads_the_keeper_as_the_broad_account_and_exports_no_identity` and `test_a_dry_run_fetches_no_credential` | `test_a_submitting_pass_reads_every_secret_with_the_lanes_one_token`, `test_a_dry_run_reads_no_propose_bearer_still_mints_and_pings_nothing` |
| `tests/scripts/test_run_change_proposer.py` | `test_on_the_mac_the_reads_are_unchanged_and_nothing_is_derived_or_minted` | Mac-only |
| `tests/scripts/test_bridge_launchers.py` (whole file) | The outcome-alarm tests | Ported to `test_run_bump_proposer.py` (start then success, fail on every other code, dry run pings nothing, failure line, missing credential, no dead-man arm); for the change proposer, its existing container tests; for the carrier, `test_run_work_carrier.py` |
| `tests/scripts/test_bridge_launchers.py` (whole file) | The installer-upsert tests | `tests/scripts/test_sds_install.py` |
| `tests/scripts/test_bridge_launchers.py` (whole file) | `test_an_unresolved_check_never_gates_the_pass` | Each lane's `*without_a_ping_url_secret*` test |
| `tests/scripts/test_bridge_chain_switch.py` (whole file) | The Mac switch-file tests for the bump proposer and carrier | Mac-only. `factory_events_*` stays tested in `test_sds_bws.py` and `test_factory_events_session.py`. The ambient-variable property moved to the two new container tests. |
| `tests/scripts/test_run_deploy_watcher.py` | `test_on_the_mac_two_identities_and_the_gh_login_and_nothing_is_minted`, `test_on_the_mac_a_missing_gh_login_exits_1` | Mac-only |
| `tests/scripts/test_run_deploy_watcher.py` | `test_on_the_mac_preset_tokens_are_kept` | Ported as `test_preset_bearers_are_kept_and_only_the_mint_key_is_read` |
| `tests/scripts/test_run_estate_landing.py` | `test_the_keychain_and_gh_are_reached_only_after_the_container_block`, `test_on_the_mac_the_identities_and_gh_are_as_before_and_nothing_is_minted` (2) | Mac-only |
| `tests/scripts/test_run_estate_landing.py` | `test_on_the_mac_an_unreadable_coolify_token_is_logged_as_before` | `test_an_unreadable_coolify_token_is_logged_and_the_pass_still_runs` |
| `tests/scripts/test_run_estate_landing.py` | `test_on_the_mac_preset_credentials_are_used_as_before` | `test_credentials_already_in_the_environment_are_never_used` |
| `tests/scripts/test_run_estate_landing.py` | `test_on_the_mac_the_programs_code_is_the_passs` | `test_the_programs_code_is_the_passs` |
| `tests/scripts/test_run_execution_watch.py` | `test_the_job_passes_submit_every_fifteen_minutes` (plist) | `test_the_execution_watch_crontab_runs_the_scheduled_pass_every_fifteen_minutes` |
| `tests/scripts/test_run_execution_watch.py` | Bare, `--seed`, `--submit` and exit-code tests on the Mac harness | Converted to the container fixture |
| `tests/scripts/test_deploy_lanes.py` | The plist half of the execution watch's cadence test | Its crontab half |
| `tests/scripts/test_run_inert_landing.py` | `test_the_keychain_and_gh_are_reached_only_after_the_container_block`, `test_on_the_mac_the_identities_and_gh_are_as_before_and_nothing_is_minted` (2), `test_on_the_mac_preset_credentials_are_used_as_before` | Mac-only |
| `tests/scripts/test_run_inert_landing.py` | `test_on_the_mac_the_programs_code_is_the_passs` (2) | `test_the_programs_code_is_the_passs` |
| `tests/scripts/test_run_landing_ledger.py` | `test_on_the_mac_the_github_token_is_the_gh_login_and_nothing_is_minted` | Mac-only |
| `tests/scripts/test_run_membership_check.py` | `test_on_the_mac_the_reads_are_unchanged_and_nothing_is_cloned_or_minted` | Mac-only |
| `tests/scripts/test_run_pin_watcher.py` | The Mac case of `test_exit_codes_fold_as_before` | Its container case |
| `tests/scripts/test_run_pin_watcher.py` | `test_on_the_mac_the_narrow_identity_and_the_gh_login_and_nothing_is_minted`, `…_a_dry_run_does_not_need_the_observer_bearer`, `…_a_preset_github_token_is_kept`, `…_a_missing_gh_login_exits_1` | Mac-only |
| `tests/scripts/test_run_pin_watcher.py` | `test_on_the_mac_a_dry_run_fetches_no_alert_token` | `test_a_dry_run_still_derives_and_mints_but_fetches_no_alert_token` |
| `tests/scripts/test_run_pin_watcher.py` | `…_an_acting_pass_that_cannot_read_the_observer_bearer_exits_1` | Its container twin |
| `tests/scripts/test_run_pin_watcher.py` | `…_an_acting_pass_that_cannot_read_the_alert_token_exits_1` | `test_an_acting_pass_that_cannot_read_the_alert_token_exits_1_before_the_mint` |
| `tests/scripts/test_launcher_scope.py` | The ledger and membership scope tests | Converted to the container path, not deleted; the sweep tests are unchanged |
| `tests/architecture/test_membership_edges.py` | `test_the_launcher_reads_its_two_bws_identities_from_the_keychain` | Inverted to `test_the_launcher_reads_no_keychain_item_and_no_gh_login` |
| `tests/architecture/test_membership_edges.py` | `test_each_credential_goes_into_the_variable_the_program_reads` | Rewritten for the container's reads |
| `tests/scripts/test_run_revision_watcher.py` | The Mac case of `test_exit_codes_fold_as_before`, and `test_the_container_skips_activation_rather_than_logging_it_missing` | Its container case; the refusal test |
| `tests/scripts/test_run_revision_watcher.py` | `test_on_the_mac_two_identities_and_the_gh_login_and_nothing_is_minted`, `…_a_dry_run_fetches_no_alert_token`, `…_a_preset_github_token_is_kept`, `…_an_acting_pass_that_cannot_read_the_alert_token_exits_1`, `…_a_missing_gh_login_exits_1` | Mac-only, though container twins exist for the dry-run alert token, the preset token and the unreadable alert token |
| `tests/scripts/test_run_rotation_decomposer.py` | `test_the_keychain_is_read_only_on_the_macs_branch`, `test_on_the_mac_each_secret_is_read_with_its_own_identity` | Mac-only |
| `tests/scripts/test_run_rotation_decomposer.py` | `test_on_the_mac_an_unreadable_secret_keeps_its_message` | The container's unreadable-secret test |
| `tests/rotation_decomposer/test_launcher.py` | The assertion on the Mac line `_narrow_secret` | Rewritten to the container line; the same invariant (the sender token is read only on an acting pass) |
| `tests/scripts/test_run_rotation_proposer.py` | `test_the_refusal_comes_before_any_credential_or_switch`, `test_run_from_a_linked_worktree_it_refuses`, `test_control_from_a_main_tree_it_gets_past_the_refusal`, `test_in_the_lanes_container_it_skips_the_refusal` | Mac-only (the launcher's worktree refusal is gone). `sds_refuse_linked_worktree` stays tested in `test_launchd_installers.py`. |
| `tests/scripts/test_run_rotation_proposer_lane.py` | `test_the_keychain_is_read_only_after_the_container_block`, `test_on_the_mac_nothing_is_minted_or_cloned_and_the_checkouts_are_its_own` (2), `test_the_mac_branchs_environment_and_arguments_are_unchanged` (2), `test_on_the_mac_the_findings_switch_never_reaches_the_program` (4) | Mac-only, though container equivalents exist |
| `tests/scripts/test_packages_checkout_activation.py` (whole file) | Packages-checkout activation for three moved lanes | Mac-only. The sweep's own activation is re-homed in `test_run_activation_sweep.py`. |
| `tests/scripts/test_run_unit_verifier.py` | `test_on_the_mac_*` (3), the Mac case of the exit fold | Mac-only; the container case of the fold |
| `tests/scripts/test_run_unit_verifier.py` | `test_the_container_skips_activation_rather_than_logging_it_missing` | The refusal test |
| `tests/unit_verifier/test_launcher.py` | `test_the_lane_gets_its_alert_token` | `test_the_pass_mints_its_github_token_with_exactly_these_arguments` |
| `tests/unit_verifier/test_launcher.py` | `test_a_failed_alert_token_fetch_still_runs_the_pass` | Its same-named twin in `test_run_unit_verifier.py` |
| `tests/unit_verifier/test_launcher.py` | `test_a_failed_verifier_bearer_fetch_still_stops_the_lane` | `test_an_unreadable_verifier_bearer_exits_1_before_anything_runs` |
| `tests/unit_verifier/test_launcher.py` | `test_the_lane_s_codes_pass_through` | `test_exit_codes_fold_as_before` |
| `tests/unit_verifier/test_unit_verifier_launcher.py` | The plist, installer-label and installer-retirement tests | Mac-only |
| `tests/unit_verifier/test_unit_verifier_launcher.py` | `test_the_launcher_is_a_registered_reader_of_the_bootstrap_token` | Mac-only: the launcher no longer reads `BWS_ACCESS_TOKEN_SDS`. The registry row stays (follow-up 1). |
| `tests/scripts/test_run_work_carrier.py` | `test_the_container_skips_activation_and_reads_no_mac_identity`, `test_the_keychain_is_read_only_on_the_macs_branch`, `test_on_the_mac_nothing_is_minted_cloned_or_verified` (2) | Mac-only |
| `tests/work_watcher/test_launcher_orchestration.py` (whole file) | Watcher-before-carry ordering, the acting and bare passes, and the exit fold (including an unrecognized code) | Ported to `test_run_work_carrier.py` |
| `tests/work_watcher/test_launcher_orchestration.py` (whole file) | The two skipped-watcher tests | Mac-only (the `[SKIPPED]` branch is gone) |
| `tests/work_carrier/test_workability.py` | `test_the_launcher_still_runs_without_a_github_credential` | Mac-only (it ran the Mac `gh` block) |
| `tests/work_carrier/test_workability.py` | `test_the_launcher_supplies_the_watchers_github_credential` | Rewritten to pin `export WORK_CARRIER_GITHUB_TOKEN` and no `gh auth token` |
| `tests/architecture/test_landing_edges.py`, `test_verification_edges.py` | The `gh auth token` entry in the inert lander's, estate lander's and unit verifier's bearer sets | Dropped: the launchers no longer read it |

## Files changed

The commit changes 92 paths:
- the 30 deleted plists and installers;
- the 15 moved launchers;
- `scripts/sds-deadman.sh`, `scripts/sds-bws.sh` and `src/bump_driver/conformance.py` (comments
  and docstring);
- `layers.toml`, `.cred-consumers.toml`, `CLAUDE.md` and `PROJECT.md`;
- `docs/operations/{deploy,credentials,landing-lanes,landing-audit,package-intake,rotation-executor,rotation-proposer}.md`;
- the test files in the preceding table, with four whole files deleted and
  `tests/scripts/test_sds_install.py` added.

The report commit adds this file and the PR number in `deploy.md`.

## Self-review

- **Container behavior is unchanged.** Every removed range was in a branch the container's host test
  excludes, or after the container block's own `exit`. The one exception is the execution watch's
  unguarded activation, whose removal HQ approved. It drops one `[activation] helper missing` log
  line per pass from the container.
- **No moved launcher holds Mac code any more.** None contains `security find`, `activate_checkout`,
  `sds-token`, `gh auth token`, `BWS_ACCESS_TOKEN_SDS` or `VPS_BACKUP`.
- **The work was split across five subagents.** Each owned separate files, and a sixth pass made
  the refusal placement consistent. I checked the result in three ways: every guard's line against
  its arm's line (table in "What I built"), the full diff of `run-execution-watch.sh`, and two full
  `make check` runs.
- **Kept on purpose:**
  - Three lines in `run-bump-driver.sh` (16, 58 and 129) say "the Mac's conformance facts". That's
    the name of the facts the Mac reports through the chain, not Mac-path code.
  - Some launchers keep an always-true `if` wrapper around their container body, to keep the diff
    small.

## Concerns and follow-ups

1. **The `bws-sds-operator-token` rows (HQ's increment 6 open item).** The 15 `runtime-fetch` rows
   for the moved launchers stay in `.cred-consumers.toml`, with notes at lines 64, 71, 78, 85, 92,
   99, 113, 120, 127, 134, 141, 148, 155, 169 and 176. Each still says the launcher "reads Keychain
   Claude/BWS_ACCESS_TOKEN_SDS", which is no longer true. They mirror the approved package
   `rotation-bws-sds-operator-token` (revision 2). They need an intent-packages revision before that
   token's next rotation, or the rotation's reader verification passes on checks that ping with
   other credentials.
2. **The `BWS_ACCESS_TOKEN_BUMP_PROPOSER` Keychain item has no reader in this repository.**
   Retiring it is Devon's act. I took no Keychain action.
3. **Keychain readers that remain:**
   - **`BWS_ACCESS_TOKEN_SDS`:**
     - `scripts/sds-token.sh` (line 27, sourced by `run-activation-sweep.sh` line 118)
     - `scripts/deploy_orchestrator.py` (lines 191 and 221)
     - `scripts/exit_probe.py` (line 57)
     - `scripts/replay_deploy_signal.py` (line 65)
     - the on-demand readers the keeper row's note names
   - **`BWS_ACCESS_TOKEN_VPS_BACKUP`:**
     - `scripts/sds-deadman.sh`'s lookup by name, for the sweep
     - `scripts/sds-install.sh`'s `sds_upsert_check`
     - other repositories' jobs (`.cred-consumers.toml` lines 507-555)
4. **Stale pointers left as HQ decided:**
   - The `lanes/<lane>/crontab` comments cite the deleted plists, at line 2 for 13 lanes and line 3
     for pin-watcher and membership-check.
   - change-manager's `.cred-consumers.toml` line 108 holds a stale note, which belongs to
     change-manager.
   - `docs/method-lessons.md` line 593 is a lesson and stays.
   - `docs/operations/local-development.md` #176 ("`com.devon.deploy-watcher` (and its
     siblings)", and the "twenty launch agents" count measured on 2026-11-04) is a dated record and
     stays.
   - ADR-0031 line 156 names the deleted `test_packages_checkout_activation.py`, and I don't edit
     ADRs.
5. **The `run-fork-harden.sh` and `run-tool-installer.sh` registry rows** name launchers that left
   for localops. They are task (4)'s, as HQ decided.
6. **`deploy_orchestrator.py`'s three rollback messages** (lines 1345-1346, 1405 and 1846) are
   unchanged, as HQ decided. `deploy.md` says only the change window can take a rollback to the
   Mac.
7. **A test-hygiene risk I didn't sweep for.** The activation sweep's test fixture read the real
   broad Keychain item and reached the real Healthchecks host before this change. I fixed that in
   this change. I didn't audit the rest of the suite for other tests that run a launcher with
   neither `SDS_DEADMAN_PING_URL_UUID` nor a `BWS_ACCESS_TOKEN_BROAD` stand-in on a Mac, which would
   make `/usr/bin/security` ask for the broad item. The moved lanes' refusal tests now prove that
   no secret is read and no ping is sent.
8. **Not covered by the handoff's standards section:** none.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production).** No log line was added or
  changed. The only change is that the removed Mac branches and the execution watch's unguarded
  `[activation] helper missing` line no longer print. The new refusal is one `FATAL:` line on
  stderr, the form `run-follow-up-mint.sh` and every launcher's existing FATAL lines already use.
- **Code Brain rule 4 (never swallow an exception silently).** No new `|| true` or empty handler.
  Every refusal exits 1 with its reason. The removed branches took their `|| true` Keychain reads
  with them, and no test hides a failure.
- **Code Brain rule 5 (never log secrets).** No output prints a secret.
  - The refusal names no value.
  - The registry notes name ids and checks only.
  - The new `sds_upsert_check` tests assert the key never appears in `curl`'s arguments or in
    either output stream, only on its stdin config.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value).**
  - No token or value was written. The tests use placeholder strings such as `not-a-real-identity`.
  - The security scan, run from the repository root, reports 0 BLOCK.
  - No plist, compose or script gained an inline `BWS_ACCESS_TOKEN` value. The change removed
    Keychain reads and touched no compose file.
- **Infra Brain 1032 (exposure means rotate).** I saw no secret value anywhere in this work.
- **Infra Brain 212 (HTTPS for public FQDNs).** It doesn't apply: nothing is served and no FQDN
  changed.
