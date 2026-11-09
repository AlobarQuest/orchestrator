Outcome: done

# Report: the landing ledger's lane files and launcher (ADR-0059 increment 3, step 3e-1b)

Worker W-62. Branch `hq/lanes-inc-3-landing-ledger`, base `origin/main` at `7d8a1140`.
PR [AlobarQuest/orchestrator#643](https://github.com/AlobarQuest/orchestrator/pull/643) is open
and ready for review. It isn't merged.

## What changed

The code commits are `ef884dcf`, `d7455933`, `ab0d18bb`, `65565852` and `fc61e1ee`. The last commit
changes only this report.

| File | Change |
|---|---|
| `lanes/landing-ledger/compose.yaml` | New. One service, `landing-ledger`, with image `ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}` and command `["landing-ledger"]`. It maps `BWS_ACCESS_TOKEN: ${LANDING_LEDGER_BWS_ACCESS_TOKEN}` and `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`, and sets `SDS_DEADMAN_PING_URL_UUID: ""` as the placeholder. Restart policy is `unless-stopped`. It has no volume, ports, networks or labels. |
| `lanes/landing-ledger/crontab` | New. `30 7 * * * lane-run landing-ledger`, in the image's America/New_York time, as the Mac job's `StartCalendarInterval` runs it. The ledger has no `--submit` flag: every run except `--dry-run` records and arms the dead-man switch. |
| `scripts/run-landing-ledger.sh` | Adds the container path, which a set `BWS_ACCESS_TOKEN` selects. The Mac path is unchanged. |
| `.bws-secrets.toml` | The observer bearer's (`f793576f-…`) `lanes` is now `["execution-watch", "landing-ledger"]`. A new `[[secret]]` row for `314a966c-…` has `name = "lanes-read-app-private-key"`, `project = "SDS / Lane Apps"` and `lanes = ["landing-ledger"]`. |
| `tests/scripts/test_run_landing_ledger.py` | New. 16 tests: 12 for the container path, 1 for the Mac path's `gh` login with no mint, and 3 for the lane's files. |
| `tests/scripts/test_launcher_scope.py` | Its ledger runs drop the ambient `BWS_ACCESS_TOKEN`, so they keep holding the Mac path. |
| `tests/scripts/test_deploy_lanes.py` | `test_every_lanes_ping_url_secret_is_listed_for_that_lane` allows an empty id only for the lanes it names, `{"landing-ledger"}`. |
| `layers.toml` | Places the new test file in `sensors`, beside the launcher. |
| `docs/operations/deploy.md` | "Registered lanes" names the ledger's files, the placeholder and the cut-over steps still to come. "A lane's GitHub token" names the ledger's mint call. |

In the container, the launcher works as follows:

1. It skips the Mac's activation helper, so a container pass no longer logs
   `[activation] helper missing ... not activated` (handoff item 6). The change is one `if`
   around the existing block, and a test covers it.
2. It arms the dead-man switch as before.
3. It reads the observer bearer with the lane's token from the environment
   (`_lane_secret`, as in `run-execution-watch.sh`).
4. It derives the scope with `env -u BWS_ACCESS_TOKEN`, so `sds_scope` never sees the lane token.
5. It mints the GitHub token, and only after the scope exits are taken. Any preset
   `LANDING_LEDGER_GITHUB_TOKEN` is replaced.
6. It exits 1 if the mint fails or returns nothing.
7. It runs `unset BWS_ACCESS_TOKEN`, then runs `record` and `audit`.

The Mac path keeps its order: Keychain identity, then observer bearer, then `gh auth token`, then
scope. The only addition is `unset NARROW_IDENTITY` after the bearer read.

**Image (item 4).** No change was needed. `lanes/Dockerfile` copies `scripts/run-*.sh`, the four
`sds-*.sh` helpers and `lanes/`. Its build loop runs `supercronic -test` on every
`lanes/*/crontab`, the new one included. `landing-ledger` and `sds-github-mint` are console
scripts in the orchestrator image's `.venv/bin`. `lane_run.py` needs no change, because the
ledger has no state variable. `landing-ledger` already has a `LANE_CHECKS` row.

## The mint's exact arguments

The key reaches the mint's stdin straight from the BWS read, undecoded (base64 of the PEM). It's
never in a variable, an argument, a log or a file, and the mint runs without the lane token in its
environment:

```sh
_lane_secret "314a966c-5654-4e3c-8358-b4fd01268775" \
  | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id 5684758 --repository <owner/name> [--repository <owner/name> ...] \
      --permission metadata=read --permission contents=read \
      --permission pull_requests=read --permission actions=read
```

There's one `--repository` per line of `scripts.sds_scope ledger`. A test pins the full argv.

I checked the four permissions against every `reader.get` in `src/landing_ledger/`. Only
`github.py` calls GitHub, and only with GET:

- `metadata` and `contents`: the repository, branch, commits, commit pulls and the gate file
  under `contents`.
- `pull_requests`: a pull request, its commits and the open list.
- `actions`: runs, jobs and attempts.

No call needs a fifth permission.

## The dead-man's behavior with no ping URL

`SDS_DEADMAN_PING_URL_UUID` is empty, so `sds_deadman_resolve` doesn't take the by-UUID branch,
which tests `-n`. It falls through to the name lookup. In the container, `BWS_ACCESS_TOKEN_BROAD`
isn't passed and there's no `/usr/bin/security`, so the broad identity is empty. The helper logs
`[deadman] no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity — alerting disabled this run`
and pings nothing, not `/start`, success or `/fail`. A container pass therefore can't report
success, silently or otherwise. While the Mac job runs, `sds-landing-ledger` hears from the Mac
alone. If the Mac job stopped first, the check would go down after its grace.

The test exercises this by stubbing a broad identity and a failing listing read, because the
real `/usr/bin/security` on the Mac can't be stubbed. The outcome is the same, no ping, but the
logged reason differs ("Healthchecks could not be reached").

## Tests and checks

- `make check` at `fc61e1ee`, the last code commit, with `orchestrator_test_hq_w_62` and
  `PGTZ=UTC`: exit 0. Lint, format and type checks passed; **14,194 passed, 1 skipped**.
- Mutation run on the launcher: 9 mutations, all failing at least one test, no survivors. Each
  mutation removed one of the following:
  - `env -u` on the scope.
  - `env -u` on the mint.
  - The `gh` exclusion in the container.
  - `actions=read`.
  - The `unset BWS_ACCESS_TOKEN`.
  - The mint exit-code check.
  - The activation skip.
  - The mint itself.
  - The Mac path's `gh auth token` fallback.
- Security scanner from the repo root: no BLOCK findings. Two WARNs come from files this change
  doesn't touch: `*-migration/` glob coverage, and UUID `aaff45ca-…`, which
  `git grep aaff45ca origin/main` finds in reports already on `main`.
- `/code-review` (medium) returned five findings:
  - The test relaxation: fixed in `ab0d18bb`.
  - Ambient-token detection: the first item under "Concerns".
  - Two schedules at 07:30: the second item under "Concerns".
  - The partial-scope mint: the Mac's existing exit-3 behavior, kept on purpose.
  - The `unset` before the exit trap: not a defect, because the ping URL is resolved at arm time
    and the trap needs no token.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files).** The launcher logs to
  stdout and stderr, which supercronic's `-json` output collects in the container. It writes no
  log file. Its plain-text `FATAL:` lines are the same form as every other launcher's, and the
  rule's JSON shape comes from supercronic.
- **Code Brain rule 4 (no silent swallow; no `|| true` hiding a failed mint or BWS read).** The
  mint pipeline has no `|| true`. With `set -o pipefail` on, a failed key read or mint makes
  `mint_rc` non-zero, and the launcher exits 1 with a `FATAL:` line. An empty token exits 1 too.
  The only `|| true` instances in the launcher are the Mac path's existing Keychain and
  `gh auth token` reads, and each is followed by an empty check that exits 1.
- **Code Brain rule 5 (never log secrets).** The key goes from `bws` to the mint's stdin through
  a pipe, and the token is captured into a variable. Neither is echoed. `sds-github-mint` prints
  only the token on stdout, and its stderr messages carry no key, JWT or token. A test asserts
  that neither the lane token nor the minted token appears in the launcher's output.
- **Infra Brain 212 (HTTPS for a public FQDN).** The lane serves nothing and has no FQDN. It
  calls `https://sds.alobar.net` (the default `orchestrator_url`) and `https://api.github.com`.
- **Infra Brain 591 and 592 (no BWS token in tracked files or history).** No token is written
  anywhere. The scanner reports no BLOCK finding.
- **Infra Brain 593 (the bootstrap token never inline).** The compose maps `BWS_ACCESS_TOKEN`
  from `${LANDING_LEDGER_BWS_ACCESS_TOKEN}` only, and `add_refusals` checks that mapping in a test.
- **Infra Brain 1032 (an exposed credential rotates now).** No credential was surfaced. I made
  no BWS, GitHub, Coolify or Healthchecks call.
- **Deployment rule 4 (no Coolify scheduled task beside an in-container scheduler).** The
  schedule is the tracked crontab under supercronic, as for execution-watch. No Coolify task
  exists or is proposed.

## Concerns

1. **The container is detected by a set `BWS_ACCESS_TOKEN`, as the handoff asked, mirroring
   execution-watch.** An operator who runs the launcher by hand from a shell that exports
   `BWS_ACCESS_TOKEN` takes the container path. On the Mac that means a mint attempt, and
   activation is skipped. launchd starts the job with `PATH` and `HOME` only, so the scheduled job
   isn't affected. Before this change, the ledger launcher ignored an ambient token.
2. **The Mac job and the container both fire at 07:30.** Only `LANE_SCHEDULE_ENABLED=false`,
   the compose default, keeps the container from running a second daily pass. The cut-over's
   order (schedule on, then the Mac job retired) has to hold that.
3. **I didn't add a `.cred-consumers.toml` runtime-fetch row for the read App's key.** The
   `lanes-read-app-private-key` row's comment says each lane's runtime-fetch row "lands in that
   lane's move PR". This handoff lists only code, tests, lane files, `.bws-secrets.toml` rows and
   docs, and puts cut-over registry rows in their own changes. HQ needs to decide which change
   carries that row.
4. **The new manifest row's `name` is the registry id, `lanes-read-app-private-key`.** The BWS
   key name of `314a966c-…` isn't recorded in this repository, and the scanner reads only `uuid`.
5. **The mint refuses repositories of more than one owner, and repositories where the App isn't
   installed.** If the live `observed` population ever names one, every container pass exits 1.
   Today the record and the App's 14 installs are meant to agree (HQ G-17). I couldn't check the
   live population here.
6. **The `.bws-secrets.toml` observer comment still lists `run-landing-ledger` as a reader
   "through the narrow identity".** That's true on the Mac. In the container, the `lanes` field
   records the read. I left the comment as is, as execution-watch's change did.

## Cut-over steps that remain

These follow ADR-0059's "Cut-over for one lane", each in its own change or act:

1. Once the orchestrator serves this release, HQ creates `sds-lane-landing-ledger` from the
   tracked compose without `instant_deploy`, so nothing starts. The token's registry row names
   this resource's uuid, so the resource comes first.
2. The VM root runs the keeper command for the lane token's keeper and the lane's ping-URL
   secret, each holding the inbox's empty marker.
3. HQ lands the lane's two registry rows in `.cred-consumers.toml`: the token row, due for its
   issue, and the ping-URL row, not yet due. The read App key's runtime-fetch row, if HQ places it
   there (concern 3), lands then too.
4. The identity's creator makes the `sds-lane-landing-ledger` BWS account and grants it
   `f793576f-…`, `314a966c-…` and the ping-URL secret.
5. Devon mints the token into the inbox, and the executor writes
   `LANDING_LEDGER_BWS_ACCESS_TOKEN`. After the `store` unit completes, HQ makes the ping-URL row
   due and Devon pastes the URL.
6. A small PR writes the ping-URL secret's id into `SDS_DEADMAN_PING_URL_UUID` and
   `.bws-secrets.toml`. The same PR removes `landing-ledger` from the `placeholders` set in
   `test_every_lanes_ping_url_secret_is_listed_for_that_lane` and updates
   `test_the_compose_meets_the_add_refusals_and_maps_nothing_else`.
7. Once that PR is served, HQ runs the lane's `--add` through the lane phases, then checks the
   following in the container:
   - `lane-run --read` on the two secrets and the ping-URL secret: each `ok`.
   - One foreign UUID: `refused`.
   - `lane-run landing-ledger --dry-run`.
8. HQ runs cut-over steps 2 onward: the paired `--dry-run` reporting runs, the week-bar baseline,
   `--schedule true`, and retiring `com.devon.landing-ledger`.

Teardown is still to do after merge: the worktree, the `orchestrator_test_hq_w_62` database and
the merged branch.
