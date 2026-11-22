Outcome: done

# Report: lanes-inc-8-change-window-ping-uuid

ADR-0059 increment 8 (Amendments 14 and 15), step 3j. This change is the code half of
`docs/operations/deploy.md`'s change-window cut-over item 5 (line 1349 after the change, 1347 at
base): "A served release whose compose names the ping-URL secret's id, with `change-window`
removed from `test_deploy_lanes.py`'s `placeholders`". It follows #774 and #771 (`8c0f97b7`).

- Base: `origin/main` at `0332a6ed`. The handoff's line references matched at that commit.
- Branch: `hq/lanes-inc-8-change-window-ping-uuid`.
- PR: #786, open and ready for review (not a draft), not merged.
- Code head: `3af69684`, which `make check` and both reviews ran on. A follow-up commit fixes two
  more stale texts (a comment in the compose and a sentence in `credentials.md`; neither is code),
  after which I reran the targeted tests. The report commits follow on the same branch.

## What I built

The keeper id `1d6a0ab4-e164-4fe2-8ccb-b50a00e255c3` is a BWS secret id. It isn't a secret, a ping
URL, or a check's ping UUID. It matches `.cred-consumers.toml`'s `change-window-ping-url` row
(`bws_uuid`) and `tests/rotation_worker/test_change_window_rows.py` (`PING_KEEPER`).

1. `lanes/change-window/compose.yaml`:
   - Line 32: `SDS_DEADMAN_PING_URL_UUID: 1d6a0ab4-e164-4fe2-8ccb-b50a00e255c3`.
   - Lines 15-16 (follow-up commit): "The ids live in the launcher, not here" becomes "The four
     work secrets' ids live in the launcher, not here", since the compose now names the ping-URL
     secret's id.
   - Lines 17-23: I rewrote the header paragraph in #774's shape, with the dry-run difference: the
     outcome alarm reads the URL through the socket on `--submit` passes only, because the bare
     pass is a dry run that pings nothing. That follows `scripts/run-change-window.sh` lines 14-20
     and line 101, `sds_outcome_begin "$CHECK_NAME" change-window "$SUBMITTING"`.
2. `lanes/secrets-broker/allowlist.toml` line 62: the keeper id, with `# change-window-ping-url`,
   at the end of `[lanes.change-window]`. `tests/scripts/test_secrets_broker_allowlist.py` needed no
   change: `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` already holds each
   entry equal to the launcher's ids plus the compose's id, so it requires this line once the
   compose names the id. The mutation runs confirm this.
3. `.bws-secrets.toml` lines 658-671: one `[[secret]]` entry after the change-window block, in the
   follow-up-mint ping entry's comment style: `name = "change-window-ping-url"`,
   `project = "Rotation / Keepers"`, and `lanes = ["change-window"]`. The comment says the lane
   reads it through the broker on `--submit` passes only and has no token keeper.
4. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - Line 1388: `placeholders: set[str] = set()` (the annotation keeps pyright from inferring
     `set[Unknown]`).
   - Line 1403: `change-window` joins the `<= set(named)` set, in alphabetical order.
   - The docstring is unchanged. It describes the placeholder mechanism in general, which stays
     true. The empty set doesn't make the test dead: `assert blank == placeholders` now requires
     that no compose has an empty id. The compose mutation run proves it.
5. `tests/rotation_worker/test_change_window_rows.py`: no existing assertion there said the
   compose value was empty or counted the allowlist. I added
   `test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry`
   (lines 135-146), with `tomllib`, `load_allowlist`, and `ALLOWLIST` (line 40), the same test as
   #774's. Its docstring says the broker would refuse the outcome alarm's read on a `--submit`
   pass.
6. `tests/scripts/test_run_change_window.py`:
   - Lines 39-41: new constant `PING_URL_KEEPER`, the keeper id, distinct from the harness's
     stand-in `PING_URL_UUID`.
   - `test_the_compose_meets_the_add_refusals_and_maps_nothing_else` stood in for the compose, so I
     updated it: the comment at lines 667-668 names the compose's id beside the launcher's four;
     line 673 expects the allowlist entry to be `{*DEFAULT_IDS.values(), PING_URL_KEEPER}`; and
     lines 680-681 expect `PING_URL_KEEPER` in the environment, with #774's comment.
   - No case in this harness sets `SDS_DEADMAN_PING_URL_UUID` empty, calls a value "the compose's",
     or counts the lane's manifest secrets, so nothing else changed. (#774's harness had a
     manifest-count test; this one has none, and the new rows test covers the manifest.)
7. Docs and comments this change made stale:
   - `docs/operations/deploy.md` lines 1306-1315 (1306-1313 at base): "reads its four secrets",
     "the broker's allowlist lists exactly the four ids the launcher reads", and
     "`test_deploy_lanes.py`'s `placeholders` lists the lane for its empty ping-URL id" become
     text saying the lane reads its four secrets and its ping-URL secret, the allowlist lists the
     four ids and the ping-URL secret's, and the compose names the keeper's id, which the outcome
     alarm reads through the socket on `--submit` passes only.
   - `scripts/run-change-window.sh` lines 33-34 (comment only): "while the compose leaves it empty,
     nothing is pinged" becomes "whose BWS id the compose names in `SDS_DEADMAN_PING_URL_UUID`".
   - `scripts/run-change-window.sh` lines 43-45 (comment only): the allowlist is no longer "exactly
     these four"; it now reads "these four and the ping-URL secret, whose id reaches this launcher
     from the compose through `lane-run`".
   - `docs/operations/credentials.md` lines 446-447 (follow-up commit): "The broker's allowlist
     serves the lane those four and nothing else; its ping-URL secret joins them when the compose
     names it" becomes "serves the lane those four and its ping-URL secret, whose id the compose
     names, and nothing else".
   - The sweep: I grepped `placeholders` across `docs/operations`, `docs/decisions`, `tests`,
     `scripts`, `lanes`, and every tracked `CLAUDE.md`. The deploy.md hits are each lane's cut-over
     checklist item, which stay. I read deploy.md's change-window section from its start (line
     1263), `credentials.md`, `secrets-broker.md`, and `.cred-consumers.toml`'s change-window rows.
   - Left unchanged: deploy.md item 2's "(`1d6a0ab4-…`, no value yet)" (line 1329; true until item
     4), item 6's "all five secrets" (already correct), the launcher's line 20 "It reads all four
     secrets below" (the four work secrets, still true), and ADR-0059 line 4257 (a plan statement,
     not made stale).

No ADR, launcher code, `lane-run`, `sds-deadman.sh`, `scripts/secrets_broker.py`, crontab,
`.cred-consumers.toml`, `test_secrets_broker_row.py`, or other lane's compose or allowlist entry
changed.

### Item 8 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`: `scripts/lane_run.py:70` defines
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, and lines 109-111 copy each name the
  container's environment sets into the launcher's environment.
- Under the broker, the outcome alarm reads the URL by that id through the socket.
  `sds_outcome_begin` (`scripts/sds-deadman.sh:222-226`) calls `sds_deadman_resolve` only when
  `acting` is 1, which the change-window launcher passes as `$SUBMITTING`. `sds_deadman_resolve`
  calls `sds_deadman_resolve_by_uuid` when the id is set (lines 146-149). That function accepts a
  socket in place of a token (`sds_in_lane_container`, line 106) and calls
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (line 118), whose one-argument form reads through
  the socket when `SDS_SECRETS_SOCKET` names one (`scripts/sds-bws.sh:77`). The existing harness
  test `test_on_the_socket_a_submit_pass_reads_every_secret_through_it` asks the ping-URL id first
  through the socket, and `test_on_the_socket_a_dry_run_asks_for_the_execute_bearer_alone` shows
  the dry run doesn't ask for it.

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_change_window.py`, `test_change_window_rows.py`, and
  `test_secrets_broker_allowlist.py`: 198 passed, on `3af69684` and again after the follow-up
  commit.
- Mutation checks. Each ran from a backup copy, was restored with `cp`, and was confirmed with
  `cmp`, clearing `__pycache__` around each run. The set was the rows test file, the ping-URL
  guard, the harness's compose test, and the allowlist test file. Control: 13 passed.
  - The allowlist line removed: the new rows test, the compose test, and
    `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` failed (3 failed, 10
    passed).
  - The keeper's manifest entry with `lanes = []`: the new rows test and
    `test_every_lanes_ping_url_secret_is_listed_for_that_lane` failed (2 failed, 11 passed).
  - The compose value set back to `""`: the ping-URL guard (the empty `placeholders`), the compose
    test, and the allowlist test failed (3 failed, 10 passed).
- `make check` on `3af69684`, database `orchestrator_test_hq_w_268`, exited 0:
  - Ruff: "All checks passed!", 1041 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: `10 workers [14892 items]`. Result: 14891 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, 1 INFO. I read each finding:
  - `bws.secret-files-gitignored` (WARN): `.gitignore`, "glob not covered: `*-migration/`".
  - `bws.manifest-matches-usage` (WARN): "undeclared UUID in code: `aaff45ca-…`", not the keeper.
  - `bws.least-privilege-scope` (INFO): "requires agent judgment".
  None names `1d6a0ab4-…`, the change-window entry, or a file this diff changes in a way the
  finding concerns. I didn't run a differential scan against `origin/main`.
- `code-standards risk`: `high-risk` (secrets: `.bws-secrets.toml` and
  `lanes/secrets-broker/allowlist.toml`), so I ran `vendor-review` on `origin/main...HEAD`. Claude
  and Codex both approve, with 0 findings kept and 0 dropped. agy: off by configuration.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `lanes/change-window/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `scripts/run-change-window.sh` (comments only)
- `docs/operations/credentials.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_change_window_rows.py`
- `tests/scripts/test_run_change_window.py`

## Self-review

- The diff follows #774. The one difference in its text is that the read happens on `--submit`
  passes only.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- The broker serves the allowlist built into its own release. Until the broker moves to a release
  with this entry (item 6), a `--submit` pass on this release would have its ping-URL read refused
  (`not_allowed`) and would ping nothing, though the pass still runs. Item 6 already moves the
  broker before the lane's `--add`, so HQ must keep that order.
- HQ merges #786 after the executor's write (item 4). Its release is the served-release half of
  item 5.
- I changed two launcher comment blocks, not one: lines 33-34 (the empty-compose sentence the
  handoff names) and lines 43-45 (the allowlist count, which item 7's "any count of the lane's
  secrets where the text counts the allowlist" covers). The launcher's code is unchanged.
- Out of scope, so I didn't edit them: `.cred-consumers.toml`'s `change-window-ping-url` block says
  the broker reads the keeper "once the compose names its id" (the block comment, ~line 1880, and
  the `bws-secret` consumer's `note`, ~line 1898). After this PR the compose names it, so both
  read as future tense for a done step. The handoff bars other `.cred-consumers.toml` changes; HQ
  can fold them into item 6's registry change.
- I'm leaving the test database `orchestrator_test_hq_w_268` in place for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the added test catches nothing. It
  asserts directly.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a
  check's ping UUID, or a token. The only id the diff adds is the keeper's BWS secret id. The
  compose and the harness constant label it "not a secret, not the ping URL", and the harness keeps
  it distinct from its stand-in id.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose maps no token. The scan has 0 BLOCK findings, and the diff
  adds no test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits one BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL. The harness's stand-in URL is
  `https://hc.example/ping/change-window`, unchanged.
- **Infra Brain lesson 450** (rotation landmines): ids only. No secret value appears in a test, the
  report, or a log.
