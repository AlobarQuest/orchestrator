Outcome: done

# Report: broker batch 1's pull request (ADR-0055 Amendment 32, item 4, step 3)

- **Handoff:** `sds-hq/state/handoffs/broker-batch-1-pr.md`.
- **Worker:** W-313.
- **Branch:** `hq/broker-batch-1`, based on `290282e0` and then rebased onto #813's merge,
  `7af40971`, as the handoff asked.
- **PR:** https://github.com/AlobarQuest/orchestrator/pull/814, ready and not merged. The code head
  is `c66bd133`, and the commit that adds this report is the PR's head.
- **Question:** I asked HQ one question, whether I could make the one-test edit in
  `tests/scripts/test_deploy_lanes.py`. HQ said yes.

## What I built, by requirement and lane

### 1. Allowlist tables (`lanes/secrets-broker/allowlist.toml`)

| Lane | UUID prefix | Registry name |
|---|---|---|
| `execution-watch` | `f793576f-…` | orchestrator-observer |
| | `b415d03b-…` | ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE (`--submit` only) |
| | `97802aac-…` | execution-watch-ping-url |
| `landing-ledger` | `f793576f-…` | orchestrator-observer |
| | `314a966c-…` | lanes-read-app-private-key |
| | `ebabdd63-…` | landing-ledger-ping-url |
| `pin-watcher` | `f793576f-…` | orchestrator-observer |
| | `b415d03b-…` | ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE (acting passes only) |
| | `314a966c-…` | lanes-read-app-private-key |
| | `872684c0-…` | pin-watcher-ping-url |

Each table equals the UUID literals on its launcher's code lines, plus its compose's
`SDS_DEADMAN_PING_URL_UUID`. `test_secrets_broker_allowlist.py` checks that.

### 2. Composes

In all three composes, I removed `BWS_ACCESS_TOKEN: ${<LANE>_BWS_ACCESS_TOKEN}` and added
`/data/sds/broker/<lane>:/run/sds-broker:ro`. execution-watch and pin-watcher keep their state
volumes, and landing-ledger keeps none, as before. Each header now says that the lane holds no
token and reads through the broker. It also says that the resource keeps its
`<LANE>_BWS_ACCESS_TOKEN` variable, mapped to nothing, until the lane tokens are revoked. Each
header says the dead-man helper reads the ping URL through the broker.

### 3. Launchers

In `run-execution-watch.sh`, `run-landing-ledger.sh` and `run-pin-watcher.sh`:

- Each launcher sources `sds-bws.sh` first, and `if ! sds_in_lane_container` replaces the
  `-z BWS_ACCESS_TOKEN` guard. The guard still runs before `sds_deadman_arm` and before any read.
  The refusal now names the socket or the token.
- `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET` now runs before each child that must not read:
  `sds_scope` and `sds-github-mint` in the ledger and the watcher.
  `unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET` runs before each program.
- The failure messages say "(the lane's socket or token)", and the header comments describe the
  socket path.
- The dead-man switch resolves its ping URL in `sds_deadman_arm`, through the socket. It does this
  before the unset, so the exit trap's ping still fires. A test asserts the success ping on a pass
  that has only the socket.
- None of the three launchers has a Mac path. Each refused to run outside the container before
  this change too.

### 4. The broker row (`.cred-consumers.toml`)

`secrets-broker-bws-token`'s `reach_secrets` gains G-100's four grants:

- `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` (orchestrator-observer)
- `97802aac-5a7f-45e8-8185-b4fc00cd318e` (execution-watch-ping-url)
- `ebabdd63-2eff-4b8e-99b7-b4fd01754eae` (landing-ledger-ping-url)
- `872684c0-899e-43b7-9c2d-b50000e88b21` (pin-watcher-ping-url)

The row's header comment names G-100. Broker reach is confirmed. `reach_projects` already holds
`b09acc9d-…` (SDS Operator, which holds `b415d03b-…`) and `b3df5419-…` (SDS / Lane Apps, which
holds `314a966c-…`). The handoff's "`b415d03b-…` (SDS Operator)" and "`314a966c-…`
(SDS / Lane Apps)" are those secrets' ids, not project ids. Every secret the three lanes read is
covered, so no stop point fired. `test_secrets_broker_row.py` pins the four new grants.

### 5. `helper_target` removed

I removed `helper_target` from `execution-watch-bws-token`, `landing-ledger-bws-token` and
`pin-watcher-bws-token`. The rows stay. Each row's comment says that the lane reads through the
broker, and that the token stays until the lane tokens are revoked (Amendment 32, item 5).
`docs/operations/rotation-executor.md`'s target table drops the three lanes' rows, which
`test_the_install_page_lists_each_target_s_container_as_derived` requires.

### 6. Identity comments

- **`.bws-secrets.toml`:** I updated the orchestrator-observer readers paragraph, the
  `sds-bridge` alert token's `--submit` and not-`--dry-run` readers, the read App key's reader
  list and grant paragraph, and the three ping-URL secrets' "Read only in the lane's container"
  lines. They now say the broker's account `lane-secrets-broker` reads, by G-100's single-secret
  grant where one applies.
- **`.cred-consumers.toml`:** I updated the observer's three `runtime-fetch` notes, the read App
  key's two notes and its header, the three ping-URL keeper notes, and the three token rows'
  `coolify-env` notes. The `coolify-env` notes now say the variable is mapped to nothing until the
  revoke sitting.

### 7. Docs

- `secrets-broker.md` has no list of broker lanes, so I didn't change it.
- `landing-audit.md`'s prerequisites are the landing-ledger lane's credentials. They named
  `SDS Operator` and a `gh auth token` fallback. I rewrote them to match how the lane works now:
  the observer bearer in `Rotation / Keepers` is read through the broker, and the GitHub token is
  minted from the read App's key. The rewrite also drops the stale backlog reference
  `113af446955b`. This is beyond the handoff's list, as one droppable hunk.
- `rotation-executor.md` renames two examples from `execution-watch` to `deploy-watcher`: the
  `helper_target` example and the `[target.<lane>]` example. execution-watch no longer has a
  target. This is beyond the handoff's list, as one droppable hunk.
- `deploy.md` is untouched by instruction. Its sections on these three lanes still describe their
  own tokens; that's the first concern listed later.

### The one edit in `tests/scripts/test_deploy_lanes.py` (HQ's answer)

`test_the_execution_watch_compose_meets_the_add_refusals` now loads
`load_allowlist(ROOT / deploy.BROKER_ALLOWLIST)` and passes it to `add_refusals`. Nothing else in
that file changed.

### Tests

- **Launcher tests:** `test_run_execution_watch.py`, `test_run_landing_ledger.py` and
  `test_run_pin_watcher.py` gain socket-path tests:
  - a pass through the socket alone, with every read through it, `bws` never run, and no child
    seeing the token or the socket marker;
  - a refused read (`bws_failed`, `not_allowed`) failing the pass with `/start` then `/fail`;
  - a dead socket failing with `no_reply` and never falling back to `bws`;
  - a socket variable that names no socket being refused.
- **Compose tests:** these assert `broker_lane`, `add_refusals` with the real allowlist,
  `compose_broker_refusals == []`, and the table's contents.
- **Stub `python`:** the ledger's and watcher's stub now passes `-I` to the real interpreter, as
  well as `-c`. The socket client runs `python -I -c`.
- **`test_pin_watcher_rows.py`:** it now asserts that the compose doesn't map the token variable.

## Tests and checks

- **`make check`** at `c66bd133`, with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_313`
  (migrated first): exit 0. Ruff, ruff format and pyright are clean. pytest collected 14,808 of
  14,819 tests (11 deselected); 14,807 passed and 1 was skipped.
- **Mutation checks:** each check was one edit, run against the seven affected test files and then
  restored from `HEAD`. The unmutated control passed 112 of 112.

  | Mutation | Failing tests |
  |---|---|
  | M1: stray `BWS_ACCESS_TOKEN: ${PIN_WATCHER_BWS_ACCESS_TOKEN}` back in the pin-watcher compose | 4: `test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket`, `test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind`, the pin-watcher compose test, `test_the_token_row_is_one_the_lane_token_adapter_acts_on` |
  | M2: `314a966c-…` dropped from `[lanes.landing-ledger]` | 2: `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads`, the landing-ledger compose test |
  | M3: `helper_target = "execution-watch"` left on its row | 3: `test_no_broker_lane_and_not_the_broker_s_own_token_names_one`, `test_each_target_is_the_service_that_loads_the_row_s_variable`, `test_the_install_page_lists_each_target_s_container_as_derived` |
  | M4: execution-watch guard reverted to `-z BWS_ACCESS_TOKEN` | 5: every execution-watch socket test except the not-a-socket refusal |
  | M5: the ledger's mint keeps `SDS_SECRETS_SOCKET` | 1: `test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it` (ledger) |
  | M6: pin-watcher unsets only `BWS_ACCESS_TOKEN` | 2: both pin-watcher socket pass tests |
  | M7: `97802aac-…` dropped from the broker row | 1: `test_the_row_declares_the_granted_projects_and_secrets` |

- **Token-shape grep** of `git diff origin/main...HEAD`, added lines only: the
  `0.<uuid>.<secret>` shape matches 0 lines. No `BWS_ACCESS_TOKEN` assignment carries a value; the
  only matches are refusal messages and tests that pass `None` or an empty string.
- **`code-standards risk`:** `high-risk`, for secrets in `.bws-secrets.toml`,
  `lanes/secrets-broker/allowlist.toml` and `tests/rotation_worker/test_secrets_broker_row.py`.
- **`vendor-review`** (`/tmp/vendor-review-k2sr7aam`): the review is complete. Claude and codex
  each approved with 0 findings, and nothing was dropped. agy: off by configuration.
- **`/code-review`** (medium, `origin/main...HEAD`): four nits.
  - I fixed two: the blank lines left by moving the `source` line, and the edited lines over 100
    columns.
  - I left the other two, for these reasons:
    - **Rotation with no `loaded` proof for the three tokens.** This matches inert landing's row,
      the decided shape. Nothing reads these tokens, and they retire at the revoke sitting.
    - **`rotation-executor.md`'s create-keeper and registry-row examples still name
      `execution-watch-bws-token`.** They're generic templates for a lane-token row, and the page's
      `helper_target` rule (line 1679) still says only a lane that loads its own token names one.
- **Security scan** from the worktree root: 0 BLOCK, 2 WARN, 1 INFO. Both WARN findings were there
  before this branch: `*-migration/` gitignore coverage, and the undeclared UUID `aaff45ca-…`,
  which isn't in the diff (grep count 0).

## Files changed

- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/landing-audit.md`
- `docs/operations/rotation-executor.md`
- `lanes/execution-watch/compose.yaml`
- `lanes/landing-ledger/compose.yaml`
- `lanes/pin-watcher/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `scripts/run-execution-watch.sh`
- `scripts/run-landing-ledger.sh`
- `scripts/run-pin-watcher.sh`
- `tests/rotation_worker/test_pin_watcher_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/scripts/test_deploy_lanes.py` (one test, by HQ's answer)
- `tests/scripts/test_run_execution_watch.py`
- `tests/scripts/test_run_landing_ledger.py`
- `tests/scripts/test_run_pin_watcher.py`
- this report

HQ's renamed question file, `broker-batch-1-pr-question-answered.md`, is untracked and stays out of
the PR.

## Self-review

- **Mac paths.** Each launcher still accepts a token lane, because `sds_in_lane_container` is true
  with a token, and the token-path tests still pass. None of the three has a Mac path.
- **Changed messages.** The ledger and the watcher keep their exit codes and their stderr lines,
  apart from the refusal's wording and the "(the lane's socket or token)" suffix.
- **Shared comments.** The shared registry comments still describe lanes that keep their own
  token. I changed only these three lanes' wording.

## Concerns

- **`deploy.md` stays stale for these lanes, by instruction.** Its sections on execution-watch,
  landing-ledger and pin-watcher still describe each lane reading with its own token, and give no
  broker step: the `install -d` directory, the broker move before the `--tag`, and the
  `lane-run --read` proof. They need the same treatment #769 gave follow-up-mint's section, in a
  later change.
- **Later root act.** After the merge, the three lanes' targets in `/etc/sds-pg-rotate/targets.toml`
  on `prod-vps` must be removed, as a root act (Amendment 32, item 4, step 3).
- **Directory ownership.** The broker directory for each lane must exist, owned by the image's
  `orchestrator` user, before the broker's move and before each lane's `--tag`. The uid isn't
  recorded in this repository; #769's report has the `docker run … id` check.
- **Standards coverage.** No standard was missing for this change.

## Standards followed

- **Code Brain 3 (logging).** I added no logging path. The launchers keep their `echo … >&2`
  lines and the dead-man helper's `[deadman]` lines, and the programs' JSON output is unchanged.
- **Code Brain 4 (error handling).** Nothing swallows a failure. A refused or dead-socket read
  leaves the value empty, and the launcher's existing check exits 1 with a named line. The
  dead-man trap pings `/fail`, and tests assert each case. The broker's reason (`bws_failed`,
  `not_allowed`, `no_reply`) goes to stderr from `sds-bws.sh`.
- **Code Brain 5 (secrets in logs).** Nothing new prints a value. The tests check that the alert
  value and the ping URL don't appear in stdout or stderr. The stubs log only a `set` marker for
  the token and the socket.
- **Infra Brain 591, 592 and 593 (no token in tracked files or history, no inline bootstrap
  token).** The token-shape grep of the diff matches 0 lines, and the scan has 0 BLOCK findings.
  The composes name variables only, and now map no token at all. Test values are placeholders
  such as `value-of-<uuid>` and `observer`, which don't match the token shape.
- **Infra Brain 1032 (`cred.exposure-rotate`).** The edited rows keep valid exposure and consumer
  fields: every row parses, and the adapter's row checks and the registry tests pass in
  `make check`. I read and surfaced no credential value. I added only UUIDs that were already in
  this repository.
- **Infra Brain 212 (HTTPS for public FQDNs).** Doesn't apply: these lanes serve no endpoint.
- **Construction-mode ruling:**
  `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`. I didn't restate it
  here.
