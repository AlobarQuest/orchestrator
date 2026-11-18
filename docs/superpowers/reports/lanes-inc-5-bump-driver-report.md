Outcome: done

# Build report: the bump driver's lane files and launcher (ADR-0059 increment 5, step 3g-3)

Worker W-211, branch `hq/lanes-inc-5-bump-driver`, base `origin/main` at `d6a4d909`. I built
option A of my question, as HQ answered it: build to decision 1, clone under the pass's own
`$HOME/Projects/`, run `factory` from a shim under the image's interpreter, and add no
acting-pass refusal. A cut-over step marked as a blocker carries the conformance-claim gap. The
crontab minute is 51. I made no production act and no live call.

## What changed

| File | Change |
|---|---|
| `scripts/run-bump-driver.sh` | Adds a container branch, entered when `BWS_ACCESS_TOKEN` is set, which runs the whole pass and exits through `sds_outcome_end`. Activation is skipped in the container. It gains the read App's key UUID and id, and a header paragraph ("TWO HOSTS, ONE LAUNCHER"). The Mac lines are unchanged, apart from the activation block's re-indent inside `if [ -z "${BWS_ACCESS_TOKEN:-}" ]`. |
| `src/bump_driver/cli.py` | Adds a `bump-driver repositories` subcommand and a `CLONED` tuple. `main` routes the subcommand before `argparse`. |
| `tests/bump_driver/test_repositories.py` (new) | Covers the subcommand: the list, case-only repeats, retired and ungranted members, an unreadable record (exit 3), a missing credential (exit 1), and extra arguments. |
| `tests/scripts/test_run_bump_driver.py` (new) | 44 tests that run the real launcher under `env -i` with stubs, on the container path and the Mac path, plus the lane's tracked files. |
| `lanes/bump-driver/compose.yaml` (new) | One service, modeled on #738's. |
| `lanes/bump-driver/crontab` (new) | `51 * * * * lane-run bump-driver`. |
| `.bws-secrets.toml` | Adds `bump-driver` to the `lanes` of `221a48d5`, `b415d03b`, `314f276d` and `314a966c`. Adds `lanes = ["bump-driver"]` to `750f737f` and `da8134b0`. The reader comments name step 3g-3, including the READ readers' comment. |
| `layers.toml` | Registers `tests/scripts/test_run_bump_driver.py` beside the launcher. `tests/bump_driver/**` already covers the new subcommand test. |
| `tests/scripts/test_deploy_lanes.py` | Adds `bump-driver` to `placeholders`. |
| `docs/operations/deploy.md` | Line 74's ":50" sentence keeps the Mac job and adds ":51 after cut-over". "Registered lanes" gains the lane's paragraph, its secrets, its read token, and its nine remaining cut-over steps. Step 1 is the blocker. |
| `docs/operations/credentials.md` | Adds the bump driver's holder paragraph (Mac identities now, the `bump-driver` lane account after cut-over). |
| `docs/superpowers/reports/lanes-inc-5-bump-driver-question-answered.md` | HQ's answered question, kept as the sibling lanes keep theirs. |

`tests/architecture/test_landing_edges.py` and `test_out_of_process_isolation.py` needed no
change: no edge table lists the bump driver's bearers, and the subcommand adds no import.
`scripts/lane_run.py` needed no change either, because `BUMP_DRIVER_STATE_DIR` is already in
`STATE_VARIABLES`.

## Each secret and how the container reads it

Every read is `sds_bws_value "<uuid>"` in its one-argument form, which reads the lane's token from
the environment, never an argument. Each is read on every pass, `--dry-run` included, in the
Mac's order. Each failed read is fatal and goes through `sds_outcome_fail`, naming the secret and
"(the lane's token)", as the Mac's failure messages name the read.

| Secret | Variable | Read |
|---|---|---|
| Orchestrator SYSTEM bearer `221a48d5-…` | `ORCHESTRATOR_API_TOKEN` (key id `orchestrator-system`) | Every pass, fatal |
| `sds-bridge` alert sender `b415d03b-…` | `SDS_BRIDGE_ALERT_TOKEN` | Every pass, fatal; only an acting pass sends |
| change-manager READ `314f276d-…` | `CHANGE_MANAGER_TOKEN` | Every pass, fatal |
| Code Brain contributor key `750f737f-4cb6-4876-9a98-b48200ea1c0b` | `CODE_BRAIN_KEY` | Every pass, fatal |
| Infra Brain contributor key `da8134b0-565f-45c8-8965-b48200ea1c40` | `INFRA_BRAIN_KEY` | Every pass, fatal |
| Read-only App key `314a966c-…` | none: piped from `sds_bws_value` straight into `sds-github-mint`'s stdin | Every pass |

A value already in the environment is never used: each variable is assigned from its read, and
`test_credentials_already_in_the_environment_are_never_used` pins that. `BWS_ACCESS_TOKEN` is
unset after the mint, before git or the driver runs. `test_no_token_reaches_an_argument` checks
the argv of every `bws`, mint, derive, git, driver and `factory` call, and of every ping, against
the lane token, the minted token and every secret value.

The outcome alarm reads no Healthchecks API key in the container. With an empty
`SDS_DEADMAN_PING_URL_UUID`, `sds_outcome_begin` (acting passes only) calls
`sds_deadman_resolve`, the same resolver `sds_deadman_arm` uses. It falls through to the name
lookup, which needs a broad identity. The container has neither `BWS_ACCESS_TOKEN_BROAD` nor
`/usr/bin/security`, so it reads nothing, logs one "alerting disabled" line, and pings nothing.
So `sds_outcome_*` honors the empty placeholder exactly as `sds_deadman_arm` does. The test sets
a broad stand-in to keep the real Keychain out, which is why the acting-pass test sees one
`51509d5e` read by the stand-in. `test_with_a_ping_url_named_the_healthchecks_key_is_never_read`
proves that once the UUID is named, only that secret is read.

## The read token: permissions and repositories

`BUMP_DRIVER_GITHUB_TOKEN` is minted from `alobar-sds-lanes-read` (App ID `5684758`) on every
pass, dry or acting, with `--permission metadata=read --permission pull_requests=read
--permission contents=read`. The permissions follow the driver's GitHub use:

- `pull_requests`: `GitHub.pull_state` (`GET /repos/{r}/pulls/{n}`) is its one API call.
- `contents`: the mirrors' clone and fetch (`Mirrors.fresh`) and decision 1's three clones.
- `metadata`: every installation token carries it.

I added no permission. The App's permission list is unchanged.

The repositories come from `bump-driver repositories`: `CLONED` (intent-packages,
project-standards and security-standards) plus each repository the member record grants
`dependency_updates`. The subcommand reads the record as the pass reads it, with the SYSTEM
bearer, without the lane token (`env -u BWS_ACCESS_TOKEN`). Repeats that differ only in case
collapse, because the mint casefolds. It fails closed with nothing on stdout: exit 1 for a missing
credential and exit 3 for an unreadable record, the pass's own codes. The launcher passes either
code through and names the subcommand's last stderr line in the `/fail` body. An empty list exits
1 before any mint.

## The clones and `factory`'s Python

`sds_git_credential BUMP_DRIVER_GITHUB_TOKEN` sets git's extraheader in the environment. The
launcher then clones each of the three repositories with
`git clone --quiet --depth 1 --single-branch --branch main https://github.com/AlobarQuest/<r>.git
"$HOME/Projects/<r>"`. `HOME` is the new directory `lane-run` makes for the pass and removes after
it, so the pass discards the clones.

The clones sit at the Mac's paths, so two things hold:

- `FACTORY_PYTHONPATH` is byte-for-byte the Mac's string.
- project-standards' checkers find `~/Projects/security-standards` by their own default. That
  resolves the `security` and governance cells, which the generator's allowlisted environment
  couldn't reach through an override variable.

After the clones, the launcher unsets git's `GIT_CONFIG_*` credential. `Mirrors` builds its own
git environment from the token (ADR lines 544-575), and the Mac's pass carries none, so the
driver's environment matches the Mac's.

For `factory`'s Python I chose #733's approach over a per-pass `uv sync`. The shim is written to
`$HOME/.local/bin/factory`, which goes first on `PATH`:

```sh
PYTHONPATH='<home>/Projects/intent-packages/src'${PYTHONPATH:+:$PYTHONPATH} exec '/app/.venv/bin/python' -m intent_packages.factory_cli "$@"
```

I chose the shim for three reasons:

- It fetches nothing beyond the three GitHub clones. `uv sync` would reach PyPI every pass, and
  by default it installs intent-packages' dev group too (pytest, ruff, pyright, psycopg). With
  `UV_CACHE_DIR` in the volume, only a lock change refetches, but that's still a new network path
  from the lane.
- intent-packages' runtime dependencies are `httpx` and `pyyaml`
  (`intent-packages/pyproject.toml`). Both are in the image's venv, and the stdlib covers the
  rest of its imports.
- The image's venv also holds the `orchestrator` command that the conformance claim runs.

`test_factory_is_the_images_interpreter_on_the_clone` runs the shim for real against a stand-in
`intent_packages.factory_cli`. It proves the shim is the `factory` on `PATH`, uses the image's
interpreter, and puts the clone's `src` before `FACTORY_PYTHONPATH`.

## The conformance claim's Mac-only inputs (cut-over blocker)

HQ's answer asked for the question's table in this report. `factory decompose` runs
`orchestrator conformance-claim <mirror>` (`intent_packages/factory/orchestrator_cli.py:71`), which
imports project-standards' `portfolio.compliance` (`src/orchestrator/conformance_claim.py:95-120`).
A claim is `green` only when every cell is `pass`, `accepted-exception` or `not-applicable`
(`conformance_claim.py:37-38`, `:79-91`). `_generator_environment` (`bump_driver/cli.py`) passes
`factory` only `PATH`, `HOME`, `PYTHONPATH`, the Brain keys, and git settings, so only the
`HOME`-relative defaults resolve.

| Cell | What it reads (`project-standards/src/portfolio/`) | In the container |
|---|---|---|
| `security`, governance | `~/Projects/security-standards` (`config.security_standards_repo`) | Resolved: the clone sits at `$HOME/Projects/security-standards`. |
| `code` | `~/Developer/code-standards/.venv/bin/code-standards`, else `uv run --project ~/Developer/code-standards` (`checkers.py:80-84`) | Missing: not among decision 1's clones, and running it needs a venv or PyPI. The cell reads `unknown`, so the claim reads `violations`. |
| `infra` | The latest report in `~/infra-drift/reports`, at most 36 hours old (`checkers.py:182-205`) | Missing: the Mac's `com.devon.infra-drift` writes it until increment 9 (D-9). The cell reads `unknown`. |
| `checks` | For a `launchagent:` executor, `~/Library/LaunchAgents/<label>.plist` (`wiring.py:50, 132-135`) | Missing: change-manager declares `launchagent:com.devon.change-window`. The cell reads violation or unknown. |

A non-green claim passes decomposition, which checks only the claim's shape
(`services/intake/decomposition.py:1047-1068`). Dispatch then refuses it with
`conformance_not_green` (`services/execution/dispatch.py:565-587`). A `--dry-run` pass never runs
the generator (`cli.py`, `would-propose`), so the paired reporting runs can't show the gap.
deploy.md's cut-over list makes this step 1, marked **Blocker**: an ADR-0059 amendment must
decide the three inputs before any acting container pass. As HQ decided, I added code-standards
to neither the mint nor any installation, and added no refusal.

## The crontab, and why minute 51

The crontab line is `51 * * * * lane-run bump-driver`. Minute 50, the Mac job's, belongs to the
rotation decomposer. The free minutes nearest 50 are 49 and 51, and 51 follows the minute-after
precedent of #736 (36→37) and #738 (15→16). `test_no_two_lanes_start_a_pass_in_the_same_minute`
and the image's `supercronic -test` cover it.

The line carries no flag, as the plist carries none: the driver acts unless `--dry-run` is given
(launcher lines 66-72). The bare `lane-run bump-driver` is therefore an acting pass. Once the
lane is in `.claude/hq-gate-pass.json`, an HQ cycle may run it out of schedule. The crontab's
comment and deploy.md both say so. The reporting run is `lane-run bump-driver --dry-run`.

## The state volume

The named volume is `sds-lanes-bump-driver-state`, mounted at `/var/lib/sds/bump-driver`, with
`BUMP_DRIVER_STATE_DIR` pointing at it. The image already pre-creates that directory, owned by
`orchestrator` (`lanes/Dockerfile`), and `lane-run` already passes the variable. A missing
`BUMP_DRIVER_STATE_DIR` fails the pass before any read (`sds_outcome_fail`, exit 1), as #736 and
#738 do. On the volume, `pass.lock` excludes a scheduled pass and an exec, and an old and a new
container across a redeploy (ADR line 364-370).

I left `ORCHESTRATOR_API_URL` and `CHANGE_MANAGER_URL` at the code's defaults
(`https://sds.alobar.net`, `https://change-mgr.alobar.net`). Both are public HTTPS URLs, so the
compose joins no network. A test asserts the driver receives neither variable.

## The Mac path: how I proved it unchanged

- `tests/bump_driver/test_launcher.py`, unedited, passes. It pins the outcome alarm, the
  `^sds_outcome_end "$rc" --told 2$` line at column 0, every two-argument `sds_bws_value`
  identity pairing, the absence of `export BWS_ACCESS_TOKEN`, the `PATH=` and
  `FACTORY_PYTHONPATH=` lines verbatim, and the plist's minute 50.
- In the new test file, `test_on_the_mac_*` runs the real launcher with no `BWS_ACCESS_TOKEN`.
  It checks the activation line, the six reads in order with each identity (the Healthchecks key
  on an acting pass only), one `gh auth token`, no mint, derive or clone, the Mac `PATH` and
  `FACTORY_PYTHONPATH`, the preset `BUMP_DRIVER_GITHUB_TOKEN` override, and exit codes passed
  through.
- `git diff d6a4d909 -- scripts/run-bump-driver.sh` shows the Mac lines as context only, except
  the activation block's re-indent.

## Report-don't-fix findings

### The deploy hold (ADR lines 269-271)

No code holds the bump driver during a deploy today. `deploy_orchestrator.py` names it only in
`LANE_CHECKS` (`"bump-driver": "sds-bump-driver"`, line 952) and `STATEFUL_LANES` (line 974).
Both serve the lane phases, its quiet slot and its volume, not an orchestrator deploy. Two things
protect a deploy:

- The "Nothing live (#82)" check in `prepare` and `recheck` refuses while a unit is claimed or
  executing, or while a dispatch has no later claim.
- The human rule "deploy away from :50" (deploy.md line 74).

`PROJECT.md`'s P2 item (line 235) proposes that the deploy command disable the launch agent
between `recheck` and `finish`.

Once the container runs the lane, `launchctl` has nothing to hold. I recommend re-scoping the
item to hold the lane's own `pass.lock` in the container from `recheck` to `finish`. An acting
pass that finds the lock held prints "another bump-driver pass is running; this one did nothing"
and exits 0 (`cli.py`), so nothing new is built into the driver. A dry run takes no lock, but it
acts on nothing. The holder must outlive one `vps_exec` call, for example a detached
`docker exec -d <container> flock <lock> sleep <deadline>`, and it must be released on failure.

I recommend a code PR, landed after cut-over. Before cut-over the Mac job is the one to hold, and
the item's current text fits it. The day of cut-over, the rule's minute moves from 50 to 51.

### The mint's repository list

The launcher learns the `dependency_updates` members before the token exists by running
`bump-driver repositories`, which reads the member record as SYSTEM, the bearer the pass already
reads. The read App's installation covers the three clones by decided text:

- D-8 (ADR line 615) installs the read App on "the members' repositories, the member record's
  repository and the standards repositories". The same bullet defines the standards
  repositories as "the standards repositories the lanes clone", and decision 1 (line 306) lists
  the bump driver's as project-standards and security-standards. D-14's and D-17's wording names
  the same repositories again.
- D-17 adds intent-packages (Q9).

No decided text names code-standards. The cut-over's first in-container `--dry-run` is the first
read mint over the granted members and the three clones, so it proves the installation.

### The paired pass (ADR ~1141, 1177-1178)

The container's reporting run is `lane-run bump-driver --dry-run`. It acts on nothing and, as an
outcome-alarm run without its acting mode, resolves no ping URL and pings nothing. It also runs
no generator. The ADR's line 1139 says "without its acting flag". For the bump driver the
reporting mode is a flag (`--dry-run`), not the absence of one, but the effect is the same.

A dry run takes no `pass.lock`, so the pair can run beside the Mac's hourly acting job without
either one acting twice. Run the pair away from minute 50 anyway, so the Mac's acting pass doesn't
change a record between the two reads.

The lock never spans the two machines (ADR line 370). At cut-over (line 1150), the session
confirms no Mac process holds the Mac's `pass.lock` before the bootout. At rollback (line 1184),
it confirms with `flock -n` in the container that nothing holds the volume's lock before the
bootstrap. The order, not the lock, keeps the hosts from acting at once. deploy.md's step 9
restates both checks.

### `ORCHESTRATOR_BUMP_MERGE_ENABLED` and `ORCHESTRATOR_DISPATCH_ENABLED` (1196-1198)

Nothing in the container reads either variable. `grep` over `src/bump_driver/`, `src/sds_members/`,
`src/alert_hub_client/`, `src/estate_clients/`, the launcher, `lane_run.py` and `lanes/` finds
neither. They are orchestrator settings (`src/orchestrator/config.py`, read in
`services/landing/pr_merge*.py` and dispatch). The driver sees them only as refusals:
`merge_policy_path_off` and `dispatch_disabled`, which it reports as `waiting`.

### Files a later `follow-up-mint` lane PR (ADR Amendment 10) also touches

The following files overlap with this PR. Sequence the two so one rebases on the other:

- `.bws-secrets.toml`: the SYSTEM bearer row's `lanes` list and comment, the same lines this PR
  edits. Its ping-URL row is new.
- `tests/scripts/test_deploy_lanes.py`: the `placeholders` set, until its ping-URL secret exists.
  A test's lane list may also name it.
- `docs/operations/deploy.md` ("Registered lanes") and `docs/operations/credentials.md` (SYSTEM's
  holders).
- `layers.toml`, for its launcher test.
- `scripts/deploy_orchestrator.py` `LANE_CHECKS`. This is outside my fence, so I didn't check
  whether it needs an entry.
- `tests/architecture/test_lane_image.py`, if that test lists lanes.

Its own new files are `lanes/follow-up-mint/`, `scripts/run-follow-up-mint.sh` and its test. It
needs no `lane_run.py` change unless it gets a new non-secret variable.

## Tests and checks

- `tests/scripts/test_run_bump_driver.py`: 44 passed. `tests/bump_driver/`: 248 passed,
  `test_launcher.py` unedited among them.
- Mutation pass over the launcher's guards, each run against the new test file:

  | Mutation | Result |
  |---|---|
  | Drop `unset BWS_ACCESS_TOKEN` | 3 failed |
  | Drop the `GIT_CONFIG_*` unset | 2 failed |
  | Drop `env -u BWS_ACCESS_TOKEN` on the derive | 1 failed |
  | Drop the state-directory check | 1 failed |
  | Drop `contents=read` | 2 failed |
  | Drop an opening `unset` of preset credentials | Survived |

  The surviving mutant was redundant, because every one of those variables is reassigned from
  BWS, so I deleted the line.
- `shellcheck scripts/run-bump-driver.sh`: clean, with no suppression added. I rewrote the shim
  write as a heredoc rather than a `printf` that drew SC2016 and SC2015.
- `make check` against `orchestrator_test_hq_w_211`, with `SECURITY_STANDARDS_DIR` at the
  fixtures and `PGTZ=UTC`: ruff check, ruff format and pyright are clean. Pytest:
  15582 passed, 1 skipped (exit 0, 5 minutes 11 seconds). `make check` also ran shellcheck over every script (the repository has a `.shellcheckrc`).

## Self-review

- The container block mirrors #738's shape. It uses `sds_outcome_fail` rather than bare `exit 1`,
  because this is an outcome-alarm launcher: every failure pings `/fail` with its reason.
- The Mac tail stays at top level, so the `--told 2` line stays at column 0 for
  `test_launcher.py`.
- The `repositories` subcommand reuses the pass's own reader and codes, adds no import, and
  doesn't change `_pass`.
- The shim's paths are single-quoted. A path holding a `'` would break it, but `/app` and
  `lane-run`'s `tempfile` home can't hold one.

## Concerns and follow-ups

- **Cut-over blocker (HQ D-answer A):** the ADR-0059 amendment on the conformance claim's
  container inputs. Until it lands, the lane can be created and its `--dry-run` proven, but no
  acting container pass may run.
- The shim depends on intent-packages' runtime dependencies staying within the image's venv. A
  new runtime dependency there would fail `factory` with an `ImportError`, which the driver
  reports as a refused decomposition. A `uv sync` would avoid that at the cost of PyPI reach.
- Standards gap: the handoff's Standards section doesn't cover the shim's generated script. I
  applied rule 5: it holds no secret, and only paths.

## Cut-over steps that remain

These are in deploy.md, under "Registered lanes", `bump-driver`:

1. **Blocker:** the ADR-0059 amendment deciding the conformance claim's three Mac-only inputs.
2. The orchestrator serving this release; HQ creates `sds-lane-bump-driver` without deploying
   it.
3. The VM root's keeper commands for the lane token and `bump-driver-ping-url`.
4. The registry rows: the token's row, then the ping URL's row, and a `runtime-fetch` reader for
   the launcher on the read App key's row.
5. The lane account's grants on its six rows and its ping-URL secret:
   - `221a48d5` and `b415d03b`, in `SDS Operator` (`sds-operator` on the Mac today).
   - `314f276d` (`Rotation / Keepers`), `750f737f` and `da8134b0` (`Brains - Contributor`). The
     broad identity reads these on the Mac, so each becomes a direct grant to the lane account,
     Devon's act at the identity gate.
   - `314a966c`, in `SDS / Lane Apps`.
6. The token's mint and write, then the ping URL's row made due and written.
7. A served release whose compose names the ping-URL secret's id, with `bump-driver` out of
   `placeholders`.
8. The lane's `--add`, then `lane-run --read` over all seven secrets and an in-container
   `lane-run bump-driver --dry-run`.
9. ADR cut-over steps 2 onward:
   - Copy `alerts.json` into the volume (not `pass.lock` or `mirrors/`).
   - Run the paired `--dry-run` runs away from minute 50.
   - Run the `pass.lock` checks at bootout and rollback.
   - Turn the schedule on, and retire the Mac job.

## Standards followed

| Standard | How the change meets it |
|---|---|
| Code Brain rule 3 (JSON logs to stdout, not files) | The launcher writes nothing to a log file of its own. The driver's report goes through `$SDS_OUTCOME_REPORT` (a temp file removed on exit) to stdout, as on the Mac. The driver's logging is unchanged. |
| Code Brain rule 4 (no silent swallow; no `\|\| true` over a BWS read, mint, clone or `uv sync`) | Every BWS read, the derive, the mint, each clone, the credential helper, `mkdir`, the shim write and `chmod` fail through `sds_outcome_fail`, naming the step. No `\|\| true` was added. The only ones are the Mac's existing Keychain reads, each followed by an emptiness check. No `uv sync` runs. |
| Code Brain rule 5 (never log secrets) | No token, key, header or secret value is printed. The tests assert the lane token, the minted token and every secret value are absent from output and from every argv. git's output in a clone failure holds no header, because no trace is on (`sds_git_credential` unsets it). |
| Infra rule 212 (HTTPS for a public FQDN) | The lane has no FQDN, no port and no labels. |
| Infra rules 591 and 592 (no BWS token in tracked files or history) | No token is in any file. The compose maps `${BUMP_DRIVER_BWS_ACCESS_TOKEN}`. The manifest holds UUIDs only. |
| Infra rule 593 (no inline bootstrap token) | `BWS_ACCESS_TOKEN: ${BUMP_DRIVER_BWS_ACCESS_TOKEN}` maps the resource variable. No plist or script holds one. |
| Infra rule 1032 (rotate an exposed credential) | No credential was surfaced to a log or the transcript in this session. |
| Infra rule 2 (no secrets or `.env` committed) | None committed. |
| Infra rule 3 (no `:latest`) | The image is `ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}`. |
| Infra rule 4 (no Coolify scheduled task beside the in-container scheduler) | The schedule is the lane's tracked crontab, run by the entrypoint only when `LANE_SCHEDULE_ENABLED` is `true`. |
