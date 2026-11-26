Outcome: done

# Broker batch 2's pull request: report

Handoff: `sds-hq/state/handoffs/broker-batch-2-pr.md` (goal step `broker-batch-2`, step 3; ADR-0055
Amendment 32 item 4 step 3). Worker W-315, branch `hq/broker-batch-2`, based on `origin/main` at
`8b326f2d`.

- **PR:** __PR__
- **Head:** __HEAD__
- **Merged:** no. The handoff says don't merge it.

## What I built

This PR moves three lanes onto the lane secrets broker: `unit-verifier`, `membership-check` and
`deploy-watcher`. It follows batch 1's shape (#814, `7496e7c6`) for each lane. For
`membership-check`, an outcome-alarm launcher, it also follows Phase B's bump driver. The PR takes
no live or production step.

### Requirement 1: allowlist tables

`lanes/secrets-broker/allowlist.toml` gains three tables. I read each list from the launcher's code
lines, conditional reads included, plus the ping-URL secret the compose names. Each read matches
HQ's list for that lane.

| Lane | UUID | Registry name |
|---|---|---|
| `unit-verifier` | `660d5846-…` | `orchestrator-verifier-bearer` |
| | `b415d03b-…` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` (every pass; a failed read warns and the pass runs) |
| | `314a966c-…` | `lanes-read-app-private-key` |
| | `1cfc8352-…` | `unit-verifier-ping-url` |
| `membership-check` | `f793576f-…` | `orchestrator-observer` |
| | `314f276d-…` | `change-manager/M2M_TOKEN_READ` |
| | `314a966c-…` | `lanes-read-app-private-key` |
| | `5fc44897-…` | `membership-check-ping-url` |
| `deploy-watcher` | `3b9503da-…` | `change-manager/M2M_TOKEN_OBSERVE` |
| | `f793576f-…` | `orchestrator-observer` |
| | `314a966c-…` | `lanes-read-app-private-key` |
| | `4f7f3d8b-…` | `deploy-watcher-ping-url` |

Two existing tests pass on the tracked files:
`test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` and
`test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket`.

### Requirement 2: composes

Each of the three composes changes in the following ways:

- It drops `BWS_ACCESS_TOKEN: ${<LANE>_BWS_ACCESS_TOKEN}`.
- It adds `/data/sds/broker/<lane>:/run/sds-broker:ro`, and nothing else at, under or above
  `/data/sds/broker`.
- `unit-verifier` keeps its state volume `sds-lanes-unit-verifier-state`.
- `membership-check` and `deploy-watcher` keep no state, so the bind is their one mount.
- Each header comment says the lane holds no token, reads through the broker, and keeps its Coolify
  variable, mapped to nothing, until the lane tokens are revoked.

### Requirement 3: launchers

The three launchers follow batch 1's pattern:

- Each sources `sds-bws.sh` first.
- The host check and the empty-token check are `sds_in_lane_container`. The refusal reads `FATAL:
  no secrets-broker socket or BWS_ACCESS_TOKEN`.
- Every child that must not read runs under `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET`, and
  the final `unset` names both variables.
- Each read-failure message now says "(the lane's socket or token)".

The children differ by lane:

- **`unit-verifier`:** the `unit-verifier repositories` derivation and `sds-github-mint`. The pass
  runs after the `unset`.
- **`membership-check`:** the `sds_scope ledger` derivation, `sds-github-mint`, and the anonymous
  `git` clone, whose `env -u` list gains `SDS_SECRETS_SOCKET`. The program runs after the `unset`.
  The launcher's second, inner token test (`if [ -n "${BWS_ACCESS_TOKEN:-}" ]`) now reads `if
  sds_in_lane_container`, as in the bump driver. Without that change, a socket pass would skip every
  read.
- **`deploy-watcher`:** the `deploy-watcher repositories` derivation and `sds-github-mint`. `watch`
  and `recheck` run after the `unset`. The preset-bearer behavior is unchanged.

### Requirement 4: the broker row's reach

The `secrets-broker-bws-token` row's `reach_secrets` gains exactly four single-secret grants:

- `3b9503da-eb7e-401d-b4a7-b4a400c07efb` (`change-manager/M2M_TOKEN_OBSERVE`)
- `1cfc8352-c960-4afc-bd49-b4ff00e8783e` (`unit-verifier-ping-url`)
- `5fc44897-b4df-44ae-8491-b5000129d9be` (`membership-check-ping-url`)
- `4f7f3d8b-a83e-4ae5-a665-b4ff0004b875` (`deploy-watcher-ping-url`)

The row's comment says Devon gives these at an HQ gate before the release. It names no gate id or
date, because neither exists yet. The row already covers every other read:

- **Single-secret grants already in the row:** `f793576f-…` (batch 1) and `314f276d-…` (Phase B).
- **Project grant on `SDS Operator` (`b09acc9d-…`):** `b415d03b-…` and `660d5846-…`, both in that
  project in `.bws-secrets.toml`.
- **Project grant on `SDS / Lane Apps` (`b3df5419-…`):** `314a966c-…`. Phase B's bump driver
  already reads it through the broker.

No lane reads a secret outside the row's reach plus these four, so that stop point didn't fire.
`test_secrets_broker_row.py`'s `SECRETS` pins the four grants.

### Requirement 5: `helper_target`

I removed `helper_target` from `deploy-watcher-bws-token`, `unit-verifier-bws-token` and
`membership-check-bws-token`. The rows stay, each with batch 1's comment that the token stays until
the revoke sitting (Amendment 32 item 5). Each row's `coolify-env` note says the compose maps the
variable to nothing. Each ping-URL keeper note says the executor writes it and the broker reads it.

### Requirement 6: identity comments

`.bws-secrets.toml` now says `lane-secrets-broker` reads each of these lanes' secrets in the lane
containers. That covers the following entries:

- `orchestrator-verifier-bearer`, through the `SDS Operator` project
- `orchestrator-observer`'s broker-lane list
- the change-manager bearers' block (`membership-check` moves to the broker list)
- `change-manager/M2M_TOKEN_OBSERVE`
- the `sds-bridge` sender's `run-unit-verifier` reader
- `lanes-read-app-private-key`'s token and broker reader lists
- the three ping-URL secrets

In `.cred-consumers.toml`, the following say "through the lane secrets broker":

- the three lanes' container `runtime-fetch` notes, on `orchestrator-verifier-bearer`
  (`unit-verifier`) and `orchestrator-observer-bearer` (`deploy-watcher`, `membership-check`)
- the read App key's three consumer notes and its block comment
- the broker row's grant list

### Requirement 7: docs

- **`docs/operations/secrets-broker.md`:** a lane list (the allowlist's 11 tables, held equal to
  the tokenless composes by `test_secrets_broker_allowlist.py`). "What the broker doesn't change"
  now names the six lanes still on a token: `bump-proposer`, `change-proposer`,
  `revision-watcher`, `rotation-decomposer`, `rotation-proposer` and `work-carrier`.
- **`docs/operations/deploy.md`:** six lane sections now say the broker route, this batch's three
  and the three batch 1 left on the token route (`execution-watch`, `landing-ledger`,
  `pin-watcher`). Each says its secrets and its ping URL are read through the broker, and names its
  bind and allowlist table. "Mounts no volume" became "mounts no state volume" where a lane's only
  mount is the bind. I also fixed one stale phrase in `inert-landing`'s section ("read with the
  lane's one token"), since that lane has been on the broker since Phase B.
- **`docs/operations/rotation-executor.md`:** two changes.
  - The `helper_target` example and the `[target.…]` snippet that batch 1 pointed at
    `deploy-watcher` now name `revision-watcher`, a lane still on its token.
  - `deploy-watcher`, `unit-verifier` and `membership-check` leave the helper-target table. The
    rule paragraph's stale "twelve tokens" and "two rows name none" now say every broker lane's row
    names none.

## Tests and checks

- **`make check`** in the worktree, database `orchestrator_test_hq_w_315`: green (exit 0). Ruff check, ruff format and pyright are clean (`0 errors`), and shellcheck is clean. pytest collected 14829 tests: 14828 passed and 1 skipped (`14828 passed, 1 skipped, 11 warnings in 450.80s`).
- **Targeted run** before `make check`: 303 passed, from the three launcher tests, the three rows
  tests, the broker row, the helper-target, allowlist and `test_deploy_lanes.py` tests.
- **Outside tests:** the first full run of `tests/scripts`, `tests/rotation_worker` and
  `tests/architecture` before the test edits had 10 failures, all in this batch's own files (the
  three launcher tests, the three rows tests and the broker row test). Each failure came from the
  token mapping's removal, the grant list or the message wording. No other test read these
  composes and failed: `test_deploy_lanes.py` passed unchanged, so that stop point didn't fire.
- **Test additions:** each launcher test runs on the stand-in broker (`conftest.py`'s `broker` and
  `dead_socket`) and covers the following cases:
  - A full pass reads every secret through the socket, `bws` never runs, and each child logs
    `socket=` (unset).
  - A refused secret fails the pass. For `unit-verifier`, a refused alert sender warns and runs,
    and a refused key runs the pass without a token and exits 3, as its token path does.
  - A dead socket fails the pass and never falls back to `bws`.
  - `SDS_SECRETS_SOCKET` that names a non-socket refuses.
  - The outside-the-container refusal passes with the token unset and with it empty.
  - Each compose test asserts `broker_lane`, `add_refusals` with the allowlist,
    `compose_broker_refusals`, the allowlist's exact set, and the service with its bind.

### Mutation checks

Each mutation went on top of the committed change, ran against the named tests, and was undone by
inverse edit, with `__pycache__` cleared around each one. The test set is the 303 targeted tests.
Every mutation was caught.

| Mutation | Failing tests |
|---|---|
| M1: `BWS_ACCESS_TOKEN: ${DEPLOY_WATCHER_BWS_ACCESS_TOKEN}` back in the deploy-watcher compose | 5: `test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket`, `test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind`, `test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` (`test_deploy_lanes.py`), the deploy-watcher compose test, `test_the_token_row_is_one_the_lane_token_adapter_acts_on` |
| M2: `3b9503da-…` dropped from `[lanes.deploy-watcher]` | 2: `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads`, the deploy-watcher compose test |
| M3: `helper_target = "unit-verifier"` left on its row | 3: `test_no_broker_lane_and_not_the_broker_s_own_token_names_one`, `test_each_target_is_the_service_that_loads_the_row_s_variable`, `test_the_install_page_lists_each_target_s_container_as_derived` |
| M4: membership-check's inner guard back to `-n BWS_ACCESS_TOKEN` | 5: every membership-check socket test except the not-a-socket refusal |
| M5: unit-verifier's mint keeps `SDS_SECRETS_SOCKET` | 1: `test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it` (unit-verifier) |
| M6: `4f7f3d8b-…` dropped from the broker row | 1: `test_the_row_declares_the_granted_projects_and_secrets` |

### Token-shape grep

I grepped the added lines of `git diff 8b326f2d..HEAD`:

- The BWS token shape (`0.`, a UUID, a dot) matches 0 lines.
- GitHub PAT, Anthropic key, private-key header, and `BWS_ACCESS_TOKEN=` with a literal value
  match 0 lines.
- Every UUID the diff adds is a BWS secret id already in `.bws-secrets.toml`. A secret id isn't a
  secret.

### Reviews

- **`code-standards risk`:** `high-risk`, for secrets in `.bws-secrets.toml`,
  `lanes/secrets-broker/allowlist.toml`, `scripts/run-deploy-watcher.sh` and
  `tests/rotation_worker/test_secrets_broker_row.py`.
- **`vendor-review`** (`/tmp/vendor-review-6xmkvf47`): complete. Claude and Codex each approved
  with 0 findings, and nothing was dropped. agy: off by configuration.
- **`/code-review`** (medium, `origin/main...HEAD`): two nits. I didn't change the code for either.
  - **The inner `if sds_in_lane_container` in `run-membership-check.sh` is redundant**, since the
    top guard already exits. I kept it because it's the bump driver's shape (Phase B), and it keeps
    the diff to a one-line condition change instead of re-indenting about 80 lines.
  - **The three tokens keep a `PATCH`-only reissue proof with no `loaded` read.** That's the
    decided shape for a broker lane's row (inert landing, batch 1). Nothing reads these tokens, and
    they retire at the revoke sitting.

## Files changed

- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/deploy.md`
- `docs/operations/rotation-executor.md`
- `docs/operations/secrets-broker.md`
- `docs/superpowers/reports/broker-batch-2-pr-report.md`
- `lanes/deploy-watcher/compose.yaml`
- `lanes/membership-check/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `lanes/unit-verifier/compose.yaml`
- `scripts/run-deploy-watcher.sh`
- `scripts/run-membership-check.sh`
- `scripts/run-unit-verifier.sh`
- `tests/rotation_worker/test_deploy_watcher_rows.py`
- `tests/rotation_worker/test_membership_check_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/rotation_worker/test_unit_verifier_rows.py`
- `tests/scripts/test_run_deploy_watcher.py`
- `tests/scripts/test_run_membership_check.py`
- `tests/scripts/test_run_unit_verifier.py`

## Self-review

- **Ordering for HQ.** The release that carries this change must follow Devon's four grants, or
  the three lanes' first broker passes fail their reads. `deploy-watcher` would fail every read
  of `3b9503da-…`, and each lane would fail its ping-URL read, which silences its check until the
  grace runs out. The broker row records the grants as given. Like batch 1, it is ahead of the
  live grants until the HQ gate.
- **membership-check with a dead socket** pings nothing. The ping URL is itself read through the
  socket, so the outcome alarm has no URL. Its check goes down after its grace, which is the
  documented behavior for a key a lane can't read (`scripts/CLAUDE.md`). A test pins it.
- **The membership-check header** said the launcher "refuses, through the outcome alarm". The code
  refuses with exit 1 before `sds_outcome_begin`, so the comment and the test name now say
  "before".
- I didn't change ADR-0055.

## Concerns

- **Stale Keychain notes.** `.cred-consumers.toml`'s `bws-sds-operator-token` row still has
  Mac-era `runtime-fetch` consumer notes for `run-deploy-watcher.sh`, `run-membership-check.sh`
  and `run-unit-verifier.sh`: "reads Keychain Claude/BWS_ACCESS_TOKEN_SDS each run". Batch 1 left
  the matching notes for its lanes too. They describe the narrow identity's Mac readers, not the
  container. Requirement 6 is about the containers, so I left them. Whether they still hold after
  the Mac jobs retired is a separate registry question.
- **Standards coverage.** The handoff's standards cover every concern this change touches. None
  is uncovered.

## Standards followed

- **Code Brain 3 (logging).** I added no logging path. The launchers keep their existing stderr
  `FATAL:` and `WARNING:` lines and the dead-man and outcome helpers. Only the wording of existing
  messages changed.
- **Code Brain 4 (error handling).** A failed read still fails the pass on every path the token
  path failed. Tests pin the bearer, change-manager and key refusals on the socket for each lane,
  and a dead socket exits 1. The one deliberate swallow is `unit-verifier`'s `|| true` on the
  alert sender. It is unchanged, keeps its inline justification ("A FAILED FETCH NEVER STOPS A
  ROTATION"), logs a `WARNING`, and the pass exits 3 when alerts can't be sent. A test pins it on
  the socket too.
- **Code Brain 5 (security).** Nothing new prints a value. The new messages name the read path
  ("the lane's socket or token") and the broker's own one-line refusal (UUID and reason code). The
  tests assert that no secret value reaches stdout or stderr.
- **Infra Brain 591, 592, 593 (`bws.no-token-in-tracked-files`, `bws.no-token-in-git-history`,
  `bws.bootstrap-token-not-inline`).** No token or token-shaped literal is in code, composes, tests
  or fixtures. The grep counts are in "Token-shape grep". The composes name only variables and
  secret ids. Tests use placeholder strings (`a-lane-token`, `value-of-<uuid>`).
- **Infra Brain 1032 (`cred.exposure-rotate`).** The registry rows I edited keep valid exposure
  and consumer fields. I removed only `helper_target`, and the `bws_lane_token` adapter reads the
  rows unchanged (`test_the_token_row_is_one_the_lane_token_adapter_acts_on`,
  `test_lane_token_helper_targets.py`). I read no credential and surfaced none.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable. The change adds no endpoint or
  FQDN.
- **Construction mode.** This work proceeds under the construction-mode ruling,
  `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`.
