Outcome: done

# Retire the placeholder lane's registration: build report

- **PR:** #654 (draft), branch `hq/retire-placeholder-registration`, based on `b0e25764`.
- **Head SHA:** code head `975df14ef6d460ee90c27f8916043de656e0bb34`; this report's commit sits on
  top of it.
- **Diff stat against `b0e25764`**, excluding this report: 9 files changed, 189 insertions(+),
  208 deletions(-).
- **`make check`:** 14,275 collected, 14,274 passed, 1 skipped, exit 0. Database
  `orchestrator_test_hq_w_81`.

## What I built

Every line pointer in the handoff matched at `b0e25764`.

**Deviations from the handoff**, each explained in the following sections:

- I kept and renamed `test_add_skips_the_token_check_only_for_the_placeholder`, which the handoff
  named as a deletion example, because its real-lane part covers a rule no other test covers.
- I changed two test files the handoff didn't list, `tests/scripts/test_deploy_orchestrator.py`
  and `tests/architecture/test_lane_image.py`, because they read the deleted constant and
  directory.
- I added a second fixture lane, `landing-ledger`, to `test_deploy_lanes.py`. The placeholder was
  the only fixture lane outside `STATEFUL_LANES`, so porting everything to `execution-watch`
  untested the stateless paths.

1. `scripts/deploy_orchestrator.py`: removed `PLACEHOLDER` and its comment. `LANES` is
   `frozenset(LANE_CHECKS)`. `registered_lanes` returns `lanes_in_registry(...)` alone, with the
   docstring fixed. The placeholder branch in `add_refusals` is gone, so every lane must map its
   own `<LANE>_BWS_ACCESS_TOKEN`. The early return in `quiet_slot_unmet` is gone, so every lane
   reads its Healthchecks check. The `--token` refusal in `_plan` is gone. The `!= PLACEHOLDER`
   clauses at the `--token-present` read, `finish_mismatches` and `_validate_container_reads` are
   gone, and each remaining condition applies to every lane. No `placeholder` hit remains in the
   file. No exception handling changed.
2. `lanes/placeholder/` deleted (`compose.yaml`, `crontab`).
3. `lanes/Dockerfile`: `placeholder` dropped from the state-directory loop and its comment.
4. `lanes/execution-watch/compose.yaml`: the comment no longer cites
   `lanes/placeholder/compose.yaml`. No other `lanes/` file referred to it.
5. `docs/operations/deploy.md`: the four special cases (lines 161, 204, 251 and 299) are removed
   and the paragraphs reflowed. Line 348's `placeholders` is the ping-URL set and stays.
6. Tests, listed in the following sections.

`docs/decisions/` is untouched.

### Tests deleted

| Test | Special case it covered |
|------|-------------------------|
| `test_the_placeholder_reads_no_listing_and_its_slot_is_the_window_alone` | The placeholder's quiet slot was the window alone, with no Healthchecks read. |
| `tests/architecture/test_lane_image.py::test_the_placeholder_compose_maps_only_its_two_variables` | The deleted placeholder compose's shape. |
| `tests/architecture/test_lane_image.py::test_the_placeholder_crontab_has_no_job` | The deleted placeholder crontab. |

### Tests ported

`tests/scripts/test_deploy_lanes.py` has a stateless fixture lane, `landing-ledger`, a registered
real lane that mounts no volume: `LEDGER_COMPOSE` (modeled on `lanes/landing-ledger/compose.yaml`),
`LEDGER_UUID`, an `sds-landing-ledger` check in the default listing, and an `added_ledger` helper.
`prepare_tag` and `prepare_token` pass `--running-volume` by `deploy.STATEFUL_LANES`, not by
lane name.

Ported to `landing-ledger` (stateless, as the placeholder was):

- `test_a_run_for_another_change_supersedes_the_waiting_one`.
- `test_a_waiting_run_doesnt_block_a_re_run_and_an_unfinished_one_does`.
- `test_add_refuses_a_schedule_written_other_than_false`.
- `test_add_refuses_a_compose_whose_schedule_doesnt_default_to_false` (edits `LEDGER_COMPOSE`).
- `test_add_refuses_a_tag_production_doesnt_serve`.
- `test_a_tag_with_two_successful_releases_refuses`.
- `test_a_failed_redeploy_prints_the_inverse_writes`.
- `test_restored_after_an_add_takes_the_two_gone_reads`.
- `test_a_tag_below_the_lanes_add_refuses`.
- `test_a_tag_needs_a_passed_add_and_the_recorded_running_tag`.
- `test_a_schedule_change_keeps_the_running_tag_and_compose`.

Ported to `execution-watch`:

- `test_add_skips_the_token_check_only_for_the_placeholder`, renamed
  `test_an_adds_finish_requires_the_token_read_and_a_present_token`. The handoff named it as a
  deletion example, but only its first line (`added_placeholder()`) was about the placeholder; the
  rest covers a real-lane rule (an `--add` finish without `--token-present` is usage, `false` is a
  condition, `true` passes) that no other test covers. I kept that part.
- `test_a_malformed_lane_command_is_usage` (every case on `execution-watch`, plus one case that
  `--lane placeholder` is usage).
- `test_the_lag_check_refuses_a_stale_registered_lane` (registers `execution-watch` through a new
  `_register_watch` helper, which two existing tests also use now).
- `test_the_lag_check_skips_a_registered_lane_with_no_passed_add` (expects
  `{"execution-watch"}`).
- `test_token_refuses_the_placeholder_and_a_lane_with_no_passed_add`, renamed
  `test_token_refuses_a_lane_with_no_passed_add`; the placeholder half is deleted, being the
  removed `--token` special case.
- `test_the_stateful_lanes_are_the_lane_images_state_directories` (no `- {"placeholder"}`).
- `test_the_check_names_are_the_launchers` loses its `"placeholder" not in LANE_CHECKS` line,
  which the new test covers through `LANES`.

Outside the handoff's list, two files read the placeholder and would fail once it was gone:

- `tests/scripts/test_deploy_orchestrator.py`: `_finished_placeholder` became `_finished_lane`
  (an `execution-watch` run), and the fixture repository's `base` registry holds an
  execution-watch token entry, so `test_prepare_refuses_a_lagging_lane_and_dispatches_nothing`,
  `test_prepare_admits_a_lane_on_the_served_tag` and
  `test_finish_prints_the_lane_phases_first_command_for_each_added_lane` keep a registered lane.
- `tests/architecture/test_lane_image.py`: the module docstring and
  `test_every_stateful_lane_has_an_orchestrator_owned_directory` (renamed) no longer include the
  placeholder; the unused `yaml` import is gone.

### Test added

`test_the_retired_placeholder_lane_is_neither_registered_nor_named_by_the_lag_check`: `placeholder`
isn't in `LANES`; `registered_lanes` is empty with an empty registry. With a finished placeholder
`--add` run file on disk at an older commit, the state the operator's lane directory holds,
`require_lanes_current` passes and `lanes_to_follow` is empty. Registering execution-watch then
gives `{"execution-watch"}`. I mutated `registered_lanes` to add `{"placeholder"}` back: the test
failed, and it passed once restored.

### Mutation check of the stateless paths

I replaced each of the eight `... in STATEFUL_LANES` conditions in `deploy_orchestrator.py` with
`True` and ran `test_deploy_lanes.py`:

- With every test on `execution-watch`, all eight survived.
- With the `landing-ledger` lane, five die (lines 1404, 1459, 1853, 2042 and 2065 of the edited
  file: the add refusal, the running-volume check, the finish mismatch and both validators).
- Three survive: the printed `--observed-volume` read (1569), and the two `--restored` volume
  checks (1891, 1924). The same three survive at `b0e25764` with the placeholder tests (run on a
  `git archive` copy), so this PR doesn't lose that coverage. I didn't add tests for them; it's
  outside this handoff.

The `placeholders` set in `test_every_lanes_ping_url_secret_is_listed_for_that_lane` is unchanged
(`{"deploy-watcher"}`), as is all ping-URL handling.

## Remaining `placeholder` hits (step 7)

From `git grep -l -i placeholder` at the code head, every file is in one of the following groups.

Records, which stay (ADRs, reports, plans and specs are the record of what was decided and done;
some of their hits use the ordinary word rather than naming the lane):

- ADRs: `docs/decisions/0043-…`, `0055-…`, `0059-…`.
- Reports: `adr-0055-accept-amend-19`, `adr-0059-amend-inc-2`, `adr-0059-amendment-3-forks-stay`,
  `adr-0059-ping-url-lookup-text`, `lanes-inc-1-image`, `lanes-inc-1-phases`, `lanes-inc-1-verify`,
  `lanes-inc-2-adr-0055-amendment`, `lanes-inc-2-adr-0055-amendment-fixes-2`,
  `lanes-inc-2-executor`, `lanes-inc-2-ping-probe`, `lanes-inc-2-ping-probe-fixes-1`,
  `lanes-inc-2-ping-probe-fixes-2`, `lanes-inc-2-ping-uuid`, `lanes-inc-2-registry-rows`,
  `lanes-inc-3-deploy-watcher`, `lanes-inc-3-landing-ledger`,
  `lanes-inc-3-landing-ledger-ping-uuid`, `lanes-inc-3-mint-helper`, `ping-url-machine-read`,
  `rotation-runbook-first-issue-created`, and this report.
- Plans and specs under `docs/superpowers/`: the 2026-07 plans (the ordinary word, predating the
  lanes), `2026-10-08-…`, `2026-10-09-…`, `2026-10-10-…`, `2026-10-12-…`, `2026-10-30-…`, and
  `specs/2026-08-02-…`.

Live files that name the placeholder lane on purpose:

- `tests/scripts/test_deploy_lanes.py`: the new test and the `--lane placeholder` usage case,
  which prove the retirement.

Live files that use the ordinary word, unrelated to the lane: the ping-URL `placeholders` set and
its mentions (`test_deploy_lanes.py`, `test_run_deploy_watcher.py`, `deploy.md` line 348),
`docs/operations/driving-a-unit.md`, placeholder test values in `tests/rotation_worker/`,
`tests/api/test_server_errors.py`, `tests/activation_sweep/test_bind.py`,
`tests/landing_ledger/`, `tests/architecture/test_external_content_observes_only.py`,
`test_rotation_edges.py`, `test_runtime_edges.py`, `tests/scripts/test_exit_probe.py`,
`tests/fixtures/`, and comments or strings in `src/` and `migrations/`. No other live file names
the placeholder lane.

## Self-review

I ran `/code-review` (medium) on `b0e25764...HEAD`. Its findings and what I did:

- An unfinished placeholder run would block every release, and `lanes-finish --lane placeholder`
  is now usage. I read the operator's lane state (`~/Library/Application Support/sds/deploy-lanes/placeholder/`):
  its four runs (one `add`, three `tag`) are all `finished`, so nothing blocks. Fixed nothing.
- The image no longer has `/var/lib/sds/placeholder` or the placeholder crontab, so a redeploy of
  the live `sds-lane-placeholder` resource on a new tag would exit 64. The handoff orders that
  removal; nothing redeploys the placeholder, since the deploy command refuses the lane after
  this change, and HQ deletes the resource next. Noted under concerns.
- The helpers pass the execution-watch volume for any stateful lane. Only `execution-watch` is a
  stateful fixture lane; left as is.
- Two reflowed `deploy.md` lines ran past the wrap width. Fixed.

- The removed branches leave each condition applying to every lane; no lane other than the
  placeholder ever took a removed branch, so real-lane behavior is unchanged.
- Production's lane state directory keeps the placeholder's run files. The unfinished-run check
  still reads every recorded directory, so an unfinished placeholder run would still hold a
  release. Its runs are expected to be finished; the new test proves a finished one doesn't hold a
  release.
- After this merges and deploys, `lanes-prepare --lane placeholder` is usage. HQ's later deletion
  of `sds-lane-placeholder` uses Coolify, not the deploy command, so nothing needs the lane name.

## Concerns

- The handoff named `test_add_skips_the_token_check_only_for_the_placeholder` as a deletion
  example. I kept and renamed its real-lane part, because the rule it covers has no other test.
- I changed two test files the handoff didn't list (`test_deploy_orchestrator.py`,
  `test_lane_image.py`); both are in this repository and broke on the deletion the handoff orders.
- Once an image built from this commit is served, the live `sds-lane-placeholder` resource must
  not be redeployed on a newer tag: that image has no placeholder crontab, so its entrypoint exits
  64. Delete the resource instead, as ADR-0059 plans.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging added or changed.
- **Code Brain rule 4 (never silently swallow an exception):** no `try`/`except` touched; the
  removed branches were plain `if` statements, and no bare `except` results.
- **Code Brain rule 5 (never log secrets):** no output added. The token reads still print `test -n`
  results only, never a value.
- **Infra Brain 212 (HTTPS for public FQDNs):** not touched.
- **Infra Brain 591, 592 (no BWS token in tracked files or history):** no token written; test
  fixtures use non-token UUIDs and resource ids.
- **Infra Brain 593 (no inline bootstrap token):** the composes still map `${...}` references only.
- **Infra Brain 1032 (rotate an exposed credential):** I surfaced no credential.
- **No Coolify scheduled task beside an in-container scheduler:** not touched. I called no
  Coolify, infraops or VPS tool.
