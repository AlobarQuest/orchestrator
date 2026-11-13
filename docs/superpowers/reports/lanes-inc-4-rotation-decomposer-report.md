Outcome: done

# Build report: the rotation decomposer's lane files and launcher (ADR-0059 increment 4, step 3f-3)

Worker W-129. Branch `hq/lanes-inc-4-rotation-decomposer`, base `origin/main` at `2cf43757`. PR:
PR #689 (opened and marked ready, not merged).

## What changed

- `lanes/rotation-decomposer/compose.yaml`: one service, `rotation-decomposer`, on the lane image.
  `BWS_ACCESS_TOKEN: ${ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN}`,
  `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}`,
  `ROTATION_DECOMPOSER_STATE_DIR: /var/lib/sds/rotation-decomposer`, and an empty
  `SDS_DEADMAN_PING_URL_UUID` placeholder. It mounts the named volume
  `sds-lanes-rotation-decomposer-state` there and uses `restart: unless-stopped`. It has no ports,
  networks, or labels. The header comment explains the placeholder, as #686's does.
- `lanes/rotation-decomposer/crontab`: `50 * * * * lane-run rotation-decomposer`, with no flags.
- `scripts/run-rotation-decomposer.sh`: when `BWS_ACCESS_TOKEN` is set (the container), the
  launcher skips activation and reads no Keychain item. It reads all three secrets with the lane's
  one token through one-argument `sds_bws_value`, and reads the alert sender only on acting
  passes. It runs `unset BWS_ACCESS_TOKEN` before the program. The Mac path keeps its reads, the
  identity for each read, its order, and its fatal messages. In the container, each fatal message
  ends with ` (the lane's token)`. `--dry-run` handling, the `EXIT CODES` block,
  `sds_outcome_begin`/`sds_outcome_end` and the 0-3 mapping are unchanged. The header describes
  both hosts.
- `.bws-secrets.toml`: `"rotation-decomposer"` is added to `lanes` on the SYSTEM bearer
  (`221a48d5-…`, its first `lanes` field), the change-manager READ bearer (`314f276d-…`), and the
  `sds-bridge` sender (`b415d03b-…`). The comments are updated to match.
- `tests/scripts/test_run_rotation_decomposer.py` (new): 23 tests, described in the following
  "Tests and checks" section.
- `tests/rotation_decomposer/test_launcher.py`: the sender-fetch assertion now follows the
  `_narrow_secret` helper and pins that the helper is the narrow identity on the Mac.
- `tests/scripts/test_deploy_lanes.py`: `rotation-decomposer` is added to `placeholders`.
- `layers.toml`: the new test is placed in the `rotation` layer, beside the launcher.
- `docs/operations/rotation-executor.md`: one sentence naming the container lane.
- `docs/operations/deploy.md`, "Registered lanes": a section for the rotation decomposer and its
  remaining cut-over steps, after the change proposer's section.

The image needed no change. `lanes/Dockerfile` already installs `git` (pinned
`1:2.47.3-0+deb13u1`) and creates `/var/lib/sds/rotation-decomposer` owned by `orchestrator`. It
copies every `scripts/run-*.sh`, and `scripts/lane_run.py` already lists
`ROTATION_DECOMPOSER_STATE_DIR` in `STATE_VARIABLES`. `rotation-decomposer` is a `pyproject.toml`
entry point in the orchestrator image's venv. `deploy_orchestrator.py` already had the lane in
`LANE_CHECKS` (`sds-rotation-decomposer`), `STATEFUL_LANES`, and `LANES`.

## The three secrets and how each is read in the container

All three are read with the lane's one token (`BWS_ACCESS_TOKEN`). `sds_bws_value` takes it from
the environment, so the token is never a process argument. No Keychain item is read. The
`/usr/bin/security` calls sit only in the Mac branch, and a test pins that.

| Secret | UUID | When | Exported as |
|---|---|---|---|
| Orchestrator SYSTEM bearer | `221a48d5-3f29-4898-b300-b4820140c880` | Every pass | `ORCHESTRATOR_API_TOKEN`, key id `orchestrator-system` |
| Change-manager READ bearer | `314f276d-55ca-4ddc-a24d-b4a3013508cd` | Every pass | `CHANGE_MANAGER_TOKEN` |
| `sds-bridge` alert sender | `b415d03b-f92d-46b1-8a9d-b4f10103df49` | Acting passes only (no `--dry-run`) | `SDS_BRIDGE_ALERT_TOKEN` |

On the Mac, the narrow `sds-operator` identity still reads the SYSTEM bearer and the sender, and
the broad identity (`BWS_ACCESS_TOKEN_VPS_BACKUP`) still reads the READ bearer. At cut-over, the
broad identity's read of the READ bearer becomes a direct grant to the lane account. That is
Devon's act at the lane's identity gate, not part of this change.

## The GitHub-call check

The decomposer makes no GitHub API call and needs no GitHub token. I checked this in four ways:

- `src/rotation_decomposer/` builds three HTTP clients: change-manager and the orchestrator
  (`client.py`, through `estate_clients.confined`) and alert-hub (`alerts.py`, through
  `alert_hub_client`). None of them targets GitHub.
- The modules it imports from `rotation_worker` (`contract`, `destinations`, `envelope`, `plans`,
  `registry`, `types`, `grants`, and `git_mirror`, which also imports `config`) make no HTTP call.
  The `github` strings in `types.py` and `config.py` are credential-type names and config fields.
  `estate_clients/confined.py` names no GitHub host, and `alert_hub_client`'s one `github` match
  is Dependabot's `github_actions` branch segment in a docstring.
- `tests/architecture/test_rotation_edges.py` already pins the decomposer's edge as
  `github=frozenset()`.
- The only GitHub contact is `RepoMirror`, which fetches `https://github.com/<repo>.git` for the
  four `REGISTRY_REPOSITORIES` (`infraops-mcp-server`, `orchestrator`, `change-manager`, and
  `factory-runner`). Its `_run` passes only `PATH`, `HOME` and three git settings, so no credential
  can reach it. The claim that these repositories are public rests on ADR-0059 decision 5 and the
  mirrors paragraph. I made no anonymous probe, because that would be a live call. The
  cut-over's `--dry-run` in the container is the first live proof.

No private mirror and no GitHub call exist, so I didn't need to stop with a question.

## The crontab minute and why

The crontab runs at minute 50, every hour, with no flags. The Mac plist has `StartInterval 3600`
and no arguments, so every scheduled Mac pass is an acting pass. Lanes already start at minutes 0,
15, 30 and 45 (execution-watch), 2-52/10 (unit verifier), 20 (deploy watcher), 25 (pin watcher at
07:25), 30 (landing ledger at 07:30), 35 (change proposer), 40 (revision watcher), and 47
(membership check at 07:47). Minute 50 is free, and a test pins it through `lane_start_minutes`.
I picked a late minute because the decomposer consumes what the work carrier registers. The
carrier has no container minute yet, and giving it an earlier free minute (for example 5 or 10)
would let a newly carried rotation be decomposed in the same hour.

## The state volume

`sds-lanes-rotation-decomposer-state` is mounted at `/var/lib/sds/rotation-decomposer`, and
`ROTATION_DECOMPOSER_STATE_DIR` points there (ADR-0059 lane-state table, row
`rotation-decomposer`). `lane-run` passes the variable through, and `alerts.py` reads it
(`STATE_DIR_ENV`). The files are `alerts.json` and `approval-since.json`, copied from the Mac's
`~/Library/Application Support/sds/rotation-decomposer/` at cut-over step 2. If they aren't copied,
alerts the Mac fired never resolve and approval-wait clocks restart. `deploy.add_refusals` accepts
the compose, including the stateful-lane volume rule.

The registry mirrors aren't state. `default_mirror_root("rotation-decomposer")` resolves to
`$HOME/.cache/rotation-decomposer/mirrors`, and `lane-run` gives each pass a new empty `HOME` with
no `XDG_CACHE_HOME`, which a test pins. So each pass makes four fresh bare clones and discards
them. `_clone` creates the parent directories. ADR-0059 states this refetch explicitly
(decision 1, the paragraph before "Lane state").

## Paired pass (item 6)

The ADR answers this, so no lane-run or deploy-script support is missing:

- Cut-over step 2 (ADR-0059 lines 1133-1141): "run the lane's reporting mode (the invocation that changes no record) on the
  Mac and in the container within the same minute". It then lists "an outcome-alarm launcher run
  without its acting flag, which resolves no ping URL (bump driver, bump proposer, change proposer,
  rotation decomposer and work carrier)" under "It pings nothing".
- For the decomposer, acting means no `--dry-run`. So the paired pass is
  `scripts/run-rotation-decomposer.sh --dry-run` on the Mac beside
  `lane-run rotation-decomposer --dry-run`, a one-off `docker exec` in the container. The
  container's schedule is still `false` at that point. A `--dry-run` pass proposes nothing
  (`_revision` in `src/rotation_decomposer/cli.py` returns `would-propose` before
  `orchestrator.propose`; `tests/rotation_decomposer/test_pass.py::test_a_dry_run_proposes_nothing`
  pins it), sends no alert (`main` calls `_tell_a_person` only when not a dry run), reads no sender
  token and pings nothing (`sds_outcome_begin ... "$_acting"` with 0;
  `tests/scripts/test_run_rotation_decomposer.py::test_a_dry_run_reads_no_sender_token_and_pings_nothing`
  pins the launcher half).
- Neither host proposes during the comparison, and the ADR's rule that "the Mac agent and the
  container never run the same lane at once" holds. The Mac agent is booted out before
  `LANE_SCHEDULE_ENABLED` turns on (cut-over steps 2-3; the rule is ADR-0059 line 1177).
- Rollback ("Rollback for one lane", ~1195-1205): no ADR-0046 switch covers the decomposer, which
  proposes as SYSTEM. A rollback that can't wait for the quiet slot is HQ's hand redeploy. The
  `deploy.md` section says so.

## The outcome alarm with no ping URL

The check is `sds-rotation-decomposer`, an outcome alarm that pings only on acting passes. With
`SDS_DEADMAN_PING_URL_UUID` empty, `sds_deadman_resolve` falls back to the lookup by check name.
That lookup needs the broad Keychain identity, which the container doesn't have. An acting pass
logs `[rotation-decomposer] no SDS_DEADMAN_PING_URL_UUID and no broad BWS identity — alerting
disabled this run`, runs the pass, and pings nothing. A `--dry-run` pings nothing anyway. A test
pins this, using a stand-in broad identity so that the real Keychain stays out of the test on a
macOS host. Until a later change writes the keeper's id, the container's check can't hear the lane.
The schedule must stay off until then, and the `deploy.md` steps give that order.

## Tests and checks

- `make check` (ruff, pyright, and the full suite against `orchestrator_test_hq_w_129`, migrated
  to head): green (exit 0): `All checks passed!` from ruff and pyright, then **14831 passed, 1 skipped** in 297 s.
- CI on PR #689 (head `33446291`): `Quality` passed, with **14809 passed, 23 skipped**,
  the same 14,832 collected as the local run. The other skips are environment skips outside this
  change; the new test file has no skip markers. `Rollout transcription currency` and `Runner
  consumer compatibility` also passed.
- The first `make check` stopped at ruff E501 (a 101-character line in the new test). I wrapped
  the line, and the second run is the green one above.
- Targeted run before `make check`: 525 tests across the decomposer, lane, deploy-lane, layer,
  image, edge, and dead-man tests, plus the change-proposer, unit-verifier and pin-watcher launcher
  tests. All passed after the new files were staged.
- Mutation checks on the launcher. Removing `unset BWS_ACCESS_TOKEN` failed 2 tests. Passing an
  identity to the container's `_broad_secret` failed 4 tests. I restored the launcher after each
  mutation, and `git diff` shows only the intended change.
- The 23 tests in `tests/scripts/test_run_rotation_decomposer.py` cover the following:
  - Container acting pass: exactly the ping-URL secret and the three secrets, each read with the
    lane token. The program gets every secret, the key id and the state directory, and no lane
    token. Pings `/start` then success. No secret value in the output, and `gh` is never called.
  - Container `--dry-run`: only the two bearers are read, the alert is unset, and nothing pings.
  - The container ignores Mac identity overrides and logs no activation line.
  - Keychain calls appear only inside the Mac branch.
  - Each of the three secrets refused in the container: exit 1 with no pass, `/fail`, and a
    message naming the lane's token.
  - Empty ping UUID: the pass runs, pings nothing, and says so.
  - Exit codes 0-3 pass through, and 127 becomes 1.
  - Mac acting pass and Mac `--dry-run`: each secret is read with its own identity, the sender
    only when acting, activation is logged, and an unchanged fatal message.
  - The state variable and `lane-run`, the mirror root under `lane-run`'s `HOME`, and git plus the
    state directory in the image.
  - The compose's exact service and `add_refusals`, the crontab line against the plist, minute
    uniqueness, and the manifest's lane secrets as named in the launcher.

## Self-review

`/code-review` (medium) returned five findings. None changed the code:

1. The container's `_narrow_secret` and `_broad_secret` have identical bodies. I kept the pair
   because it mirrors the Mac's two identities read for read, so the Mac branch and the shared
   read lines stay unchanged. Merging them would require passing an identity variable at every
   call.
2. `test_launcher.py` pins source text. That file already pinned exact lines; I kept its three
   properties and moved the assertions to the new helper. The behavioral test covers the same
   ground.
3. The Keychain structural test finds the branch end with the first column-0 `fi` after `else`.
   That is true of the launcher's layout, and a misplaced Keychain read would also fail the
   container behavioral test, which asserts every read uses the lane's token.
4. The empty ping-URL placeholder is deliberate, per the handoff and the `deploy.md` ordering.
5. The tests depend on a repo-local `.venv/bin/python`, the same pattern as the change-proposer and
   unit-verifier launcher tests.

## Concerns and follow-ups

- The SYSTEM bearer gets its first `lanes` entry. A container identity will now hold
  `orchestrator-system`, the widest M2M role, on a VPS rather than the Mac. That is the decision
  ADR-0059 records (Moves table row ~785). The lane account's grant on it, in BWS project
  `SDS Operator`, is a cut-over act.
- Each pass makes four fresh bare clones under `git_mirror.TIMEOUT_SECONDS` (120 s each). If clone time becomes a problem, ADR-0059's increment 5
  note on fork hardening's cache applies here too: point the mirror root into the volume by its
  own decision.
- On the Mac, a run with an ambient `BWS_ACCESS_TOKEN` now takes the container branch. The plist
  sets only `PATH` and `HOME`, so launchd runs are unaffected. However, `sds-hq chain` also starts
  this job. If HQ's chain runs it with a `BWS_ACCESS_TOKEN` in its environment, the run skips
  activation and reads all three secrets with that token. HQ should confirm that the chain's
  environment doesn't carry one before the Mac job goes out.
- Change-manager's `.cred-consumers.toml` (a registry in change-manager, not here): I didn't name
  a container reader on the READ bearer's row as a cut-over step, because the membership check's
  steps, for the same bearer, name none. HQ can confirm whether change-manager's registry wants one
  for either lane.
- `docs/operations/rotation-executor.md` now says the container lane exists and that the Mac job
  is the lane until cut-over.
- Standards gap: the handoff's Standards section has no rule on a lane's crontab minute or on
  ordering lanes that depend on each other (carrier before decomposer). I followed the earlier
  lanes' "no shared start minute" convention.

## Cut-over steps that remain

These are in order, each in its own change or act (ADR-0059, "Cut-over for one lane"; also in
`deploy.md`):

1. The orchestrator serves this release. HQ then creates `sds-lane-rotation-decomposer` from the
   compose without deploying it.
2. The VM root runs the keeper commands for the lane token and `rotation-decomposer-ping-url`.
3. The lane's registry rows land in this repository, in two changes: the token's row, then the
   ping URL's row (not part of this change, as the handoff requires).
4. The lane account gets grants on its three secrets and its ping-URL secret. **The change-manager
   READ bearer (`314f276d-…`), which only the broad identity reads today, becomes a direct grant to
   the lane account: Devon's act at the identity gate.**
5. Devon mints the token and the executor writes it. Then the ping URL's row is made due and the
   executor writes the URL (ADR-0055 amendment 23).
6. A served release whose compose names the ping-URL secret's id, with `rotation-decomposer`
   removed from `test_deploy_lanes.py`'s `placeholders`.
7. The lane's `--add`, then `lane-run --read` over all four secrets (the only sender read before
   the first acting pass) and a container `--dry-run`. The dry run also proves the anonymous
   mirror fetch from the VPS.
8. Cut-over step 2 onward: copy `alerts.json` and `approval-since.json` into the volume, run the
   paired `--dry-run` reporting runs, turn on the schedule, and retire the Mac job. The latter is
   coordinated with `sds-hq chain`, which starts this job.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files)**: no new log destination. The
  launcher writes its report to stdout through `sds_outcome_end`, and supercronic emits JSON for
  the container (`-json`). No file logging was added. The Mac plist's log file is unchanged and
  predates this change.
- **Code Brain rule 4 (no silent swallow; no `|| true` hiding a failed BWS read)**: every BWS read
  is followed by its own empty check, which calls `sds_outcome_fail` (exit 1, `/fail` with the
  message). The only `|| true` lines are the Mac's existing Keychain lookups, and each is followed
  by an empty check that fails the pass. Tests pin that a refused read exits 1 for each secret.
- **Code Brain rule 5 (never log secrets)**: values go only into variables. The container reads
  pass the token through the environment, never as an argument. A test asserts that no secret
  value or token appears in stdout or stderr.
- **Infra 212 (HTTPS)**: the lane has no FQDN. It calls `https://sds.alobar.net`, change-manager,
  alert-hub and `https://github.com` (the mirrors), all over HTTPS, through unchanged defaults.
- **Infra 591, 592 (no BWS token in tracked files or history)**: no token was written. Only BWS
  UUIDs (ids, not secrets) and the resource variable name appear. The test fixtures use the
  placeholder string `a-lane-token`.
- **Infra 593 (bootstrap token never inline)**: the compose maps
  `BWS_ACCESS_TOKEN: ${ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN}` from the resource variable only, and
  `add_refusals` checks that mapping.
- **Infra 1032 (exposed credential rotates now)**: no credential was surfaced. I made no live
  call and read no real secret.
- **Deployment (no Coolify scheduled task beside an in-container scheduler)**: the schedule is the
  tracked crontab that supercronic runs, gated by `LANE_SCHEDULE_ENABLED`. No Coolify scheduled
  task exists.
