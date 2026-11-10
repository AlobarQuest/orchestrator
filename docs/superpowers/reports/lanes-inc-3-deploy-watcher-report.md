Outcome: done

# Report: the deploy watcher's lane files and launcher (ADR-0059 increment 3, step 3e-2)

Worker W-80, branch `hq/lanes-inc-3-deploy-watcher`, base `origin/main` at `1f74921f`. The PR is
open and ready, not merged.

## What I built

This change gives the deploy watcher its own container path, modeled on the landing ledger's
(#643). The release that carries it is the one cut-over step 1 needs before HQ creates
`sds-lane-deploy-watcher`.

## Files changed

- `lanes/deploy-watcher/compose.yaml` (new): one service with the following settings:
  - `command: ["deploy-watcher"]`, the lane the entrypoint and `lane-run` expect.
  - `BWS_ACCESS_TOKEN: ${DEPLOY_WATCHER_BWS_ACCESS_TOKEN}`.
  - `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`.
  - `SDS_DEADMAN_PING_URL_UUID: ""`, with a comment that explains the placeholder.
  - `restart: unless-stopped`. No volume, ports, networks, or labels.
- `lanes/deploy-watcher/crontab` (new): `20 * * * * lane-run deploy-watcher`.
- `scripts/run-deploy-watcher.sh`: adds the container branch, as described in the following
  sections. The Mac path behaves as before.
- `src/deploy_watcher/cli.py`: adds the `deploy-watcher repositories` command. It prints the sorted
  keys of `ROLLOUT_WORKFLOWS`, one per line. On an empty table it prints nothing and exits 3.
- `.bws-secrets.toml`:
  - Adds `deploy-watcher` to the `lanes` of the OBSERVER row (`f793576f-…`) and the read-App key
    row (`314a966c-…`).
  - Adds `lanes = ["deploy-watcher"]` and a comment to the change-manager `M2M_TOKEN_OBSERVE` row
    (`3b9503da-…`).
- `tests/scripts/test_run_deploy_watcher.py` (new): 20 tests, covering the container branch, the Mac
  path, the compose, the crontab, and the manifest.
- `tests/deploy_watcher/test_cli.py`: three tests for `repositories`. One feeds the derived list
  through the mint's own `_check_request` with the four permissions.
- `tests/scripts/test_deploy_lanes.py`: restores the `placeholders` set, as `{"deploy-watcher"}`.
  #643 had added it for the ledger and #646 removed it. An empty ping-URL id is allowed only for a
  lane named there.
- `layers.toml`: registers the new test file beside the deploy watcher's launcher, in `sensors`.
- `docs/operations/deploy.md`: "Registered lanes" names the deploy watcher and the cut-over steps
  still to come, and "A lane's GitHub token" names its mint and derivation.

`lanes/Dockerfile` and `scripts/lane_run.py` don't change. The image copies `scripts/run-*.sh`
and `lanes/`. Both `deploy-watcher` and `sds-github-mint` are console scripts in the orchestrator
venv (`pyproject.toml`), so they're at `$REPO_ROOT/.venv/bin/` in the image. The derivation is a
subcommand of `deploy-watcher`, so it needs no new file in the Dockerfile's copy list.

I didn't change `.cred-consumers.toml`. The handoff makes that edit conditional on #643 having
added a `runtime-fetch` reader for `run-landing-ledger.sh` on the read-App key's row. `git log -S`
shows #644 added that row, not #643, so the condition is false. The reader is listed as a cut-over
item.

## The container branch

A set `BWS_ACCESS_TOKEN` selects the container path, as in the ledger. In order, the launcher does
the following:

1. Skips activation, which is the Mac's alone.
2. Arms the dead-man switch, unchanged: `sds_deadman_arm sds-deploy-watcher --finding 2 "$@"`.
3. Reads both bearers with the lane's one token, in the environment: `3b9503da-…` into
   `DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN` and `f793576f-…` into
   `DEPLOY_WATCHER_ORCHESTRATOR_TOKEN`.
4. Derives the repositories with `env -u BWS_ACCESS_TOKEN deploy-watcher repositories`.
5. Mints `DEPLOY_WATCHER_GITHUB_TOKEN`. Any preset value is replaced.
6. Runs `unset BWS_ACCESS_TOKEN`, then `watch "$@"` and `recheck`. The order and the exit-code
   folding are unchanged.

The container never asks `gh`. A failed or empty mint exits 1 before `watch` runs.

### The derivation's exit codes

The derivation mirrors the ledger's handling of `sds_scope`:

- Any code other than 0 or 3 passes through verbatim, for example 127.
- Exit 3, or empty output with exit 0, exits 3 with no mint and no key read.

The derivation can't be partial: its source is a table in the code, not a read. So unlike the
ledger, an exit 3 never mints for part of a list.

## The mint's exact arguments and repositories

The launcher pipes the key straight from BWS into the mint:

```sh
sds_bws_value 314a966c-5654-4e3c-8358-b4fd01268775 \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 \
      --repository alobarquest/brain --repository alobarquest/change-manager \
      --permission metadata=read --permission contents=read \
      --permission pull_requests=read --permission actions=read
```

The repositories are the sorted keys of `ROLLOUT_WORKFLOWS`, the only list of them. The key reaches
the mint's stdin in its stored form and is never put in a variable, an argument, or a file. The
mint runs without the lane token.

**Lowercase keys.** The table's keys are lowercase, and I pass them as printed. The mint's regex
accepts them, its owner check is casefolded (one owner, so one installation), and its echo check
casefolds GitHub's `full_name`. GitHub resolves `/repos/{owner}/{name}` without regard to case.
`test_the_derived_repositories_pass_the_mints_request_checks` pins the request side against the
real table. The live answer, GitHub echoing these lowercase names back, is untested until the
cut-over's `--dry-run`.

**Permissions.** I checked every path in `src/deploy_watcher/github.py`:

- `metadata` and `contents`: `pulls/{n}`, `pulls`, `commits/{sha}`, `contents/{path}`, and
  `compare`.
- `pull_requests`: `pulls/{n}` and `pulls`.
- `actions`: `actions/workflows/{path}`, `…/runs`, and `actions/runs/{id}/attempts/{n}/jobs`.

All are GET requests and need no other permission, so no widening is involved.

## The crontab minute and why

I chose minute 20, `20 * * * *`. The cadence matches the Mac job's hourly `StartInterval 3600`,
which the test pins against the plist. Minute 20 avoids the top of the hour and the quarter-hours
that `execution-watch` uses (`*/15`). It also avoids the landing ledger's 07:30, so the three lanes
don't start together on the shared VM or against GitHub's rate limit.

## The dead-man's behavior with no ping URL

`SDS_DEADMAN_PING_URL_UUID` is empty, so `sds_deadman_resolve` falls back to the lookup by check
name. That lookup needs the broad identity. `lane-run` doesn't pass `BWS_ACCESS_TOKEN_BROAD`, and
the image has no `/usr/bin/security`, so the identity comes back empty.

The helper then logs `no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity — alerting disabled
this run`, pings nothing, and doesn't gate the pass. `test_without_a_ping_url_secret_a_pass_pings_nothing`
holds the outcome: exit 0, no ping, and both programs run. The schedule stays off
(`LANE_SCHEDULE_ENABLED` defaults to `false`) until the cut-over enables it.

## Tests and checks

- `make check`: green (`rc=0`), with ruff, ruff format, pyright, and pytest. **14,276 passed, 1
  skipped**, against `orchestrator_test_hq_w_80`, migrated to head, on the committed tree.
  An earlier run, before the first commit, failed only `test_every_pattern_names_a_file`, because
  the new test file wasn't yet tracked. It passed after the commit.
- New launcher tests: 20, all passing. The `bws` stub logs the actual token per UUID, so the tests
  distinguish the container's one lane token from the Mac's broad and narrow identities.
- Mutation run against the launcher: ten mutations, all caught. Each was restored from the commit.
  - The mint keeps the lane token.
  - No `unset` before `watch`.
  - A `gh` fallback in the container.
  - An empty list still mints.
  - Lost pass-through of codes other than 0 and 3.
  - The Mac observer bearer read with the broad identity.
  - A preset GitHub token kept in the container.
  - Activation run in the container.
  - `recheck` given the launcher's arguments.
  - The derivation keeps the lane token.

  A first attempt at the preset-token mutation replaced a line with itself, which made it a no-op.
  I reran it as a real change, and it was caught.
- `/code-review` (medium) on `origin/main...HEAD` returned five points, with no correctness defect
  that needs a change:
  - The ambient `BWS_ACCESS_TOKEN` on the Mac: see "Concerns and follow-ups".
  - Exit 3 on a failed derivation pinging `/fail`: intended by the handoff.
  - The order of `mint_rc=$?`: correct, and the same as the ledger's.
  - The hand-kept `placeholders` set: the handoff asks for it.
  - The empty ping URL once the schedule is enabled: a cut-over ordering point, covered in
    `deploy.md`.

Shellcheck ran on the launcher as part of `make check` (the repo has a `.shellcheckrc`) and on its
own, and it's clean.

## Self-review

- The launcher keeps its `--dry-run` handling, the `watch`-then-`recheck` order, the exit
  folding, and the dead-man arm unchanged. The tests cover the folding cases 2, 3 over 2, 1 over 3,
  and 127.
- The Mac path is unchanged. It reads the two Keychain identities directly, keeps the per-credential
  preset overrides, and uses the `gh` login. Its one addition is an `unset` of
  `BROAD_IDENTITY`, `NARROW_IDENTITY`, and `BWS_ACCESS_TOKEN` before the programs run, and the
  programs read none of them.
- No new owner name went into the code. The repositories come from the one existing table.
- No value of a secret appears in any file, log line, or argument. The tests assert that the lane
  token, the minted token, and both bearers never appear on stdout or stderr.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production):** this change adds no log
  surface. The launcher's lines are the same plain lines the ledger's container branch prints,
  and supercronic (`-json`) wraps each one as JSON on the container's stdout.
- **Code Brain rule 4 (no silent swallow; no `|| true` hiding a failed mint or BWS read):**
  - The container path has no `|| true`.
  - A failed bearer read exits 1, naming the credential.
  - A failed derivation exits 3 or its own code, with a stderr line.
  - A failed key read or mint exits 1 with `FATAL: could not mint …`.
  - The only `|| true` lines are the Mac's existing Keychain reads and `gh auth token`. Each is
    followed by an empty check that exits 1.
- **Code Brain rule 5 (never log secrets):** values travel only through the environment and a pipe
  to stdin. Error lines name the credential, never its value. Four tests assert that no token
  appears in the output.
- **Infra Brain 212 (HTTPS for a public FQDN):** the lane has no FQDN. Its calls go to
  `https://sds.alobar.net`, `https://change-mgr.alobar.net`, and `https://api.github.com`, and all
  are unchanged.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history):** only UUIDs and the App id
  are tracked. The Stop hook's scanner gate runs at session end.
- **Infra Brain 593 (no inline bootstrap token):** the compose maps
  `${DEPLOY_WATCHER_BWS_ACCESS_TOKEN}` from the resource variable only.
- **Infra Brain 1032 (rotate an exposed credential):** I read and surfaced no credential, made no
  BWS, GitHub, Coolify, Healthchecks, or `launchctl` call, and minted nothing.
- **Deployment (no Coolify scheduled task beside an in-container scheduler):** the schedule is the
  tracked crontab that supercronic runs in the container, gated by `LANE_SCHEDULE_ENABLED`.

## Concerns and follow-ups

- **An ambient `BWS_ACCESS_TOKEN` on the Mac selects the container path.** This is the ledger's
  #643 design, and I mirrored it. It does undo one property the deploy watcher's earlier header
  described: a token exported in an operator's debugging shell was ignored, and the run succeeded.

  Now a manual Mac run from such a shell goes as follows:
  1. Activation is skipped.
  2. `sds_deadman_arm` resolves the live `sds-deploy-watcher` check by name, through the Keychain's
     broad identity, and pings `/start`. `SDS_DEADMAN_PING_URL_UUID` is unset on the Mac.
  3. Both bearers are read with the one ambient token. One read is denied, so the launcher exits 1
     with a `FATAL: could not read … from BWS` line that names BWS, not the cause.
  4. The EXIT trap pings `/fail` on the live check.

  A `--dry-run` arms nothing, so it pings nothing. Under launchd nothing is exported, so the
  scheduled job is unaffected.

  I left the selector unchanged, because the handoff names it and the ledger shares it. If HQ
  wants a dedicated marker, for example a variable that `lane-run` sets, that's a change to both
  launchers and `lane_run.py`, which I'd do in its own PR.
- **`recheck` isn't gated on the rollout table.** It re-derives every stored observation on
  `record.target_repository`. Today every stored row comes from `watch`, which refuses
  repositories outside the table, so all rows name a table repository.

  If a repository leaves `ROLLOUT_WORKFLOWS` while its rows remain, the narrowed token can't read
  it. That recheck reports `[incomplete]` and the hour exits 3. Under `gh` it would still have
  read. This is honest, not silent, but removing a table entry now carries that consequence.
- **The table's owner names.** `ROLLOUT_WORKFLOWS` keys carry the owner name. This change adds no
  new owner name to code, as the standing answer requires: every test reads the table. Moving those names out of the code is
  follow-up work, not part of this PR.
- **The live echo of lowercase names** is first exercised at the cut-over's `--dry-run`. The code
  path casefolds both sides, and the request side is pinned by a test.

## Cut-over steps that remain

These follow the order of ADR-0059, "Cut-over for one lane", step 1, with ADR-0055 amendment 19
item 8's token and ping-URL order and amendment 23's ping-URL read. `docs/operations/deploy.md`
lists the same steps. Each is its own change or act:

1. The orchestrator serves this release, and the lanes image is built at this commit. HQ then
   creates `sds-lane-deploy-watcher` from `lanes/deploy-watcher/compose.yaml` without
   `instant_deploy`, with `LANE_SCHEDULE_ENABLED=false`.
2. The VM root runs the keeper commands for the lane token and `deploy-watcher-ping-url`. Each
   keeper holds the inbox's empty marker.
3. HQ lands the lane's registry rows in this repository's `.cred-consumers.toml`, in two changes.
   The token's row is `DEPLOY_WATCHER_BWS_ACCESS_TOKEN`, which registers the lane, and is due for
   its issue. The ping URL's row isn't due yet. Two more consumers belong to the same work:
   - the read-App key row's `runtime-fetch` reader for `scripts/run-deploy-watcher.sh`;
   - **change-manager's registry consumer for `M2M_TOKEN_OBSERVE`**, for this lane's container
     read. That row lives in change-manager's `.cred-consumers.toml`, and this PR doesn't touch
     it.
4. The identity's creator grants the lane's account read access to `f793576f-…`, `3b9503da-…`,
   `314a966c-…`, and the ping-URL secret.
5. Devon mints the token, and the executor writes `DEPLOY_WATCHER_BWS_ACCESS_TOKEN`. After the
   token's `store` unit completes, HQ makes the ping URL's row due. The executor then reads the URL
   from the Healthchecks listing and writes the ping-URL secret (ADR-0055 amendment 23).
6. A small PR writes the ping-URL secret's id into the compose and removes `deploy-watcher` from
   `test_deploy_lanes.py`'s `placeholders`. The orchestrator then serves that release.
7. The lane's `--add` runs in a quiet slot. Then come the `lane-run --read` proof (each of the
   lane's secrets `ok`, one other secret `refused`), the `--dry-run` in the container, and the
   week-bar baseline.
8. Cut-over steps 2 onward: the paired `--dry-run` reporting runs, the Mac agent's bootout,
   `--schedule true`, and the check that the last ping came from the container.

## Teardown

The PR isn't merged, so I left the worktree and the test database `orchestrator_test_hq_w_80` in
place. Remove both, and the local branch, once the PR merges. The main tree's `git status` shows
nothing I created.
