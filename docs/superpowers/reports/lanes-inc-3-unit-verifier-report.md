Outcome: done

# Build report: the unit verifier's lane files and launcher (ADR-0059 increment 3, step 3e-4)

Branch `hq/lanes-inc-3-unit-verifier`, based on `origin/main` at `270eec42`. This change has
no production act. It makes no live mint, no BWS read, no GitHub, Coolify, VM, Healthchecks or
orchestrator call, and no `launchctl`.

## What changed

The following files changed:

- `lanes/unit-verifier/compose.yaml` (new): one service, `command: ["unit-verifier"]`,
  `BWS_ACCESS_TOKEN: ${UNIT_VERIFIER_BWS_ACCESS_TOKEN}`, `LANE_SCHEDULE_ENABLED:
  ${LANE_SCHEDULE_ENABLED:-false}`, `VERIFIER_STATE_DIR: /var/lib/sds/unit-verifier` on the named
  volume `sds-lanes-unit-verifier-state`, an empty `SDS_DEADMAN_PING_URL_UUID` placeholder
  explained in the header comment, and `restart: unless-stopped`. It has no ports, networks or
  labels.
- `lanes/unit-verifier/crontab` (new): `2-52/10 * * * * lane-run unit-verifier`.
- `scripts/run-unit-verifier.sh`: the container branch (the following sections describe it). The
  Mac path is unchanged.
- `src/unit_verifier/cli.py`: the `unit-verifier repositories` subcommand
  (`check_filing_repositories`, `print_repositories`). A bare `unit-verifier` and `--dry-run` run
  as before.
- `.bws-secrets.toml`: `"unit-verifier"` added to `lanes` on `orchestrator-verifier-bearer` (the
  field is new on that row), `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` and
  `lanes-read-app-private-key`, with their comments updated.
- `layers.toml`: the two new test files placed beside the unit verifier's other files.
- `docs/operations/deploy.md`: "Registered lanes" names the unit verifier's files and the cut-over
  steps that remain. "A lane's GitHub token" names its mint and its failure rule.
- Tests:
  - New: `tests/scripts/test_run_unit_verifier.py` and `tests/unit_verifier/test_repositories.py`.
  - `tests/unit_verifier/test_launcher.py` now runs the Mac path through a stand-in
    `sds-token.sh`. It used to set `BWS_ACCESS_TOKEN`, which now means the container.
  - `tests/architecture/test_verification_edges.py`: the lane's bearers gain the read-App key.
  - `tests/scripts/test_deploy_lanes.py`: `unit-verifier` joins `placeholders`.
  - `tests/scripts/test_run_revision_watcher.py`: its cross-lane minute check uses the shared
    `lane_start_minutes` parser. Its own `int(field)` couldn't read `2-52/10`.

I didn't change `.cred-consumers.toml`. In the model, #655 added no reader row, and the deploy
watcher's read-App `runtime-fetch` reader landed at its cut-over (#656). So the reader for this
launcher is cut-over step 3 in the following list.

## The mint

In the container, each pass runs the following:

```
sds_bws_value 314a966c-5654-4e3c-8358-b4fd01268775 \
  | env -u BWS_ACCESS_TOKEN .venv/bin/sds-github-mint \
      --app-id 5684758 --repository <each derived repository> \
      --permission metadata=read --permission administration=read
```

- **Permissions.** `administration=read` is what `GET
  /repos/{repository}/branches/main/protection/required_status_checks` needs. It's this lane's one
  GitHub call (`required_checks.py`), and ADR-0059 decision 2 (~626-631) names Administration
  read for it. `metadata=read` is there because `sds-github-mint` refuses a request that omits it
  (`github_app_mint.py` `_check_request`): GitHub grants metadata with every token. The App holds
  both, so this needs no new permission.
- **Repositories.** `unit-verifier repositories` reads `GET /api/v1/members` through
  `VerifierClient.members()` with the lane's own VERIFIER bearer. It doesn't use `sds_scope` or the
  observer bearer. It prints the live members' repositories granted `dependency_updates` or
  `app_changes`, the services `service_for_change_class` returns for a bump and for an app change.
  Each repository appears once by casefolded name, first spelling kept, because the mint refuses a
  name twice. An unreadable or empty record, or no repository granted either service, prints
  nothing on stdout and exits 3. It never prints a shorter list. No list is typed in the shell and
  no owner name is in the code.
- **Key handling.** The App's key goes from the BWS read straight to the mint's stdin. The mint and
  the derivation run without the lane's token, and `BWS_ACCESS_TOKEN` is unset before the pass. Any
  preset `ROTATION_VERIFIER_GITHUB_TOKEN` is cleared in the container, and `gh` is never asked
  there.

## The failure rule as built

A missing GitHub token never stops the pass, as the handoff states. ADR-0059 says nothing
different for this lane.

- If the derivation fails (any non-zero exit, or empty output), the launcher logs one line
  ("the verifier's repositories could not be derived (exit N); nothing was minted and this pass
  runs with no GitHub token"). It doesn't read the App key and doesn't mint.
- If the mint fails (non-zero, empty answer, or an unreadable key), the launcher logs one line
  ("could not mint the verifier's GitHub token from the read-only App (exit N); this pass runs
  with no GitHub token").
- Either way the pass runs with `ROTATION_VERIFIER_GITHUB_TOKEN` empty. Then the launcher reports
  3, which pings `/fail`: a program exit of 0 or 2 becomes 3, and 3 stays 3. A program exit of 1,
  or of any undescribed code (folded to 1 as before), stays 1, because the tool failing outranks
  an incomplete pass. Neither log line carries a token.
- The alert-token rule, the exit folding and `sds_deadman_arm sds-unit-verifier --finding 2` are
  as before. The header's `EXIT CODES` block gains one continuation line under 3.

The pass's behavior with no token differs from the handoff's wording. The handoff says
check-filing units "wait or go to a person". In the code, `_required_names` raises
`VerifierCallError` when there's no token, and `run_pass` records it as `refused`. So in a
tokenless pass every bump and every app-change unit with an `automated_check` criterion is held
unverified as `verify-refused`, with its keyed alert, and the next pass tries again. Rotation units,
and app-change units with no `automated_check` criterion, verify normally. I documented the
behavior as the code has it and didn't change the program. The Mac has the same behavior when
`gh` has no login.

## Crontab minutes

`2-52/10` runs minutes 2, 12, 22, 32, 42 and 52, every ten minutes, as the Mac job's `StartInterval`
of 600 does. No other lane starts in those minutes: execution watch runs on the quarter-hours, the
deploy watcher at :20, the revision watcher at :40 and the landing ledger at 07:30. The Mac job
passes no arguments, so the line has no flags. Supercronic (robfig's parser) accepts the range-step
form. I couldn't run `supercronic -test` locally because it isn't installed. The image build runs
it over every lane's crontab (`lanes/Dockerfile` 76), so the release build checks it.

## The dead-man switch with no ping URL

The compose leaves `SDS_DEADMAN_PING_URL_UUID` empty. So `sds-deadman.sh` falls back to the Mac's
lookup by check name, which needs a broad identity that the container doesn't have. A pass then
pings nothing and logs `no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity — alerting disabled
this run`. `test_without_a_ping_url_secret_a_pass_pings_nothing` pins this. The schedule is off
(`LANE_SCHEDULE_ENABLED` defaults to `false`) until cut-over.

## Tests and checks

- `make check` passed with exit 0 against `orchestrator_test_hq_w_84`: lint "All checks passed!"
  and **14363 passed, 1 skipped**.
- The new launcher tests cover the following:
  - The mint's exact arguments and repositories.
  - All four BWS reads (ping URL, bearer, alert sender, App key) with the lane's one token.
  - That `gh` is never asked in the container, and that activation is skipped there.
  - That a preset token is replaced.
  - Dry run, and a failed alert-token fetch that still runs the pass.
  - That an unreadable bearer exits 1.
  - Three derivation failures and three mint failures, each with no mint (where it applies), a
    tokenless pass, exit 3 and a `/fail` ping.
  - The fold table for a tokenless pass, and the exit codes on both hosts.
  - The Mac path (the `sds-token.sh` identity, `gh auth token`, no mint), the compose, the state
    variable, the crontab and the manifest.
- The new subcommand tests run through `main(["repositories"])`. They cover the union and casefold
  dedupe (retired, withdrawn and other-service members excluded), four unreadable answers with exit
  3 and empty stdout, no granted repository with exit 3, a missing bearer with exit 1, the service
  literals held to `service_for_change_class`, and a bare `unit-verifier` and `--dry-run` still
  parsing.
- I ran a mutation check on the launcher. All five mutations were caught by
  `test_run_unit_verifier.py`, and the launcher was restored by file copy:
  1. No exit-3 promotion.
  2. A preset token kept in the container.
  3. The lane token kept for the pass.
  4. `administration=read` dropped.
  5. The launcher exits on a derivation failure.
- `shellcheck scripts/run-unit-verifier.sh` reports nothing. The security scanner (`--category
  security`) has no BLOCK findings.

## Self-review

- The container is detected by a set `BWS_ACCESS_TOKEN`, as in the model. On the Mac launchd sets
  only `PATH` and `HOME`, so the Mac path still sources `sds-token.sh`. One behavior changes on
  the Mac: an ad hoc run with `BWS_ACCESS_TOKEN` preset now takes the container branch. The model
  has the same property.
- `unset BWS_ACCESS_TOKEN` happens in the container only, so the Mac path stays byte-for-byte what
  it was (apart from comments).
- I didn't add a module-level collection of service names. The two service names are a local
  literal that `granted()` validates against `SERVICES`, and a test holds them to
  `service_for_change_class`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files).** The launcher writes plain
  stderr lines, as every sibling launcher does. The container's entrypoint and supercronic own the
  log stream, and nothing here writes a log file. This launcher doesn't add JSON logging. It matches
  the model, which also writes plain lines.
- **Code Brain rule 4 (never silently swallow).** The derivation and the mint have no `|| true`.
  Each failure sets `GITHUB_TOKEN_MISSING`, logs one line, and makes the launcher report 3. The
  existing alert-token `|| true` is followed by its logged warning, and the program's failed
  alert-hub send exits 3, as before. In `print_repositories` each caught exception is printed and
  exits 3.
- **Code Brain rule 5 (never log secrets).** No log line holds a token, key, bearer or header. Tests
  assert that the lane token, the minted token, the bearer and the alert token don't appear in
  stdout or stderr. The key reaches the mint only on stdin.
- **Infra Brain 212 (HTTPS).** The lane has no FQDN. It calls `https://sds.alobar.net` and
  `https://api.github.com`, unchanged.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history).** No token is in any file.
  The compose names `${UNIT_VERIFIER_BWS_ACCESS_TOKEN}` only. The scanner has no BLOCK findings.
- **Infra Brain 593 (no inline bootstrap token).** The compose maps `BWS_ACCESS_TOKEN` from the
  resource variable only, and the script never assigns it a value in the container.
- **Infra Brain 1032 (exposed credential rotates).** I surfaced no credential, so nothing needs
  rotation.
- **Deployment (no Coolify scheduled task beside an in-container scheduler).** The schedule is the
  tracked crontab that supercronic runs. This change creates no Coolify task.

## Concerns and follow-ups

- A tokenless pass alerts once for each check-filing unit as `verify-refused`, as described in
  "The failure rule as built". So a mint outage gives one `/fail` from the check plus one alert for
  each such unit. That's loud but not wrong. A follow-up could map "no GitHub token" to
  `CHECKS_UNREADABLE`, a closer description, but that would change the program's alert kinds,
  which is out of this scope.
- The App's installation covers the 14 members. Only the cut-over dry run proves that it covers
  every repository granted `dependency_updates` or `app_changes`. A repository outside it makes
  the mint refuse, and every pass then runs tokenless and exits 3.
- The mint names at most `MAX_REPOSITORIES` (500) repositories. The granted population is far
  below that.
- I couldn't test `supercronic -test` locally. The image build covers it.

## Cut-over steps that remain

These steps are in `deploy.md`, in order, each its own change or act:

1. The orchestrator serves this release. Then HQ creates `sds-lane-unit-verifier` from the compose
   without deploying it.
2. The VM root runs the keeper commands for the lane token and `unit-verifier-ping-url`.
3. The lane's registry rows: the token's row and the ping URL's row, in two changes. In the same
   work, the read-App key row's `runtime-fetch` reader for `scripts/run-unit-verifier.sh` in
   `.cred-consumers.toml`.
4. The lane account's grants on its three secrets (the VERIFIER bearer, the alert sender and the
   read-App key) and its ping-URL secret.
5. The token's mint and the executor's write. Then the ping URL's row is made due, followed by the
   executor's write.
6. A served release whose compose names the ping-URL secret's id, with `unit-verifier` removed
   from `placeholders`.
7. The lane's `--add`, then the `lane-run --read` proof and a `--dry-run` in the container. The dry
   run also proves the installation covers the derived repositories.
8. The copy of the Mac's `sds/verifier/alerts.json` into `sds-lanes-unit-verifier-state` before the
   first pass in the container. Then the paired reporting runs, the schedule, and the Mac job's
   retirement.
