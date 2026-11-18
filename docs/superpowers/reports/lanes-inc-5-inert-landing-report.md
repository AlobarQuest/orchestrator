Outcome: done

# Build report: the inert lander's lane files and launcher (ADR-0059 increment 5, step 3g-1)

Worker W-206, branch `hq/lanes-inc-5-inert-landing`, based on `origin/main` at `9052f7f8` (#734).
The changes are code, tests, lane files, `.bws-secrets.toml` rows, and docs only. I made no live
call, no BWS read, no mint, and no Coolify, VPS, or `launchctl` act.

## What changed

| File | Change |
|---|---|
| `scripts/run-inert-landing.sh` | Adds a container branch (`BWS_ACCESS_TOKEN` set) that runs the whole pass and exits. Activation is now inside `if [ -z "${BWS_ACCESS_TOKEN:-}" ]` (re-indented only). Every Mac line after the container block is byte-for-byte as before. The header gains a "TWO HOSTS, ONE LAUNCHER" paragraph. |
| `src/inert_lander/cli.py` | Adds `inert-landing repositories`, which prints the landing policy's inert population and fails closed (exit 2, empty stdout). Adds `_landing_policy`, which `run` and `repositories` share, so both read the policy under the same settings. `main` dispatches the subcommand the way `bump-proposer repositories` does (#733). |
| `lanes/inert-landing/compose.yaml` | Adds one service with the lane token mapped from `${INERT_LANDING_BWS_ACCESS_TOKEN}`, the schedule defaulting to `false`, `LANDER_STATE_DIR` on the named volume `sds-lanes-inert-landing-state`, an empty ping-URL placeholder, and `restart: unless-stopped`. It has no network, ports, or labels. |
| `lanes/inert-landing/crontab` | `37 * * * * lane-run inert-landing --submit`. |
| `.bws-secrets.toml` | Adds `inert-landing` to `lanes` on the four rows (SYSTEM, change-manager READ, `sds-bridge` sender, read App key), and updates each row's reader comment. |
| `layers.toml` | Registers `tests/scripts/test_run_inert_landing.py` under `[landing]`. The new `tests/inert_lander/test_repositories.py` is already covered by `tests/inert_lander/**`. |
| `tests/scripts/test_run_inert_landing.py` | New. 38 tests of the container branch, the Mac path, and the lane files. |
| `tests/inert_lander/test_repositories.py` | New. 8 tests of the subcommand. |
| `tests/architecture/test_landing_edges.py` | Edge table: adds `READ_APP_KEY` to the inert lander's `bearers`, because the launcher now names `314a966c-…` (the same kind of edit #733 made to `test_bridge_edges.py`). `GH_AUTH_TOKEN` stays, because the Mac still calls it. |
| `tests/scripts/test_deploy_lanes.py` | Adds `inert-landing` to `placeholders`. |
| `docs/operations/deploy.md` | Adds the inert lander's lane section and its remaining cut-over steps, after the rotation proposer's. |
| `docs/operations/credentials.md` | Adds a holder paragraph that names the `inert-landing` lane account after cut-over. |

`scripts/lane_run.py` is unchanged. The container needs `LANDER_STATE_DIR` (already in
`STATE_VARIABLES`) and `SDS_DEADMAN_PING_URL_UUID` (already in `COPIED`). The lander's URL
variables default to production (`https://sds.alobar.net`, `https://change-mgr.alobar.net`), so no
new variable is needed. `test_every_compose_variable_lane_run_must_pass_is_in_its_list` holds this.

## Each secret and how the container reads it

The container reads every secret with the lane's one token, using the one-argument form of
`sds_bws_value` (the token stays in the environment, never in a process argument). Each secret is
always read. Values already in the environment are unset first, so `lane-run`'s environment can't
supply one. Each failed read prints `FATAL: … (the lane's token)` and exits 1. No read has
`|| true`.

| Secret | UUID | When | Container order |
|---|---|---|---|
| change-manager READ bearer → `INERT_LANDING_CHANGE_MANAGER_TOKEN` | `314f276d-…` | every pass | 1 (the repository list needs it) |
| read-only App key, piped into `sds-github-mint` | `314a966c-…` | every pass | 2 |
| orchestrator SYSTEM bearer → `INERT_LANDING_ORCHESTRATOR_TOKEN` | `221a48d5-…` | every pass | 3 (after the derivation and mint, so neither sees it) |
| `sds-bridge` alert sender → `SDS_BRIDGE_ALERT_TOKEN` | `b415d03b-…` | `--submit` only | 4 |

The every-pass and `--submit`-only split matches the Mac's. The order differs: the Mac reads the
change-manager bearer, then SYSTEM, then `gh`, then the alert sender. `unset BWS_ACCESS_TOKEN` runs
before the lander. The derivation and the mint run under `env -u BWS_ACCESS_TOKEN`. The container
reads no Keychain item and calls no `gh`.

## The read token and its permissions

`INERT_LANDING_GITHUB_TOKEN` replaces `gh auth token` in the container. The container mints it on
every pass, bare or submitting, as follows:

```
sds_bws_value 314a966c-… | env -u BWS_ACCESS_TOKEN sds-github-mint --app-id 5684758 \
  --repository <each inert repository> --permission metadata=read --permission pull_requests=read
```

- **Repositories:** the mint names the repositories `inert-landing repositories` prints, which
  derives them the way #733 does. That list is the landing policy's `inert` block, read with the
  change-manager READ bearer. It is exactly the set the pass enumerates: `_subjects` iterates
  `rule.inert` and nothing else. Revert pull requests are subjects only inside those repositories.
  `test_every_repository_the_pass_lists_is_named` holds the list against `_subjects`, and
  `test_the_deploying_population_is_not_named` checks that the deploying block is excluded. An
  empty population can't occur: `parse` refuses one, and the launcher also refuses an empty list
  with exit 2.
- **Permissions:** the lander's only GitHub read is `GitHubReader.open_pull_requests`
  (`GET /repos/{r}/pulls`), which needs `pull_requests: read`. Every installation token carries
  `metadata: read`. `test_the_inert_landers_github_need_is_the_one_listing` already pins that one
  read. The lander never writes with this token; it lands through the orchestrator's dispatch App.
- **Failure:** a failed derivation exits with the subcommand's code (2 for an unreadable policy)
  and mints nothing. A failed key read or mint exits 1.

## The crontab and why

`37 * * * * lane-run inert-landing --submit`. Minute 37 is free: the minutes in use are 20, 35, 40,
50, `2-52/10`, `*/15`, and the six daily lanes. The lane-minute test
(`test_no_two_lanes_start_a_pass_in_the_same_minute`) passes. The quiet-slot rule in `deploy.md`
governs redeploys (outside 02:00 to 06:00, and not while the check is `started`), not crontab
minutes, so it doesn't refuse 37. Minute 35, the Mac job's, belongs to the change proposer. The
crontab line carries `--submit`, so HQ's cycles never fire the lane out of schedule. A bare
`lane-run inert-landing` is the reporting run, and it pings the lane's real check, as ADR-0059
says at about line 1141.

## The state volume

The named volume `sds-lanes-inert-landing-state` is mounted at `/var/lib/sds/inert-landing`, which
`lanes/Dockerfile` line 71 pre-creates with owner `orchestrator`. `LANDER_STATE_DIR` points there,
and `held_alerts.state_dir` uses the value as given, without appending the lane's name. It holds
`held-since.json`, `alerts.json`, and `revert-alerts.json`. The container launcher refuses a pass
with `LANDER_STATE_DIR` unset (exit 1, before any read). Otherwise the state would go to
`lane-run`'s discarded `HOME`. `deploy.add_refusals` passes on the compose, and `_state_volume`
resolves the named volume.

## The Mac path, and how I proved it unchanged

1. **Text:** the diff's only removed lines are the activation block's 10 lines, re-added two spaces
   deeper inside `if [ -z "${BWS_ACCESS_TOKEN:-}" ]`. On the Mac that test is always true, because
   launchd never sets the variable. Every other Mac line is unchanged. The container block exits,
   and `test_the_keychain_and_gh_are_reached_only_after_the_container_block` checks that every
   `/usr/bin/security`, `sds-token.sh`, and `gh auth token` line comes after it.
2. **Behaviour, differential:** I ran the launcher at `9052f7f8` and the new one under the same
   Mac-path fakes, in 8 cases: bare, `--submit`, each of the three reads refused, lander exit 3, a
   preset GitHub token, and a different broad identity. I compared the exit code, the output, and
   every log (BWS reads with their tokens, `gh`, pings, the lander's argv and environment). Result:
   0 differences. To check that the comparison can fail, I mutated one Mac line (`gh auth token`
   given an extra flag): it reported 7 differences, then 0 after I restored the line. The script
   was `/tmp/w206/diff_mac.py`, not committed, because the old launcher is gone after merge.
3. **Tests:** no Mac-path launcher test existed for this lane. I found no test of
   `run-inert-landing.sh` beyond the edge table. So `test_run_inert_landing.py` adds Mac-path tests:
   the identities and `gh` as before, nothing minted, preset overrides honoured, and the exit code
   passed through. The existing `tests/inert_lander/` tests (136, plus the 8 new ones beside them) pass unedited.

## Report, don't fix

### The paired pass

- **Before cut-over:** the container's schedule is off (`LANE_SCHEDULE_ENABLED` defaults to
  `false`), so the container runs only one-off `lane-run` execs. A bare pass asks for nothing:
  `_pass`, `_branch_updates`, `_revert_pass`, and `settle_unknown_outcomes` take `submit=False`,
  and held and revert alerts sync only on `--submit`. So it writes nothing to the orchestrator,
  GitHub, alert-hub, or its volume. It can't submit beside the Mac's hourly `--submit` job.
- **The paired run:** both hosts run a bare pass in the same minute (ADR ~1177-1178). I recommend
  running the pair away from minute 35. A bare run doesn't race the Mac's submit, but the Mac's
  submit can land a pull request between the two reads and change the compared identifiers.
  `deploy.md` step 8 says this.
- **Pings:** until the ping-URL secret exists, the container pings nothing. With no
  `SDS_DEADMAN_PING_URL_UUID`, no broad identity, and no Keychain in the container, the name lookup
  logs one reason and stops, so it never reads the Healthchecks key `51509d5e-…`.
  `test_with_a_ping_url_named_the_healthchecks_key_is_never_read` covers the named case. After the
  secret exists, both bare runs ping `sds-inert-landing`, which the cut-over procedure accounts for.
- **Cut-over:** the ordering keeps the two hosts from landing together. Cut-over step 2 boots out
  and disables the Mac agent while the container's schedule is still `false`. Only then does step 3
  set `--schedule true`. Nothing in code enforces this; the ordering in the ADR and in `deploy.md`
  does.

### Callers of the orchestrator SYSTEM credential the lane would join

I reconciled the callers against the credential's registry entry, `orchestrator-system-bearer`
in this repository's `.cred-consumers.toml`. In this repository, I verified each one by grep:

- `run-work-carrier.sh` and `run-rotation-decomposer.sh`, which already hold it in their lane
  containers.
- `run-activation-sweep.sh`, `run-bump-driver.sh`, `run-estate-landing.sh`,
  `run-inert-landing.sh`, and `run-tool-installer.sh`, on the Mac.
- `scripts/exit_probe.py` and `scripts/deploy_orchestrator.py`.

The registry also lists three callers in other repositories, which I cite from it without
verifying: infraops' `scripts/drift-audit.sh` (`mint-follow-ups`, 03:00), intent-packages'
operator-invoked `factory` CLI, and `factory-validation-kit-restart-recovery`'s `credentials.py`.
The Coolify entry is a sha256 hash, not a reader. The inert lane would be the third container
holder.

**Does the lane need SYSTEM?** On today's routes, yes. These calls refuse anything but SYSTEM
through `authorize_actor`:

- `POST inert-pr-merge` and `POST inert-pr-branch-update` (`lane_act.py`).
- `POST held-pr-close` (`held_close.py`).
- `POST landing-revert` (`revert_act.py`).
- `GET landing-watch` and `GET landing-unknown-outcomes` (`revert_watch.py`).

The bare pass makes both GET calls. `GET inert-pr-merge-admission` accepts any authenticated actor,
but it's the only such read. So even the reporting run needs SYSTEM, unless the watch reads gain a
read role. That role and a per-lane SYSTEM identity would be grants, so I didn't propose or make
them here.

### The kill switch `ORCHESTRATOR_INERT_LANDING_ENABLED`

Nothing in the container reads it. A grep of `src/`, `scripts/`, and `lanes/` outside
`src/orchestrator/` finds it only in comments and echo text: `com.devon.inert-landing.plist`
line 22 and `install-inert-landing-launchd.sh` lines 8 and 57. `src/orchestrator/config.py` reads
it as `settings.inert_landing_enabled`, and the inert routes consult that setting. It stays an
orchestrator variable and restart, as ADR-0059 says (392-397). It stops the container's acts the
same way it stops the Mac's, because both act only through those routes.

## Tests and checks

- `make check` (Postgres `orchestrator_test_hq_w_206`, `SECURITY_STANDARDS_DIR` at the fixture,
  `PGTZ=UTC`) on the committed tree: **15,433 passed, 1 skipped (15,434 collected)**, exit 0. ruff,
  ruff format, and pyright report 0 errors. A first run failed one test,
  `test_layer_manifest.py::test_every_pattern_names_a_file`, because the new test file was
  untracked; it passed after the commit.
- New tests: 38 in `test_run_inert_landing.py`, 8 in `test_repositories.py`. Each run passed.
- **Mutations** of the container branch (restored and checked with `cmp` afterwards): 11 tried, 10
  killed. Killed: lane token not unset before the lander; an extra `contents` permission; `gh` in
  the container; alert read on every pass; derivation run with the lane token; derivation exit code
  ignored; mint exit code ignored; state directory unchecked; activation run in the container;
  preset credentials not unset. The last one survived at first, which showed that a preset alert
  token on a bare pass was untested; I added that case and it's now killed. **1 survivor:** passing
  the token as `sds_bws_value`'s second argument. That mutant is equivalent: a shell-function
  argument reaches `bws` only through its environment, so no process argument carries it.
- `/code-review` (medium) on `origin/main...HEAD`: 6 findings. I applied 2: `run` and
  `repositories` now share `_landing_policy`, and the mint's failure message names the key read as
  a possible cause. I kept 4 as they are. Container detection by ambient `BWS_ACCESS_TOKEN`, the
  repeated per-secret pattern across launchers, and the hand-rolled subcommand dispatch are the
  conventions #733 and #734 established. The schedule-overlap ordering is the ADR's cut-over
  design. The first is a concern, in the following section.
- Security scanner (`security_scan.cli . --category security`, from the worktree root): 0 BLOCK,
  2 WARN, 1 INFO.

## Self-review

- The container block follows #734's shape: one block that exits, with Mac lines after it. It uses
  `echo FATAL >&2; exit N` with the dead-man trap, not the outcome-alarm `sds_outcome_fail`,
  because this lane arms the dead-man switch.
- The derivation runs before SYSTEM is read, so the subcommand's environment holds only the
  change-manager bearer (`test_the_list_is_derived_from_the_policy_credential_alone`).
- The policy is read twice per pass: once to derive the list, once by the pass. If a person edits
  the policy between the two reads, a repository the token doesn't name is reported `unreadable`
  (a finding, exit 3) for one pass. That fails closed.

## Concerns and follow-ups

1. **The container branch is selected by an ambient `BWS_ACCESS_TOKEN`,** as in every moved lane.
   Before this change, a Mac run with that variable exported used it as the narrow identity through
   `sds-token.sh`. Now that run takes the container branch: it skips activation and mints from the
   read App. launchd's job never sets it, so the scheduled pass is unaffected. A person running the
   launcher by hand from a shell that exports it gets the container path. A `lane-run`-set marker
   would remove the ambiguity, but that's a change to every lane, so it isn't in this scope.
2. **A mint refusal fails the whole pass.** If the read App isn't installed on one inert repository,
   or the inert population spans two owners, the mint refuses and the pass exits 1 (a `/fail`
   ping). On the Mac the same repository is one `unreadable` line (exit 3) beside the others. The
   first bare `lane-run inert-landing` at the `--add` proof is where the installation's coverage is
   proved. ADR-0059 Amendment 3 item 4 keeps the forks in the inert population, and Q9 installs the
   read App on each fork, so coverage is expected, but it's unproven until that run.
3. **No standard covers the shell-side duplication** of the read-check-FATAL pattern across 10
   launchers. If HQ wants a shared helper, it's a cross-lane change.

## Cut-over steps that remain

The same list is in `docs/operations/deploy.md`, in the inert lander's section.

1. The orchestrator serving this release; then HQ creates `sds-lane-inert-landing` from the
   compose without deploying it.
2. The VM root's keeper commands for the lane token and `inert-landing-ping-url`.
3. The lane's registry rows, in two changes (token, then ping URL), with a `runtime-fetch` reader
   for `scripts/run-inert-landing.sh` on the read App key's row.
4. **The lane account's grants on the four rows,** plus the ping-URL secret:
   - SYSTEM `221a48d5-…` (`SDS Operator`).
   - change-manager READ `314f276d-…` (`Rotation / Keepers`). The broad identity reads it on the
     Mac, so this becomes a direct grant, Devon's act at the lane's identity gate.
   - `sds-bridge` sender `b415d03b-…` (`SDS Operator`).
   - read App key `314a966c-…` (`SDS / Lane Apps`).
5. The token's mint and the executor's write; then the ping URL's row made due and its write.
6. A served release whose compose names the ping-URL id, with `inert-landing` removed from
   `placeholders`.
7. `--add`, then the `lane-run --read` proof over the five secrets, and a bare
   `lane-run inert-landing`: the first read mint, which proves the read App's installation.
8. Cut-over steps 2 onward:
   - Copy `sds/inert-landing/` into the volume before the first `--submit`.
   - Run the paired bare runs away from minute 35.
   - Boot out and disable the Mac job, then turn the schedule on.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** I added no log file. The
  launcher's lines go to stderr as every launcher's do, and the scheduler is supercronic with
  `-json`. The subcommand prints its data on stdout and its reason on stderr.
- **Code Brain rule 4 (never silently swallow):** every BWS read, the derivation, and the mint is
  checked, and each failure prints a named `FATAL` line and exits with a documented code. I added no
  `|| true`; the two that remain are in unchanged Mac lines (the broad Keychain read and `gh auth token`), each followed by an empty-value check. The subcommand turns
  `LandingPolicyError` into a printed reason and exit 2, never a silent empty list.
- **Code Brain rule 5 (never log secrets):** no value reaches output or argv. The token stays in the
  environment, and the App key goes from BWS straight to the mint's stdin. The minted token is held
  only in `INERT_LANDING_GITHUB_TOKEN`. `test_each_pass_reads_its_secrets_with_the_lanes_one_token`
  and `test_no_token_reaches_an_argument` assert that no value or token appears in output, `bws`
  argv, mint argv, the lander's argv, or ping argv.
- **Infra Brain 212 (HTTPS):** the lane has no FQDN, and every call is to an `https://` URL
  (change-manager, the orchestrator, alert-hub, and the GitHub API).
- **Infra Brain 591, 592 (no BWS token in tracked files or history):** I committed no token, and the
  scanner reports 0 BLOCK. Tests use placeholder strings (`a-lane-token`).
- **Infra Brain 593 (bootstrap token never inline):** the compose maps
  `BWS_ACCESS_TOKEN: ${INERT_LANDING_BWS_ACCESS_TOKEN}` from the resource variable only, and
  `add_refusals` passes.
- **Infra Brain 1032 (exposed credential rotates):** I surfaced no credential; I made no live read.
- **Infra Brain rule 2 (no secrets or `.env` committed):** I committed none.
- **Infra Brain rule 3 (no `:latest`):** the image is `sds-lanes:${SDS_LANES_IMAGE_TAG}`.
- **Infra Brain rule 4 (no Coolify scheduled task beside an in-container scheduler):** the schedule
  is the container's own `crontab`, and I created no Coolify task.
