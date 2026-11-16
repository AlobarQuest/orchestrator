Outcome: done

# Build report: the work carrier's lane files and launcher (ADR-0059 increment 4, step 3f-2)

Worker W-184. Branch `hq/lanes-inc-4-work-carrier` from `origin/main` `b5adc971`. The handoff is
`sds-hq/state/handoffs/lanes-inc-4-work-carrier.md`, with HQ's answers to my four questions in its
final section. I took no production act. I made no BWS read of a real secret, no GitHub App call,
and no Coolify, VPS, change-manager, orchestrator, alert-hub, chain-database, Healthchecks or
`launchctl` call. Every run used fakes.

## What changed

The work is in two commits: the pin bump alone, then the lane.

| File | Change |
| --- | --- |
| `security-standards.pin.toml` | Bumped to `05963b76…` with a recomputed `artifact_sha256` (its own commit). |
| `scripts/sds-git.sh` | New. `sds_git_credential <token-variable> [<user.name> <user.email>]`. |
| `scripts/run-work-carrier.sh` | A container branch beside the Mac path, which is indented into an `else` and otherwise unchanged. |
| `src/work_watcher/cli.py` | New `work-watcher repositories` subcommand, dispatched from `main`. |
| `scripts/lane_run.py` | `CHAIN_SETTINGS`: the chain's three non-secret settings pass through. |
| `lanes/work-carrier/compose.yaml`, `lanes/work-carrier/crontab` | New. |
| `lanes/Dockerfile` | `/var/tmp/sds-work-carrier`, owned by `orchestrator`, and a root-owned symlink `/intent-packages` into it. The helper count in the comment is now five. |
| `.bws-secrets.toml` | `"work-carrier"` added to the `lanes` of eight rows. The "No `lanes`" notes on `a9843d17` and `2f708d08` are replaced. |
| `docs/operations/deploy.md` | "Registered lanes" gains the work carrier and its remaining cut-over steps. |
| `docs/operations/credentials.md` | The read password's holders row names the lane account as its holder after cut-over. |
| `docs/decisions/0059-…md` | One dated line under amendment 7's "Open before building", citing D-52 A (HQ answer 2). |
| `layers.toml` | Registers `tests/scripts/test_run_work_carrier.py` (bridge) and `tests/scripts/test_sds_git.py` (runtime). |
| New tests | `tests/scripts/test_run_work_carrier.py`, `tests/scripts/test_sds_git.py`, `tests/work_watcher/test_repositories.py`, `tests/work_carrier/test_container_portfolio.py`. |
| Changed tests | `test_lane_run.py` (chain settings), `test_deploy_lanes.py` (`placeholders = {"work-carrier"}`), `test_lane_image.py` (five helpers), `test_bridge_edges.py` (the watcher's bearers include the read App's key), `test_workability.py` (the Mac GitHub block is now indented). |

## Each secret and how the container reads it

The container branch runs when `BWS_ACCESS_TOKEN` is set. Every read is
`sds_bws_value <uuid>` with the lane's one token in the environment, never in argv. No Keychain item
is read: all three `/usr/bin/security` lines sit inside the Mac `else`, and a test pins that. Each
read that comes back empty fails the pass with exit 1 and `(the lane's token)` in the message.

| Secret | Variable | When |
| --- | --- | --- |
| change-manager READ `314f276d-…` (broad identity on the Mac) | `CHANGE_MANAGER_TOKEN`; also `WORK_WATCHER_CHANGE_MANAGER_TOKEN` on a bare pass | Every pass |
| orchestrator SYSTEM `221a48d5-…` | `WORK_WATCHER_ORCHESTRATOR_TOKEN`; `WORK_CARRIER_ORCHESTRATOR_TOKEN` exported only on `--register` | Every pass, as the Mac's bare pass reads it where it can |
| orchestrator OBSERVER `f793576f-…` | `SDS_MEMBERS_OBSERVER_TOKEN` | Every pass |
| `factory-events-read-password` `2f708d08-…` | `FACTORY_EVENTS_READ_PASSWORD`, as an assignment prefix on `work-carrier` and the chain verify only | Every pass |
| read-only App key `314a966c-…` | none: piped straight into `sds-github-mint` | Every pass |
| carrier OBSERVER `a9843d17-…` | `WORK_CARRIER_OBSERVATION_TOKEN` (key id `work-carrier-observer`) | `--register` |
| `sds-bridge` sender `b415d03b-…` | `SDS_BRIDGE_ALERT_TOKEN` | `--register` |
| change-manager PROPOSE `acccb346-…` (broad identity on the Mac) | `WORK_WATCHER_CHANGE_MANAGER_TOKEN` | `--register` |

`unset BWS_ACCESS_TOKEN` runs right after the mint, before git and every program. The first draft
unset it after the clone. `test_no_program_gets_the_lanes_token` caught `git clone` running with the
token in its environment, and I moved the line.

## The GitHub token and the clone

`work-watcher repositories` prints intent-packages, plus each repository whose pull request a
pending or approved record names, read with `staleness.pull_request_of`. Those are the only GitHub
reads the pass makes: the clone and the watcher's one pull request GET. Names that differ only in
case are deduplicated. The output goes to stdout only, and errors go to stderr.

The launcher builds `TARGETS` from that list, as `run-deploy-watcher.sh` does. It then mints with
`--app-id 5684758`, those `--repository` flags, and `metadata=read`, `contents=read` and
`pull_requests=read`, into `WORK_CARRIER_GITHUB_TOKEN`. The mint runs with `env -u BWS_ACCESS_TOKEN`.
A failed mint, or an empty token, is exit 1.

**One deliberate deviation from HQ answer 3.** HQ wrote that an underivable list "exits 3", as the
deploy watcher does. On the deploy watcher's scale, 3 means incomplete. On this launcher's scale, 3
means a finding a person must look at, and it ranks below 1 and 2. A change-manager outage would
then read as a finding instead of a tool failure. It would also disagree with the Mac in the paired
pass, where the same outage exits 1 from `work-watcher` itself. So I kept the launcher's own scale:

- The subcommand exits 1 for an unreadable listing and 2 for a missing credential, as `run` does.
- The launcher passes any non-zero code through verbatim, so 127 stays 127.
- An empty list exits 2. The list always holds intent-packages, so this is guarded but can't
  happen.

The test is `test_an_underivable_list_mints_nothing_and_passes_its_code_through`. If HQ wants a
literal 3, it's a one-line change.

The clone works like this:

1. `source scripts/sds-git.sh`, then `sds_git_credential WORK_CARRIER_GITHUB_TOKEN`, with no
   identity pairs.
2. `rm -rf` of the previous clone.
3. `git clone --quiet --depth 1 --single-branch --branch main
   https://github.com/AlobarQuest/intent-packages.git /var/tmp/sds-work-carrier/intent-packages`.
4. `WORK_CARRIER_CHECKOUT_ROOT=/var/tmp/sds-work-carrier` and
   `WORK_WATCHER_PACKAGES_CHECKOUT=<that>/intent-packages`.
5. After the carry, the clone is removed. A failed removal is logged, and the next pass removes it
   first.

`WORK_CARRIER_CLONE_ROOT` is a test seam: `lane-run` never passes it, and a test pins that.

**Why `PYTHONPATH` doesn't work for the intent-packages path (HQ answer 4).**
`package_sources._intent_packages_src` (150-153) computes
`Path(__file__).resolve().parents[2].parent / "intent-packages" / "src"`, from the module's own
location, and returns it only if that directory exists. It reads no environment variable. The
`verify-approval` subprocess also builds its own `PYTHONPATH` from that path. So only a directory at
`/intent-packages` works for `/app/src/orchestrator`. The image provides it as a root-owned symlink
into the scratch directory the lane user owns. The image's security-standards (`/app/security-standards`, at the pin) is found the
same way, through `SECURITY_STANDARDS_DIR`. `lane-run` strips the image's own `ENV`, so the launcher
sets that variable from `REPO_ROOT`.

**`sds-git.sh`** follows ADR-0059 544-566:

- It reads the token from a named variable, so the token is never one of its arguments.
- It exports `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and `GIT_TERMINAL_PROMPT=0`.
- It sets `GIT_CONFIG_COUNT=1`, with pair 0 `http.https://github.com/.extraheader` =
  `AUTHORIZATION: basic <base64 of x-access-token:token>`. The value is computed with the `printf`
  builtin into `base64` on stdin, with newlines stripped, because GNU wraps at 76 columns.
- It sets `GIT_CONFIG_COUNT=3`, with `user.name` and `user.email`, only when both are passed. That's
  for the proposers, and it's off for the carrier.
- It also unsets `GIT_TRACE`, `GIT_TRACE_CURL`, `GIT_TRACE_PACKET` and `GIT_CURL_VERBOSE` (Rule 5).

## The chain settings and network

The compose sets three tracked literals (HQ answer 1): `FACTORY_EVENTS_DB_HOST:
b82fa1vg5whecgnjreuqb5tk`, `FACTORY_EVENTS_DB_PORT: "5432"` and `FACTORY_EVENTS_DB_NAME: postgres`.
The service joins the external `coolify` network, with `networks: [coolify]` and `coolify:
{external: true}`. It's the only lane compose with a `networks:` entry.

`lane_run.CHAIN_SETTINGS` passes the three settings, and never the password. The launcher checks all
three before any BWS read and fails the pass by name if one is empty. There's no file mode and no
`chain-db.env` in the container. It copies them to `CHAIN_DB_*` and unsets every `FACTORY_EVENTS_*`
variable, so only the carry and the verify get them, as an assignment prefix.

**Cut-over check:** in-network reach of `b82fa1vg5whecgnjreuqb5tk:5432` is unproven until the first
bare `lane-run work-carrier` in the container. That run's `[chain]` line is the proof. It's step 7
in the `deploy.md` list.

## The VPS verify and the paired pass

The VPS verify follows HQ answer 2. In the container, the launcher runs
`python -m orchestrator.factory_chain_read` once per pass, after the retirement phase and before
the carry, with the read password, the three settings and
`PYTHONPATH=$SECURITY_STANDARDS_DIR/src`. That walks the whole chain under the read role.

- Exit 0 with `"outcome": "verified"` in the last line's JSON passes, and the summary is appended to
  the report as `[chain] outcome=verified seq=… head=… events=…`.
- Only the record's summary fields reach the report: `outcome`, `seq`, `head`, `events`, and a
  break's `error` or a failure's `error_type`. They're parsed with the venv interpreter. A verified
  record also carries every `package.approved` event, which would grow the lane log and every
  `/fail` body with the chain, so those events stay out. This narrows HQ answer 2's "log only the
  module's own JSON record" to its summary; the advisor's review caught it.
- Anything else writes `[chain] FATAL: ...` with the summary to the report, records exit 1 on the
  launcher's worst-outcome ranking, and skips the carry. A worse retirement code, such as 2, still
  wins.
- It runs after the retirement, not before it, because the retirement reads no chain. A database
  outage doesn't stop built work being retired or its alerts resolving. That's the "before the
  carry" HQ named; my first draft ran the verify before the watcher too, and the code review moved
  it.
- Its stderr is discarded, with an inline justification, as `package_sources` discards it: driver
  text can repeat connection details (ADR-0055 amendment 24 item 13).

So D-52 A's "the carrier's first VPS pass carries the VPS verify" (ADR-0059 2858-2860; ADR-0055
3917-3918, 3935-3937) holds on an empty queue. The dated line under amendment 7 records it as every
pass's behavior.

The paired pass follows ADR-0059 cut-over step 2 (~1137-1140). The work carrier's reporting mode is
"an outcome-alarm launcher run without its acting flag, which resolves no ping URL". The bare
`lane-run work-carrier` in the container and the bare `scripts/run-work-carrier.sh` on the Mac,
run in the same minute, both register nothing and ping nothing. Neither writes a record, because the
watcher writes only with `--retire` and the carry only with `--register`. So both can run beside
each other without both registering. The comparison follows step 4 (~1160-1170): the exit code,
the record ids, and each record's kind.

The container's bare pass also runs the verify. One difference I found: a bare Mac pass leaves
`~/Projects/intent-packages` where it is, while the container clones `main`. `deploy.md` step 8
says to fast-forward the Mac checkout first. No other lane-run or deploy-script support is needed.

## The portfolio source

The portfolio read is unchanged (D-55 A's feed is out of scope): `workability.py`,
`DEFAULT_PORTFOLIO` and `WORK_CARRIER_PORTFOLIO` are untouched, and `WORK_CARRIER_PORTFOLIO` isn't
in `lane_run.py`.

`tests/work_carrier/test_container_portfolio.py` builds the container's environment with
`lane_run.lane_environment`, which strips `WORK_CARRIER_PORTFOLIO` and gives a new empty `HOME`.
It shows the following:

- The portfolio can't be read.
- A granted repository-targeted record is `UNDECIDED`: `granted` yes, `capable` and `permissions`
  unknown. It's held, and never carried.
- A standing rotation package is `OPERATIONAL` and carries (`assess_operational`).

So until the feed lands, the container lane holds every repository-targeted record (exit 2) and
carries only standing rotations. That makes the feed a cut-over prerequisite: with a
repository-targeted record queued, the paired comparison would differ (step 8 of the remaining
steps).

I checked the standing-rotation carry path for container gaps:

- It runs `orchestrator emit-intake-payload` from the image's PATH (`prepare.py`, `EMITTER`), and no
  checkout's `.venv`. So the clone needs no venv, unlike the two proposers.
- Its registry reads (`rotated_by_sds`, `find_credential`) go through
  `RepoMirror(default_mirror_root("work-carrier"))`. That's `~/.cache/work-carrier/mirrors` under
  `lane-run`'s new `HOME`, fetched anonymously from the four public registry repositories, as the
  rotation decomposer's mirrors are. Each pass refetches them.

## The pin bump

`security-standards.pin.toml` moved from `60ec80f7…` to `05963b7626b594d85bcb0d232f54421619ae3fd8`
(security-standards #97's merge, past R-SS #95 `e905304`, confirmed with
`merge-base --is-ancestor`). The new `artifact_sha256` is
`4873d9ab0f29dfc467d4f3a538565944a9835d0abfaf1b57dbcd2991d633c2e6`. I computed it with
`scripts/shape_registry_context.py` and `build_registry_bundle.artifact_digest`, per `deploy.md`
756-789. As a control, the same recipe at the old revision reproduced the old pin's `d5cb4a58…`. It's
in its own commit, `1b0b4380`.

## The crontab and why

`5 7 * * * lane-run work-carrier --register` matches the Mac plist's 07:05 with `--register`.
Minute 5 is one no other lane starts on, and a test pins that.

**For HQ:** the line carries a flag, `--register`, so HQ's cycles won't fire it out of schedule.
Only the scheduled line, or an explicit `lane-run work-carrier --register`, registers.

## The state volume

`sds-lanes-work-carrier-state` is mounted at `/var/lib/sds/work-carrier`, and
`WORK_WATCHER_STATE_DIR` points there. The directory already exists in the image. The launcher
requires the variable on a `--register` pass and runs `mkdir -p` on it. At cut-over, the Mac's
`~/Library/Application Support/sds/work-watcher/alerts.json` is copied in, as ADR-0059's lane-state
table row says.

## Tests and checks run

- `make check`: **green**. It collected 15,074 tests: **15,073 passed and 1 skipped** (exit 0,
  7 minutes 21 seconds). Ruff, format and pyright are clean.
- CI on PR #723 (run 43885468053): `Quality` pass, with 15,051 passed and 23 skipped (14 minutes
  46 seconds; CI skips more tests than the local run); `Rollout transcription currency` pass;
  `Runner consumer compatibility` pass.
- Security scanner (`--category security`, from the repository root): 0 BLOCK, 2 WARN. The WARNs
  are the existing gitignore-coverage pattern checks; this change adds no secret file.
- Targeted runs: `tests/scripts/test_run_work_carrier.py` (51), `tests/scripts/test_sds_git.py`
  (12), `tests/work_watcher/test_repositories.py` (6) and `test_container_portfolio.py` (2).
- **Mutation testing.** I mutated 18 guards, and each mutant failed at least one test:
  - In the launcher: the token unset, the outcome check, the verify exit-code check, the verify's
    carry gate, the verify's recorded code, the chain-settings check, the settings unset (so they
    would reach the watcher), SYSTEM exported to a bare carry, the pre-clone discard, the derive
    exit-code check, the blank-line guard, and the summary parse's exit code and field filter.
  - In the subcommand: the live filter, the case-insensitive dedupe, and the packages repository.
  - In the helper: the trace unset and `GIT_CONFIG_GLOBAL`.
  - The pre-clone discard survived at first, because the git stub accepted a non-empty target. The
    stub now refuses one, as real git does, and the mutant dies.

## Self-review

`/code-review` (medium) ran on the diff and raised eight findings:

- **Fixed:** the verify ran before the retirement, so a chain outage stopped the retirement too. It
  now runs after the retirement and before the carry.
- **Fixed:** a blank line from `work-watcher repositories` would have become `--repository ''`. Blank
  lines are now skipped, and an all-blank list is the empty-list refusal (exit 2).
- **Comment corrected:** the clone is removed at the end of a pass that reaches it and before every
  clone. A failed pass leaves it for the next pass, and it holds no credential. An `EXIT` trap would
  replace the dead-man helper's own trap, so I didn't add one.
- **Comment corrected:** on a bare pass, the SYSTEM bearer is exported under the watcher's
  variable, so every later child inherits it. That's the Mac path's behavior too (its common
  `export WORK_WATCHER_ORCHESTRATOR_TOKEN`). The carry never reads that name, and gets its own
  variable only on `--register`.
- **Not changed: a set `BWS_ACCESS_TOKEN` is the container signal.** That's the convention every
  moved launcher uses (`run-rotation-decomposer.sh`, `run-deploy-watcher.sh`). The Mac job sets
  none, and the Mac launchers deliberately never export one.
- **Not changed: the repository list comes from record text.** This is HQ answer 3's design. The
  parse accepts only `owner/name` characters (`staleness._PULL_REQUEST`), and the mint is bounded
  by the read App's installation (concern 2).
- **Replaced: the substring check on `"outcome": "verified"`.** The record is now parsed as JSON
  (see "The VPS verify"), so the check doesn't depend on its formatting.
- **Not changed: the empty ping-URL placeholder.** It's what the handoff asks for, as #689 did.

- The Mac path is unchanged apart from indentation inside the `else`. The existing Mac tests pass
  unmodified, except `test_workability.py`'s block extraction, which now cuts at the indented `fi`:
  `test_launcher_orchestration.py`, `test_bridge_chain_switch.py`,
  `test_packages_checkout_activation.py` and `test_bridge_launchers.py`.
  `test_on_the_mac_nothing_is_minted_cloned_or_verified` adds a check that the Mac path makes no
  mint, derive, clone or verify, and that it still reads `gh auth token`.
- I added no suppression comment.
- `sds_outcome_fail` overwrites the report, so the clone's and the verify's failure text goes into
  the failure message itself.

## Concerns and follow-ups

1. **The exit-3 deviation** described in "The GitHub token and the clone". HQ can confirm it or
   reverse it.
2. **The read App's installation must cover every repository a live record names.** If it doesn't,
   the mint refuses the whole pass (exit 1), as the deploy watcher's would. The cut-over's bare run
   shows that.
3. **The helper's no-argv guarantee rests on `printf` being a builtin.** The test checks
   `base64`'s argv, not `printf`'s. On macOS, `base64` doesn't wrap, so the long-token test can
   only fail on Linux, which is where CI and the image run.
4. **The image's intent-packages check uses the orchestrator interpreter.**
   `/app/security-standards/.venv-events` doesn't exist in the image, so intent-packages'
   `_events_python` falls back to the orchestrator venv's interpreter, which holds psycopg. The
   first queued record in the container proves that path. The cut-over's bare run proves it only
   if a record is queued.
5. **Standards the handoff's section doesn't cover:** none found.

## The cut-over steps that remain

`docs/operations/deploy.md`, "Registered lanes", lists them in order:

1. The served release. Then HQ creates `sds-lane-work-carrier` without deploying it.
2. The VM root's keepers for the lane token and `work-carrier-ping-url`.
3. The token's and the ping URL's registry rows, in two changes. In the same work, the read-App
   key's `runtime-fetch` reader for `run-work-carrier.sh`, and the read password's `runtime-fetch`
   note naming the lane account.
4. The lane account's grants on its eight secrets and its ping-URL secret. **Two of them are the
   broad-identity reads, which become direct grants to the lane account (Devon's act):**
   change-manager READ `314f276d-…` and change-manager PROPOSE `acccb346-…`.
5. The token's mint and write, then the ping URL.
6. A served release naming the ping-URL id, with `work-carrier` removed from `placeholders`.
7. `--add`, then `lane-run --read` over the eight secrets, then a bare `lane-run work-carrier`. That
   run is the in-network reach check and the first VPS verify, and it also fetches the public
   credential registries a standing rotation's carry reads.
8. **The D-55 portfolio feed**, in its own step and ADR amendment. ADR-0059 decision 5 (805-811)
   decides the portfolio source before the lane moves. Without the feed, a repository-targeted
   record holds (exit 2) in the container while the Mac carries it, so the paired comparison
   fails whenever one is queued.
9. The `alerts.json` copy, the paired bare runs (after fast-forwarding the Mac checkout), the
   schedule, and the Mac job's retirement. After the retirement, `sds-operator`'s read of the read
   password goes, and the lane account is its one holder.

## Standards followed

- **Code Brain Rule 3 (structured JSON logs to stdout, not files).** The launcher keeps the outcome
  alarm's report. The chain verify's log is the module's own JSON record. No new log file is
  written.
- **Rule 4 (no silent swallow; no `|| true` hiding a failed BWS read, clone or mint).** Every BWS
  read, the derive, the mint, the clone and the verify fail the pass with a named message. The only
  deliberate discard is the verify's stderr, which has an inline justification, and its outcome is
  still enforced. The clone removal at the end is logged on failure, not hidden. No `|| true` was
  added; the existing ones sit only in the Mac Keychain reads.
- **Rule 5 (never log secrets, auth headers or git trace).** The extraheader lives only in the
  environment, and the tests assert it never appears in output. git trace variables are unset. The
  token is never in argv (`printf` builtin into `base64` stdin). No secret value appears in stdout
  or stderr in any test.
- **Infra Brain 212 (HTTPS for a public FQDN).** The lane has no FQDN. Its calls go to GitHub,
  change-manager and the orchestrator over HTTPS, and to the database over the internal `coolify`
  network.
- **591 and 592 (no BWS token in tracked files or history).** None was added. The compose maps the
  token from the resource variable.
- **593 (the bootstrap token never inline).** `BWS_ACCESS_TOKEN: ${WORK_CARRIER_BWS_ACCESS_TOKEN}`
  only. `add_refusals` and the compose test pin it.
- **1032 (an exposed credential rotates now).** No credential was surfaced.
- **Deployment (no Coolify scheduled task beside the in-container scheduler).** The schedule is the
  tracked crontab, run by supercronic. No scheduled task was created.
