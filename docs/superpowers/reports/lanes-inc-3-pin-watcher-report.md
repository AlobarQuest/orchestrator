Outcome: done

# Report: the pin watcher's lane files and launcher (ADR-0059 increment 3, step 3e-5)

Worker W-100, branch `hq/lanes-inc-3-pin-watcher`, base `origin/main` at `7dd01201`. Nothing was
merged and no production act was taken: no mint, no BWS read, no GitHub, Coolify, orchestrator,
alert-hub or Healthchecks call, and no `launchctl`.

## What changed

| File | Change |
|---|---|
| `lanes/pin-watcher/compose.yaml` | New. One service, `command: ["pin-watcher"]`, `BWS_ACCESS_TOKEN: ${PIN_WATCHER_BWS_ACCESS_TOKEN}`, `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`, `PIN_WATCHER_STATE_DIR: /var/lib/sds/pin-watcher` on the named volume `sds-lanes-pin-watcher-state`, `SDS_DEADMAN_PING_URL_UUID: ""` with the #655-style comment, `restart: unless-stopped`. No ports, networks or labels. |
| `lanes/pin-watcher/crontab` | New. `25 7 * * * lane-run pin-watcher`. |
| `scripts/run-pin-watcher.sh` | Container branch added (described in the following sections). Mac identity read moved from `sds-token.sh` to a direct Keychain read (see "Concerns"). |
| `.bws-secrets.toml` | `"pin-watcher"` added to the `lanes` of the observer bearer, the `sds-bridge` alert sender and the read-App key; the read-App key's comment names the launcher and says its grant comes at cut-over. |
| `.cred-consumers.toml` | One `runtime-fetch` reader for `scripts/run-pin-watcher.sh` on `lanes-read-app-private-key` (`healthchecks_check = "sds-pin-watcher"`); the block comment notes this reader lands ahead of its account's grant. No lane-token or ping-URL rows. |
| `tests/scripts/test_run_pin_watcher.py` | Rebuilt on the revision watcher's `env -i` harness: 39 tests, container and Mac. |
| `tests/scripts/test_deploy_lanes.py` | `pin-watcher` added to `placeholders`. |
| `docs/operations/deploy.md` | "Registered lanes": a `pin-watcher` paragraph with its remaining cut-over steps. "A lane's GitHub token": the pin watcher's mint and its two permissions. |
| `docs/operations/membership.md` | Names `run-pin-watcher.sh` as a `sds_scope ledger` reader. |

## The mint

In the container, each pass, a dry run included:

```sh
_lane_secret "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 --repository <owner/name>... \
      --permission metadata=read --permission contents=read
```

- **Key:** BWS `314a966c-…`, read with the lane's token and piped straight to the mint's stdin.
  It's never in a variable, an argument or a file.
- **Permissions:** `metadata=read` and `contents=read`. Every call in `src/pin_watcher/github.py`
  is a GET:
  - Contents: the recommendation file, each caller file, a compare and a commit.
  - Metadata: `/repos/{r}` and the `/users/{login}/repos` or `/orgs/{login}/repos` listing.
  - Public (needs no permission): `/users/{login}`.

  No call needs more.
- **Repositories:** the launcher runs `SDS_SCOPE_ORCHESTRATOR_TOKEN="$ORCHESTRATOR_API_TOKEN" env
  -u BWS_ACCESS_TOKEN .venv/bin/python -m scripts.sds_scope ledger`. That prints
  `observed(members)` (onboarding and member), the population `cli.py` uses. It runs with the
  OBSERVER bearer the lane already holds. No list is typed in the shell and no owner name is in
  code. The mint never names a public non-member, and `factory-runner` (a member) is in it like
  any member.
- **Preset value:** any preset `PIN_WATCHER_GITHUB_TOKEN` is replaced by the minted one.
- **No `gh`:** the container never calls `gh auth token`. `BWS_ACCESS_TOKEN` is unset before
  `pin-watcher` runs, on both hosts.

## Failure rules as built (container)

| Condition | Result |
|---|---|
| Observer bearer unread (any pass, a dry run included, since the derivation needs it) | `FATAL` exit 1 before the derivation |
| Alert sender unread on an acting pass | `FATAL` exit 1 before the derivation |
| `sds_scope` exits 3, or exits 0 with nothing printed | exit 3; nothing minted or watched; the key is never read |
| `sds_scope` exits any other non-zero code (1, 126, 127, 137) | that code, passed through; nothing minted |
| Mint fails (including a second-owner refusal, open item 4) or prints nothing | `FATAL: could not mint … (sds-github-mint exited N)` exit 1; nothing watched |
| Program exits outside {0,1,2,3} | exit 1, as before |

On the Mac, the following behavior is unchanged:

- The alert sender is read only on an acting pass, and exit 1 if it's empty.
- The observer bearer is fatal only on an acting pass.
- The GitHub credential is `PIN_WATCHER_GITHUB_TOKEN`, else `gh auth token`, and exit 1 if it's
  empty.
- The exit codes fold as before, and the dead-man is armed with `sds_deadman_arm sds-pin-watcher
  --finding 2`.

## Crontab minute

`25 7 * * *`, in the image's `TZ=America/New_York` (`lanes/Dockerfile` line 78), the same zone as
the landing ledger's `30 7 * * *`. The Mac's 07:20 would clash with the deploy watcher's hourly
`:20`. Minute 25 is used by no other lane: execution watch runs at 0, 15, 30 and 45; the unit
verifier at 2–52/10; the deploy watcher at 20; the revision watcher at 40; the landing ledger at
30. `test_no_two_lanes_start_a_pass_in_the_same_minute` holds it.

## The dead-man switch with no ping URL

The compose leaves `SDS_DEADMAN_PING_URL_UUID` empty, so `sds_deadman_arm` falls back to the
Mac's lookup by check name. That lookup needs the broad Keychain identity the container doesn't
have. A container pass therefore pings nothing and logs `no SDS_DEADMAN_PING_URL_UUID and no broad
BWS identity — alerting disabled this run`; the pass itself still runs
(`test_without_a_ping_url_secret_a_pass_pings_nothing`). With a ping-URL id set, the tests show
`/start` then success, or `/fail` on exit 1 or 3. The schedule stays off (`LANE_SCHEDULE_ENABLED`)
until cut-over.

## Tests and checks

- `make check` with `TEST_DATABASE_URL` on `orchestrator_test_hq_w_100`: green. **14,621 passed,
  1 skipped**, "All checks passed!".
- `tests/scripts/test_run_pin_watcher.py`: 39 tests. They cover the following:
  - The mint's exact arguments and the repositories passed to it.
  - Every read using the one lane token, and `gh` never asked in the container.
  - Activation skipped, and a preset token replaced.
  - A container dry run calling the derivation and the mint, with no alert fetch.
  - The failure rules in the preceding table.
  - The Mac path: identities, `gh` login, dry-run leniency, preset token kept.
  - Exit folding on both hosts.
  - The compose, volume and state variable; the crontab and plist time; the minute clash; the
    manifest's three secrets.
- Mutation proof, each restored by inverse edit, with an empty `git diff` after:
  1. I reduced the derivation check to `[ -z "$REPOSITORIES" ]`, so an exit 3 with output would
     mint. `test_a_derivation_exiting_3_with_output_still_mints_nothing` failed.
  2. I added `--permission actions=read` to the mint. The exact-arguments test and the dry-run
     test failed.
- `/code-review` (medium): four findings, all by design. See "Self-review".

## Self-review

The `/code-review` pass raised the following four points. I made no code change for any of them:

1. **A set `BWS_ACCESS_TOKEN` alone means "container".** This is the same discriminator as the
   landing ledger, the revision watcher and the unit verifier. launchd's plist sets only `PATH`
   and `HOME`.
2. **The mint covers observed members only, not public non-members.** This is as the handoff
   specifies. Whether the token can read public non-members is open item 2, settled by the
   cut-over dry run (described in the following sections).
3. **The derive-then-mint block is duplicated across launchers.** It's a deliberate mirror of the
   model. A shared helper would be a cross-launcher refactor, out of scope.
4. **The empty ping-URL placeholder.** This is required by the handoff and guarded by
   `placeholders`.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** the launcher adds no logging system. It
  prints one-line `FATAL`/derivation messages to stderr like every sibling launcher; the
  program's own logging is unchanged.
- **Code Brain 4 (no silent swallow):** no `|| true` on the mint or a BWS read in the container
  path. The mint's exit code is captured, and a failure logs `sds-github-mint exited N` before
  exit 1. A BWS read failure leaves the value empty, which a named `FATAL` line reports. The one
  `|| true` left is the Keychain read on the Mac, followed by an explicit empty check and `FATAL`.
- **Code Brain 5 (no secrets in logs):** no message carries a token, bearer or key. The key goes
  through a pipe only. A test asserts that the lane token, minted token, observer bearer and alert
  token appear nowhere in stdout or stderr.
- **Infra 212 (HTTPS):** the lane has no FQDN; it calls `https://sds.alobar.net` (the default
  `ORCHESTRATOR_API_URL`) and the mint and reader call `https://api.github.com`.
- **Infra 591, 592 (no BWS token tracked):** no token in any file; only UUIDs and variable names.
- **Infra 593 (bootstrap token never inline):** the compose maps `BWS_ACCESS_TOKEN` from
  `${PIN_WATCHER_BWS_ACCESS_TOKEN}` only. The script reads it from the environment.
- **Infra 1032 (exposed credential rotates):** nothing was exposed; no secret was read in this
  session.
- **Deployment (in-container scheduler only):** the schedule is `lanes/pin-watcher/crontab`; no
  Coolify scheduled task.

## Concerns and follow-ups

- **The Mac identity read changed form, though not identity or behavior.** The Mac path used to
  source `sds-token.sh`, which respects a set `BWS_ACCESS_TOKEN`. Now a set value means the
  container. So the launcher reads the same Keychain item (`Claude`/`BWS_ACCESS_TOKEN_SDS`)
  directly into an unexported variable, with the `BWS_ACCESS_TOKEN_SDS` override, as the landing
  ledger and revision watcher do. The secrets read, the identity and the `gh` fallback are
  unchanged. An ad-hoc Mac run with `BWS_ACCESS_TOKEN` exported now takes the container path;
  use `BWS_ACCESS_TOKEN_SDS` instead.
- **The mint's repository list includes onboarding repositories.** `observed` covers onboarding
  repositories as well as members, but the read App is installed on the 14 members. An observed
  repository outside the installation makes the mint refuse, so every pass exits 1. The landing
  ledger already mints from the same `sds_scope ledger` list, so I expect it to hold. The cut-over
  dry run proves it.
- **The pin watcher's account holds no grant yet.** Its runtime-fetch reader on the read-App key
  lands before the grant. That is the order the handoff asks for; the other four readers had
  grants when they landed.

## Cut-over steps that remain

These are also in `docs/operations/deploy.md`, "Registered lanes".

1. The orchestrator serving this release; then HQ creates `sds-lane-pin-watcher` from the compose
   without deploying it.
2. The VM root's keeper commands for the lane token and `pin-watcher-ping-url`.
3. The lane's registry rows: the token's row and the ping URL's row, in two changes.
4. **Grants:** the lane account's grants on its three secrets (observer bearer, `sds-bridge` alert
   sender, read-App key) and its ping-URL secret.
5. The token's mint and the executor's write; then the ping URL's row made due and its write.
6. A served release whose compose names the ping-URL secret's id, with `pin-watcher` removed from
   `placeholders`.
7. The `--add`, the `lane-run --read` proof, and a container `--dry-run`. **What the dry run must
   show (amendment 4, open item 2):**
   - It derives and mints, with no `gh`.
   - With the minted token it reads a public non-member's caller file.
   - It lists through each account-type endpoint (`/users/{login}/repos` for a user,
     `/orgs/{login}/repos` for an organization).
   - The mint succeeds over the full observed list, which proves one owner and installation
     coverage, onboarding repositories included.
   - It exits 0 or 2, not 3.

   If a public read fails there, the anonymous fallback (out of scope here) is the decision to
   make.
8. **State copy:** copy the Mac's `~/Library/Application Support/sds/pin-watcher/alerts.json`
   into `sds-lanes-pin-watcher-state` before the first acting pass, or the alerts the Mac left
   firing never resolve. Then the paired reporting runs, `LANE_SCHEDULE_ENABLED=true`, and the Mac
   job's retirement.
