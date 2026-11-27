Outcome: done

# Broker batch 4's pull request

Worker W-326, handoff `broker-batch-4-pr` (ADR-0055 Amendment 32 item 4 step 3, Broker Phase C's
fourth and last batch). Branch `hq/broker-batch-4`, based on orchestrator `origin/main` at
`18ff4afd`. The PR is open and not merged. The release, the broker's move, each lane's conversion
and the read proof are HQ's later acts; this PR takes no production step.

## What I built

Three lanes, `work-carrier`, `bump-proposer` and `revision-watcher`, read every secret through the
lane secrets broker instead of their own BWS tokens. Batch 3's PR #821 (`e4412d4f`) is the shape.
After this batch, the broker's own compose is the only `lanes/*/compose.yaml` that maps
`BWS_ACCESS_TOKEN`, so no Amendment 32 lane is left on its own token.

### By requirement

1. **Allowlist tables.** `lanes/secrets-broker/allowlist.toml` gains `[lanes.work-carrier]`,
   `[lanes.bump-proposer]` and `[lanes.revision-watcher]`, each with the following UUIDs:

   | Lane | UUIDs (prefix, registry name) |
   |---|---|
   | `work-carrier` | `314f276d` change-manager/M2M_TOKEN_READ, `221a48d5` orchestrator-system-bearer, `f793576f` orchestrator-observer, `2f708d08` factory-events-read-password, `314a966c` lanes-read-app-private-key, `a9843d17` work-carrier-observer-bearer (`--register` only), `b415d03b` ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE (`--register` only), `acccb346` change-manager/M2M_TOKEN_PROPOSE (`--register` only), `55fbae20` work-carrier-ping-url |
   | `bump-proposer` | `314f276d` change-manager/M2M_TOKEN_READ, `f793576f` orchestrator-observer, `b415d03b` ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE, `314a966c` lanes-read-app-private-key, `acccb346` change-manager/M2M_TOKEN_PROPOSE (`--submit` only), `2ce0dbc7` factory-events-append-password (`--submit` only), `b8f547b9` lanes-push-app-private-key (`--submit` only), `786f75e2` bump-proposer-ping-url |
   | `revision-watcher` | `f793576f` orchestrator-observer, `0bf066e9` prod-coolify-read-token, `726a18ba` APP_BRAIN_READ_KEY, `b415d03b` ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE (acting passes only), `314a966c` lanes-read-app-private-key, `9b2722ea` revision-watcher-ping-url |

   HQ's grep held. I checked it against each launcher's reads, conditional ones included, and
   against each compose's `SDS_DEADMAN_PING_URL_UUID`. I also checked the children: `work-watcher`,
   `work-carrier`, `bump-proposer`, `revision-watcher`, `orchestrator.factory_chain_read`,
   `scripts.sds_scope` and the helpers. None reads BWS or the Keychain. `221a48d5` uses the label
   the existing tables give it, `orchestrator-system-bearer`; its manifest name is
   `ORCHESTRATOR_M2M_CREDENTIALS`.
2. **Composes.** Each compose has no `BWS_ACCESS_TOKEN` mapping and gains
   `/data/sds/broker/<lane>:/run/sds-broker:ro`, with nothing else at, under or above
   `/data/sds/broker`. Each keeps its state volume. The work carrier and the bump proposer keep the
   external `coolify` network their chain database needs. Each header comment now says the lane
   holds no token, reads through the broker, and keeps its `<LANE>_BWS_ACCESS_TOKEN` variable,
   mapped to nothing, until the revoke.
3. **Launchers.**
   - Each sources `sds-bws.sh` before its host check, which is now `sds_in_lane_container`.
   - `run-bump-proposer.sh` had a second gate, the `if [ -n "${BWS_ACCESS_TOKEN:-}" ]` around its
     whole credential block. It's converted too. Without that, a brokered pass would skip every
     read and hand the program nothing.
   - Every `env -u BWS_ACCESS_TOKEN` child gains `-u SDS_SECRETS_SOCKET`:
     - `work-watcher repositories` and the mint (work carrier);
     - `bump-proposer repositories` and both mints (bump proposer);
     - `sds_scope apps` and the mint (revision watcher).
   - Each final `unset` covers both variables, before git and the programs run.
   - The failure messages say "(the lane's socket or token)". Every failed read stays fatal.
4. **`.cred-consumers.toml`, the broker row.** `secrets-broker-bws-token`'s `reach_secrets`
   gains exactly the six G-109 grants:
   - the ping URLs `55fbae20-…`, `786f75e2-…` and `9b2722ea-…`;
   - `2ce0dbc7-…` (`factory-events-append-password`);
   - `0bf066e9-…` (`prod-coolify-read-token`);
   - `726a18ba-…` (`APP_BRAIN_READ_KEY`).

   Every other UUID the lanes read was already covered:
   - in `reach_secrets`: `314f276d`, `f793576f`, `acccb346` and `2f708d08`;
   - through `SDS Operator`: `221a48d5`, `a9843d17` and `b415d03b`;
   - through `SDS / Lane Apps`: `314a966c` and `b8f547b9`.

   The row's comment block names the new grants by project, with a G-109 sentence.
   `rotated_by_sds`, `helper_target` and `created` are unchanged.
5. **Lane token rows.** `helper_target` is removed from `revision-watcher-bws-token`,
   `work-carrier-bws-token` and `bump-proposer-bws-token`. Each row stays, with batch 3's comment
   that the lane reads through the broker and the token stays until the revoke (item 5). Each
   `coolify-env` note says the compose maps the variable to nothing. `bws-bump-proposer-token`
   and every Mac-side row and file are untouched.
6. **Reader comments.** In both registries, every comment that said one of these lanes reads with
   its own token now names the broker's account `lane-secrets-broker`. That covers:
   - the ping-URL keepers;
   - the observer, SYSTEM and work-carrier observer bearers;
   - the change-manager bearers and the `sds-bridge` sender;
   - both App keys and the platform token;
   - App Brain's key;
   - both chain passwords, citing D-81 (4) A for the append password and D-81 (5) A for the read
     holder.
7. **Docs.**
   - `docs/operations/secrets-broker.md`: the broker lane list adds the three lanes. "What the
     broker doesn't change" now says no Amendment 32 lane reads with its own token, and that the
     `bump-proposer` account keeps the Mac's Keychain token (D-81 (6)).
   - `docs/operations/deploy.md`: the three lanes' sections say "read through the broker", with
     batch 3's sentence on the compose and the allowlist. The work carrier's adds D-81 (5) A, and
     the bump proposer's adds D-81 (4) A.
   - `docs/operations/credentials.md`: the chain-password holders table names the broker's
     account for both lanes.
   - `docs/operations/rotation-executor.md`: the helper-target table drops the three lanes' rows,
     and `secrets-broker` is the one row left.
     `test_the_install_page_lists_each_target_s_container_as_derived` requires the table to equal
     the rows that name a target.

## Tests and checks

### `make check`

`make check` exited 0 at `07465492` (the change commit), with `TEST_DATABASE_URL` set to
`orchestrator_test_hq_w_326`, migrated to head:

- **Ruff and pyright:** "All checks passed!" and "0 errors, 0 warnings, 0 informations".
- **pytest:** 14961 items collected: 14960 passed and 1 skipped.

Before the test edits, the lane suites had 18 failures. All were in this batch's own lane and row
tests, which pinned the token mapping and the old messages. `test_deploy_lanes.py` and
`test_lane_image.py` passed unchanged, so the handoff's last stop point didn't apply.

### The pull request and CI

The PR is https://github.com/AlobarQuest/orchestrator/pull/823, not merged. CI ran on head
`81b6bdc7` (the change commit `07465492` plus the report commit), and every check passed:

| Check | Result |
|---|---|
| `Quality` | Passed in 17m23s. 14961 items collected: 14947 passed and 14 skipped. |
| `Rollout transcription currency` | Passed. |
| `Runner consumer compatibility` | Passed. |

The commit that adds this section changes only this report.

### New and changed tests

Each launcher test mirrors #821's tests:

- a brokered pass reads every secret through a stand-in broker and never runs `bws`;
- the socket marker reaches no child (each stub logs `SDS_SECRETS_SOCKET`, and so does git);
- a dry or bare pass reads no acting-only secret;
- a `bws_failed` or `not_allowed` refusal fails the pass before the program;
- a dead socket fails the pass with `no_reply` and never falls back to `bws`;
- a path that isn't a socket refuses;
- an unset and an empty token both refuse;
- the compose test asserts `broker_lane`, `add_refusals` with the allowlist,
  `compose_broker_refusals` and the allowlist table.

The bump proposer's brokered test also proves the credential block ran: the program gets both
bearers, the sender, the append password and the push header.

Other test changes:

- The revision watcher's stub `python` now also passes `-I` through to the real interpreter. The
  socket client runs `python -I -c`, which the stub would otherwise have answered as the scope
  call.
- The row tests now assert the lane variable is absent from each compose.
  `test_proposer_lane_rows.py` drops its `brokered` flag, since both of its lanes are now broker
  lanes.
- `test_secrets_broker_row.py` lists the six grants.

Results:

| Test file | Passed |
|---|---|
| `test_run_work_carrier.py` | 67 |
| `test_run_bump_proposer.py` | 71 |
| `test_run_revision_watcher.py` | 32 |

### Mutation checks

Each mutation ran on the committed tree against the guard and lane test files, with `__pycache__`
cleared first. I restored each by copying the saved file back, and `git status` was clean after
each.

| Mutation | Failing tests |
|---|---|
| `BWS_ACCESS_TOKEN: ${BUMP_PROPOSER_BWS_ACCESS_TOKEN}` back in `lanes/bump-proposer/compose.yaml` | `test_secrets_broker_allowlist.py::test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket`, `::test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind`, `test_deploy_lanes.py::test_the_allowlists_tables_are_exactly_the_broker_lanes_composes`, `test_proposer_lane_rows.py::test_the_token_row_is_one_the_lane_token_adapter_acts_on[bump-proposer]`, `test_run_bump_proposer.py::test_the_compose_meets_the_add_refusals_and_maps_nothing_else` (5 failed) |
| `a9843d17-…` (work-carrier-observer-bearer, a `--register`-only read) dropped from `[lanes.work-carrier]` | `test_secrets_broker_allowlist.py::test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads`, `test_run_work_carrier.py::test_the_compose_meets_the_add_refusals_and_maps_nothing_else` (2 failed) |
| `helper_target = "revision-watcher"` left on `revision-watcher-bws-token` | `test_lane_token_helper_targets.py::test_no_broker_lane_names_one_and_the_broker_s_own_token_names_the_broker`, `::test_each_target_is_the_service_that_loads_the_row_s_variable`, `::test_the_install_page_lists_each_target_s_container_as_derived` (3 failed) |

My first run of the third mutation didn't apply: its anchor, the row's `rotate_requested` date,
appears twice in the file, so the script's assertion stopped it, and the guards passed against an
unmutated file. I reran it anchored on the row's `bws_uuid`, and the table shows that run.

### Token-shape grep of the diff

I ran two greps over the added lines of `git diff origin/main...HEAD`. The BWS access-token shape
(`0.` then a UUID, then a dot) matched 0 lines. GitHub installation tokens, PEM headers and
Anthropic key prefixes also matched 0 lines. The added UUIDs are secret ids, not values.

### Reviews

- **`code-standards risk`:** `high-risk`. The secrets rule matched `.bws-secrets.toml`, the
  allowlist, both chain launchers and `test_secrets_broker_row.py`.
- **`vendor-review`** (`/tmp/vendor-review-l721g5tn`): complete. Claude and Codex each approved
  with 0 findings, and nothing was dropped. agy: off by configuration.
- **`/code-review`** (medium, `origin/main...HEAD`): three nits, none changed in the code.
  - **The `REVISION_WATCHER_ALERT_BWS_UUID` override** would be refused by the broker. `lane-run`
    doesn't pass it into the container, so it can't reach a lane pass. Batch 3 kept the same
    overrides (`CHANGE_PROPOSER_OBSERVER_BWS_UUID`), and the bump proposer has its own.
  - **Rows without `helper_target`** keep the `PATCH` reissue proof. That's the decided shape for
    a broker lane's row (`tests/rotation_worker/test_lane_token_loaded.py`). HQ holds these lanes'
    token packages from the batch's start until the revoke (Amendment 32 item 5.5).
  - **The broker's reason code goes to stderr**, not into the FATAL line. That's `sds_bws_value`'s
    shape for every broker lane, and the brokered tests assert the reason is on stderr.

## Files changed

- `.bws-secrets.toml`
- `.cred-consumers.toml`
- `docs/operations/credentials.md`
- `docs/operations/deploy.md`
- `docs/operations/rotation-executor.md`
- `docs/operations/secrets-broker.md`
- `docs/superpowers/reports/broker-batch-4-pr-report.md`
- `lanes/bump-proposer/compose.yaml`
- `lanes/revision-watcher/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `lanes/work-carrier/compose.yaml`
- `scripts/run-bump-proposer.sh`
- `scripts/run-revision-watcher.sh`
- `scripts/run-work-carrier.sh`
- `tests/rotation_worker/test_proposer_lane_rows.py`
- `tests/rotation_worker/test_revision_watcher_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/rotation_worker/test_work_carrier_rows.py`
- `tests/scripts/test_run_bump_proposer.py`
- `tests/scripts/test_run_revision_watcher.py`
- `tests/scripts/test_run_work_carrier.py`

`docs/decisions/0055-…` and `src/rotation_worker/` are untouched.

## Self-review

- **The bump proposer's inner gate was the real risk.** It's the same trap batch 3 found in the
  rotation proposer. A conversion of only the top refusal would have passed every existing test,
  since those run with a token. A brokered pass would then have skipped the credential block and
  run the program with no bearer, no password and no clone. The brokered `--submit` test asserts
  each value the block hands over.
- **The work carrier's chain read.** The read password now comes through the broker
  (D-81 (5) A). The broker row already held `2f708d08` for the bump driver. It still reaches only
  `work-carrier` and `factory_chain_read`, as an assignment prefix, after the `unset`.
- **The revision watcher arms the dead-man switch.** Its ping-URL read goes through the socket
  too, by id. A dead socket means no ping, and the check goes down after its grace, as for any
  broker lane (`scripts/CLAUDE.md`).
- **The VPS helper's three lane targets.** `/etc/sds-pg-rotate/targets.toml` on `prod-vps` still
  holds `revision-watcher`, `work-carrier` and `bump-proposer` targets. Once these lanes run on
  the broker, those targets point at containers that load no `BWS_ACCESS_TOKEN`. No row names
  them, so the executor never asks. Removing them is a root act on the VPS, outside this PR; I
  record it for HQ and change nothing there.
- **Rollback.** A `--tag` back to a release before this change would run a launcher that refuses
  without `BWS_ACCESS_TOKEN` against a compose that maps none. A rollback of a converted lane
  therefore also needs the old compose, as for batches 1 to 3.

## Concerns

- **The VPS helper's targets.** The three lane targets left in the helper's file are cleanup for
  the revoke sitting or a later root session, not for this PR.
- **Standards coverage.** The handoff's Standards section covers this change. The only extra
  concern, the shell's handling of the socket marker, follows #821's pattern.

## Standards followed

Construction-mode ruling: `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`.

- **Code Brain 3 (logging).** The launchers log only through their existing paths:
  `sds_outcome_fail` and the outcome report for the two outcome-alarm launchers, and stderr plus
  the dead-man switch for the revision watcher. I added no logging path.
- **Code Brain 4 (error handling).** Every read is still checked with `[ -n … ] ||
  sds_outcome_fail` or an explicit `exit 1`, so a failed or refused read fails the pass. The
  brokered refusal and dead-socket tests prove this for each lane. Nothing new swallows an error.
- **Code Brain 5 (security).** Nothing new prints a value. The new messages name a credential and
  "(the lane's socket or token)", never its value. The brokered tests assert that no served value
  appears in stdout or stderr.
- **Infra Brain 591 `bws.no-token-in-tracked-files`, 592 `bws.no-token-in-git-history`, 593
  `bws.bootstrap-token-not-inline`.** The diff adds no token or token-shaped literal; the
  token-shape grep found 0 matches. The composes name variables only, and now map no token at
  all. The tests use placeholder values (`a-lane-token`, `value-of-<uuid>`) and a placeholder
  UUID for the ping URL.
- **Infra Brain 1032 `cred.exposure-rotate`.** The registry rows I edited keep valid exposure and
  consumer fields: `check_row` passes for each token row, and the registry tests pass. The broker
  row's `rotated_by_sds`, `helper_target` and `created` are unchanged. No credential was read or
  surfaced: I ran no `bws` and read no Coolify variable.
- **Infra Brain 212 (HTTPS for public FQDNs).** Not applicable: the change adds and changes no
  endpoint.
