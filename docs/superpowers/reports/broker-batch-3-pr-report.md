Outcome: done

# Broker batch 3's pull request: report

Handoff: `sds-hq/state/handoffs/broker-batch-3-pr.md` (goal step `broker-batch-3`, step 3; ADR-0055
Amendment 32 item 4 step 3). Worker W-323, branch `hq/broker-batch-3`, based on `origin/main` at
`14fcb47b`.

- **PR:** [AlobarQuest/orchestrator#821](https://github.com/AlobarQuest/orchestrator/pull/821)
- **Head:** the code head is `29471bd2`. The later commits change only this report, and the last
  of them is the PR's head.
- **Merged:** no. The handoff says don't merge it.

## What I built

This PR moves three lanes onto the lane secrets broker: `change-proposer`, `rotation-decomposer`
and `rotation-proposer`. It follows batch 2's shape (#817, `259ea179`). For `change-proposer` and
`rotation-decomposer`, both outcome-alarm launchers, it follows `run-membership-check.sh` from that
batch. The PR takes no live or production step.

### Requirement 1: allowlist tables

`lanes/secrets-broker/allowlist.toml` gains three tables. I read each list from the launcher's code
lines, including conditional reads, plus the ping-URL secret named in the compose. I also checked
every helper and child process each launcher starts. None of them reads BWS: the `change-proposer`,
`rotation-decomposer` and `rotation-proposer` programs, `sds-github-mint`, git and infraops'
`cred-findings` all run after the token is unset, or with it stripped. Each list matches HQ's.

| Lane | UUID | Registry name |
|---|---|---|
| `change-proposer` | `acccb346-…` | `change-manager/M2M_TOKEN_PROPOSE` (`--submit` only) |
| | `314a966c-…` | `lanes-read-app-private-key` |
| | `f793576f-…` | `orchestrator-observer` |
| | `7651ccc2-…` | `change-proposer-ping-url` |
| `rotation-decomposer` | `221a48d5-…` | `orchestrator-system-bearer` |
| | `314f276d-…` | `change-manager/M2M_TOKEN_READ` |
| | `b415d03b-…` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` (acting passes only) |
| | `936bbbf2-…` | `rotation-decomposer-ping-url` |
| `rotation-proposer` | `acccb346-…` | `change-manager/M2M_TOKEN_PROPOSE` (`--submit` only) |
| | `f793576f-…` | `orchestrator-observer` (`--submit` only) |
| | `b415d03b-…` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` (`--submit` only) |
| | `b8f547b9-…` | `lanes-push-app-private-key` (`--submit` only) |
| | `506a5436-…` | `rotation-proposer-ping-url` |

`221a48d5-…` is named `ORCHESTRATOR_M2M_CREDENTIALS` in `.bws-secrets.toml`, and its
`.cred-consumers.toml` row id is `orchestrator-system-bearer`. I used the row id because the
existing tables for inert landing, estate landing and the bump driver already use it.

### Requirement 2: composes

Each compose has no `BWS_ACCESS_TOKEN` mapping and mounts `/data/sds/broker/<lane>:/run/sds-broker:ro`.
Nothing else is mounted at, under or above `/data/sds/broker`. `rotation-decomposer` and
`rotation-proposer` keep their named state volumes. `rotation-proposer` also keeps
`cred-rotation-state.json` at `SECURITY_DRIFT_STATE_DIR` on that volume, so no state moves and
nothing changes owner. Each header now says the lane holds no token and reads through the broker,
and that the resource keeps `<LANE>_BWS_ACCESS_TOKEN`, mapped to nothing, until the revoke sitting.

### Requirement 3: launchers

The same changes apply to all three launchers:

- `sds-bws.sh` is sourced before the host test, which is now `sds_in_lane_container`.
- The refusal message names the socket and the token.
- Read failures say "(the lane's socket or token)".
- Each child that must not read runs under `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET`, or after
  `unset` of both.

The children are, per lane:

- **change-proposer:** `change-proposer repositories`, the mint, and the program. The inner
  `if [ -n "${BWS_ACCESS_TOKEN:-}" ]` gate is now `if sds_in_lane_container`. Left as it was, a
  broker pass would have run the program with no credentials.
- **rotation-decomposer:** the program.
- **rotation-proposer:** the mint, git and the program. `sds_anonymous_clone` already stripped both
  variables. The inner gate is now `if sds_in_lane_container`. Left as it was, a broker pass would
  have armed the dead-man switch, skipped the whole pass and exited 0.

I also added `SDS_SECRETS_SOCKET` to the `unset` in `sds_infraops_session`, the person's infraops
command that the rotation proposer's launcher sources from `scripts/sds-cred-registries.sh`.

### Requirement 4: the broker row's reach

`secrets-broker-bws-token`'s `reach_secrets` gains exactly four UUIDs: `acccb346-…`, `7651ccc2-…`,
`936bbbf2-…` and `506a5436-…`. It held 21 and now holds 25, none repeated. I checked the
coverage of every other UUID in the three tables:

- **Already in the row's `reach_secrets`:** `f793576f-…` and `314f276d-…`.
- **Project grant on `SDS Operator` (`b09acc9d-…`):** `221a48d5-…` and `b415d03b-…`. Their
  `.bws-secrets.toml` project is `SDS Operator`.
- **Project grant on `SDS / Lane Apps` (`b3df5419-…`):** `314a966c-…` and `b8f547b9-…`.

No lane reads a secret outside that reach, so no stop point applied.

### Requirement 5: `helper_target`

I removed `helper_target` from `change-proposer-bws-token`, `rotation-decomposer-bws-token` and
`rotation-proposer-bws-token`, and gave each the comment batch 2 used. The rows stay, with
their consumers, `rotate_requested` and reach unchanged. After the merge, the three lanes' targets
in the VPS helper's `/etc/sds-pg-rotate/targets.toml` are removed as a root act on `prod-vps`
(Amendment 32 item 4 step 3). That's HQ's act, not this PR's.

### Requirement 6: identity comments

- **`.bws-secrets.toml`:** I rewrote each comment that names one of these lanes' container readers
  to say the broker's account `lane-secrets-broker` reads it. That covers the SYSTEM bearer, the
  observer bearer, READ and PROPOSE, the `sds-bridge` sender, both lane App keys and the three ping
  URLs. Where a sentence names several lanes, I rewrote it lane by lane, so the batch 4 lanes still
  say "with the lane's own token". The comments name G-106 for the grants Devon gave there.
- **`.cred-consumers.toml`:** I updated the following entries:
  - the change-proposer and rotation-proposer container `runtime-fetch` notes on
    `orchestrator-observer-bearer`;
  - both App keys' notes;
  - the three `coolify-env` notes;
  - the three ping-URL keeper notes;
  - the broker row's grant list, with the G-106 sentence;
  - the lane-App block comment.

### Requirement 7: docs

- **`docs/operations/secrets-broker.md`:** the broker lane list gains the three lanes. "What the
  broker doesn't change" now names the three lanes still on a token: `bump-proposer`,
  `revision-watcher` and `work-carrier`.
- **`docs/operations/deploy.md`:** each of the three lanes' sections now says it reads through the
  broker, in the sentence batch 2 used. Its dead-man reads go through the broker too.
  `change-proposer`'s "mounts no volume" became "no state volume". The rotation proposer's clone
  sentence names the socket's marker.
- **`docs/operations/rotation-executor.md`:**
  - The rotation decomposer's paragraph now says it reads through the broker.
  - The helper-target table drops the three lanes, as
    `test_the_install_page_lists_each_target_s_container_as_derived` requires.
  - The Coolify token adapter's held-deploy answer, line 1110, now reads "400
    (`4.0.0-beta.473`) or 405 (`4.7.6`)".

No other operations page says these lanes read with their own token.

### Tests

The following test changes follow batch 2's:

- **Row tests:** `test_change_proposer_rows.py` and `test_rotation_decomposer_rows.py` now assert
  that the compose doesn't map the token variable. `test_proposer_lane_rows.py` covers
  bump-proposer and rotation-proposer, and bump-proposer stays on its token. So I gave its `Lane`
  a `brokered` field, and the assertion splits on it rather than flipping for both.
- **Broker row:** `test_secrets_broker_row.py`'s `SECRETS` gains the four UUIDs.
- **Compose tests:** each lane's compose test pins the broker lane: `broker_lane`, `add_refusals`
  with the allowlist, `compose_broker_refusals`, the table's exact set, the environment without the
  token, and the volumes.
- **Launcher tests:** each lane's launcher test gains a child log of the socket marker. They also
  gain unset and empty refusals, a refusal when the variable names a regular file, and a full pass
  on the socket with `bws` never run. The rest cover a refused secret (`bws_failed` and
  `not_allowed`) that fails the pass, and a dead socket that fails the pass without falling back to
  `bws`. change-proposer and rotation-decomposer also gain a dry run on the socket.
- **Text pins:** `tests/rotation_decomposer/test_launcher.py` pins the new `unset` line.
  `test_sds_cred_registries.py` reads the shared git stub's extra `socket=` field.

The `test_sds_cred_registries.py` change counts as an outside test that reads a real file of these
lanes. It failed only because I added a field to a stub it imports, not because of a real compose,
so the handoff's last stop point didn't apply. `test_deploy_lanes.py` passed unchanged.

## Tests and checks

### `make check`

`make check` exit 0 at `29471bd2`, with `TEST_DATABASE_URL` pointing at
`orchestrator_test_hq_w_323`, migrated to head:

- **Ruff and pyright:** "All checks passed!" and "0 errors, 0 warnings, 0 informations".
- **pytest:** 14926 items collected: 14925 passed and 1 skipped.

Two earlier runs failed and were fixed in the two follow-up commits: a `ruff format` line, and the
decomposer's text pin on `unset BWS_ACCESS_TOKEN`.

### Mutation checks

Each mutation ran on the committed tree against the lanes' guard and lane test files, and was
restored by copying the saved file back. `git status` was clean after each.

| Mutation | Failing tests |
|---|---|
| `BWS_ACCESS_TOKEN: ${CHANGE_PROPOSER_BWS_ACCESS_TOKEN}` back in `lanes/change-proposer/compose.yaml` | `test_secrets_broker_allowlist.py::test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket`, `::test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind`, `test_change_proposer_rows.py::test_the_token_row_is_one_the_lane_token_adapter_acts_on`, `test_run_change_proposer.py::test_the_compose_meets_the_add_refusals_and_maps_nothing_else`, `test_deploy_lanes.py::test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` (5 failed) |
| `b8f547b9-…` dropped from `[lanes.rotation-proposer]` | `test_secrets_broker_allowlist.py::test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads`, `test_run_rotation_proposer_lane.py::test_the_compose_meets_the_add_refusals_and_maps_nothing_else` (2 failed) |
| `helper_target = "rotation-decomposer"` left on its token row | `test_lane_token_helper_targets.py::test_no_broker_lane_and_not_the_broker_s_own_token_names_one`, `::test_each_target_is_the_service_that_loads_the_row_s_variable`, `::test_the_install_page_lists_each_target_s_container_as_derived` (3 failed) |
| `run-rotation-proposer.sh`'s inner gate back to `[ -n "${BWS_ACCESS_TOKEN:-}" ]` | the 7 broker tests in `test_run_rotation_proposer_lane.py` (a full pass, the dry run, the four refusals and the dead socket) |
| `run-change-proposer.sh`'s mint without `-u SDS_SECRETS_SOCKET` | `test_run_change_proposer.py::test_on_the_socket_without_a_token_a_submitting_pass_reads_every_secret_through_it`, `::test_on_the_socket_a_dry_run_reads_no_propose_bearer_and_pings_nothing` (2 failed) |

Every mutation was caught, and none survived.

### Token-shape grep

I grepped the added lines of `git diff origin/main...HEAD` for a BWS token shape, a GitHub token
prefix, an Anthropic key prefix and a PEM header. It matched 0 lines. Every UUID the diff adds is a
BWS secret id already in `.bws-secrets.toml`, and a secret id isn't a secret. The tests use
placeholder strings (`a-lane-token`, `value-of-<uuid>`).

### Reviews

- **`code-standards risk`:** `high-risk`, for secrets in `.bws-secrets.toml`,
  `lanes/secrets-broker/allowlist.toml`, `scripts/run-change-proposer.sh` and
  `tests/rotation_worker/test_secrets_broker_row.py`.
- **`vendor-review`** (`/tmp/vendor-review-3iatcjl0`): complete. Claude and Codex each approved
  with 0 findings, and nothing was dropped. agy: off by configuration.
- **`/code-review`** (medium, `origin/main...HEAD`): four nits, none of them changed in the code.
  - **The 405 in `rotation-executor.md`** looks unrelated to the broker. The handoff's requirement
    7 asks for it as PR #820's follow-up, so it stays. It's a doc line, and this PR doesn't change
    the adapter's probe code.
  - **`_narrow_secret` and `_broad_secret` in `run-rotation-decomposer.sh`** are identical
    wrappers. They were identical before this change, and `test_launcher.py` keys on
    `_narrow_secret`. Removing them is unrelated cleanup, so I left them.
  - **The inner `if sds_in_lane_container` in `run-change-proposer.sh`** is always true after the
    top guard. That's the shape batch 2 kept for `run-membership-check.sh`. It keeps the diff to a
    one-line condition change.
  - **Rows without `helper_target`** keep the `PATCH` reissue proof. That's the decided shape for
    a broker lane's row (`docs/operations/rotation-executor.md`, "Lane tokens and ping URLs";
    `tests/rotation_worker/test_lane_token_loaded.py`), and the tokens retire at the revoke
    sitting.
- **Security scan** from the worktree root: 0 BLOCK, 2 WARN, 1 INFO. Both WARNs were there before
  this branch, as batches 1 and 2 recorded: `*-migration/` gitignore coverage, and the undeclared
  UUID `aaff45ca-…`, which isn't in the diff.

## Files changed

- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/deploy.md`
- `docs/operations/rotation-executor.md`
- `docs/operations/secrets-broker.md`
- `docs/superpowers/reports/broker-batch-3-pr-report.md`
- `lanes/change-proposer/compose.yaml`
- `lanes/rotation-decomposer/compose.yaml`
- `lanes/rotation-proposer/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `scripts/run-change-proposer.sh`
- `scripts/run-rotation-decomposer.sh`
- `scripts/run-rotation-proposer.sh`
- `scripts/sds-cred-registries.sh`
- `tests/rotation_decomposer/test_launcher.py`
- `tests/rotation_worker/test_change_proposer_rows.py`
- `tests/rotation_worker/test_proposer_lane_rows.py`
- `tests/rotation_worker/test_rotation_decomposer_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/scripts/test_run_change_proposer.py`
- `tests/scripts/test_run_rotation_decomposer.py`
- `tests/scripts/test_run_rotation_proposer_lane.py`
- `tests/scripts/test_sds_cred_registries.py`

I didn't change ADR-0055 or anything under `src/rotation_worker/`.

## Self-review

- **The inner gates were the real risk.** A conversion of only the top refusals would have passed
  every existing test, since those tests run with a token. The rotation proposer would then have
  exited 0 on every broker pass while doing nothing. Mutation 4 shows the broker tests catch that.
- **Ordering for HQ.** The release follows the G-106 grants and the broker directories, which the
  handoff records as done. Within the batch, each lane still takes its own steps 5 to 8: the
  broker's move to the release with its `listening` line for each lane, the `--tag` conversion,
  `lane-run --read` over each table, and the revoke sitting later.
- **The dead-socket path.** With a dead socket, each lane's ping-URL read fails too, so no `/fail`
  is sent and the check goes down after its grace. That's the documented behavior for a key a lane
  can't read (`scripts/CLAUDE.md`). The pass itself exits 1.
- **Rollback.** A `--tag` back to a release before the conversion still finds each lane's
  `<LANE>_BWS_ACCESS_TOKEN`, which the resources keep.

## Concerns

- **change-manager's registry.** The consumer rows for `M2M_TOKEN_PROPOSE` (read by
  `change-proposer` and `rotation-proposer`) and `M2M_TOKEN_READ` (read by `rotation-decomposer`)
  live in change-manager's `.cred-consumers.toml`. They might still describe these containers
  reading with the lane's own token. The handoff scopes this to one orchestrator PR, so I didn't
  touch that repository. This is the same concern batch 2 recorded.
- **Mac-era Keychain notes.** The `bws-sds-operator-token` row's `runtime-fetch` notes for the
  three launchers still say "reads Keychain Claude/BWS_ACCESS_TOKEN_SDS each run". They describe
  the narrow identity's Mac readers, not the container. Batches 1 and 2 left the matching notes
  too.
- **Standards coverage.** The handoff's standards cover every concern this change touches.

## Standards followed

- **Code Brain 3 (logging).** I added no logging path. The launchers keep their existing stderr
  `FATAL:` lines and the dead-man and outcome helpers. Only the wording of existing messages
  changed.
- **Code Brain 4 (error handling).** A failed read still fails the pass on every path where the
  token path failed. Tests pin a failed `bws` read and a `not_allowed` refusal on the socket for
  each lane, and a dead socket fails the pass with exit 1 and no fallback to `bws`. I added no
  swallow.
- **Code Brain 5 (security).** Nothing new prints a value. The new messages name the read path
  ("the lane's socket or token") and the broker's own one-line refusal, which gives the UUID and a
  reason code. The tests assert that no secret value reaches stdout or stderr.
- **Infra Brain 591, 592 and 593 (`bws.no-token-in-tracked-files`, `bws.no-token-in-git-history`,
  `bws.bootstrap-token-not-inline`).** No token or token-shaped literal is in code, composes, tests
  or fixtures; the token-shape grep matched 0 lines. The composes name only variables and secret
  ids.
- **Infra Brain 1032 (`cred.exposure-rotate`).** The registry rows I edited keep valid exposure
  and consumer fields. I removed only `helper_target` and added four `reach_secrets` entries. The
  `bws_lane_token` adapter reads the rows unchanged
  (`test_the_token_row_is_one_the_lane_token_adapter_acts_on`, `test_lane_token_helper_targets.py`,
  `test_secrets_broker_row.py`). I read no credential and surfaced none.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable. The change adds no endpoint or
  FQDN.
- **Construction mode.** This work proceeds under the construction-mode ruling,
  `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`.
