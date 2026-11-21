Outcome: done

# Report: lanes-inc-5-bump-driver-ping-uuid

ADR-0059 increment 5, step 3g-3: the code half of `docs/operations/deploy.md`'s bump-driver cut-over
item 7 (line 1173 at base, "A served release whose compose names the ping-URL secret's id, with
`bump-driver` removed from `test_deploy_lanes.py`'s `placeholders`"). It follows #767 (`d1ea67f8`),
estate landing's item 6, another broker lane.

- Base: `origin/main` at `36a882e9`. The handoff's line references matched at that commit.
- Branch: `hq/lanes-inc-5-bump-driver-ping-uuid`.
- PR: #771, open and ready for review (not a draft), not merged.
- Code head: `66b9e614`, which `make check` and both reviews ran on. The report commit follows it on
  the same branch.

## What I built

The keeper id `430691e2-4a36-4261-a28f-b50900e064b7` is a BWS secret id, not a secret, not a ping
URL, and not a check's ping UUID. It matches `.cred-consumers.toml`'s `bump-driver-ping-url` row
(`bws_uuid`) and `tests/rotation_worker/test_bump_driver_rows.py:33` (`KEEPER`).

1. `lanes/bump-driver/compose.yaml`:
   - Line 39: `SDS_DEADMAN_PING_URL_UUID: 430691e2-4a36-4261-a28f-b50900e064b7`.
   - Lines 23-29: the header paragraph is rewritten in `d1ea67f8`'s shape. Unlike estate's, it says
     the outcome alarm reads the URL through the broker socket on an acting pass only, never on
     `--dry-run` (`scripts/run-bump-driver.sh:120-126` passes `_acting` to `sds_outcome_begin`,
     which resolves only when it is 1, `scripts/sds-deadman.sh:224-226`).
2. `lanes/secrets-broker/allowlist.toml` line 47: the keeper id, with `# bump-driver-ping-url`, at
   the end of `[lanes.bump-driver]`. `tests/scripts/test_secrets_broker_allowlist.py`'s
   `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` already holds each entry equal
   to the launcher's UUID literals plus the compose's `SDS_DEADMAN_PING_URL_UUID`, so it needed no
   change: with the compose naming the id, it requires this line (mutation checks follow).
3. `.bws-secrets.toml` lines 564-576: one `[[secret]]` entry after `estate-landing-ping-url`, in its
   comment style: `name = "bump-driver-ping-url"`, `project = "Rotation / Keepers"`,
   `lanes = ["bump-driver"]`. The comment says the lane reads it through the broker on an acting
   pass and has no token keeper.
4. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - `bump-driver` leaves `placeholders`, which is now `change-window` and `follow-up-mint`.
   - Line 1403: `bump-driver` joins the `<= set(named)` set.
   - The docstring is unchanged: it describes `placeholders` in general, which stays true for
     `change-window` and `follow-up-mint`.
5. `tests/rotation_worker/test_bump_driver_rows.py`: no existing assertion there said the compose
   value was empty. I added
   `test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry`
   (lines 109-118), with `tomllib`, `load_allowlist` and `ALLOWLIST` (lines 11, 27, 37), the same
   test as estate's.
6. `tests/scripts/test_run_bump_driver.py`:
   - Line 261: unchanged. It is the harness's default environment, not a copy of the compose, and
     deliberately drives the outcome alarm's empty-id fallthrough to the name lookup, which
     `test_each_pass_reads_its_six_secrets_with_the_lanes_one_token` (lines 305-318) asserts.
     `d1ea67f8` kept estate's harness default the same way. No empty-id case was removed.
   - Line 867 (now 866-867): stood in for the compose, so it is updated. The compose assertion in
     `test_the_compose_meets_the_add_refusals_and_maps_nothing_else` expects the literal keeper id,
     with `d1ea67f8`'s comment.
   - Line 696 (`_brokered` docstring): "The compose's ping-URL id is empty" becomes "The harness's
     ping-URL id is empty".
   - Line 742: "Once the compose names the ping-URL secret (a later change)" becomes "With a ping-URL
     id named, as the compose names one".
   - Line 788: "which the compose names once its secret exists" becomes "which the compose names".
   - Lines 922-932: `test_the_repositorys_manifest_lists_the_lanes_secrets` lists the keeper beside
     the seven secrets and exempts it from the launcher-names-it check, because its id reaches the
     launcher from the compose through `lane-run`. The handoff didn't name this test, but the
     manifest change fails it otherwise; this is `d1ea67f8`'s form.
7. `docs/operations/deploy.md` lines 1086-1092: "the following seven … the broker's allowlist lists
   exactly these. Its ping-URL secret joins them, in the manifest and the allowlist, when it exists;
   until then … `placeholders`" becomes "its ping-URL secret and the following seven … the broker's
   allowlist lists exactly these eight", keeping "each fatal if unreadable, the first six as on the
   Mac" for the seven, plus a sentence naming `bump-driver-ping-url` and the outcome alarm reading
   it through the socket on an acting pass only. Item 7 (line 1173) is unchanged, as `d1ea67f8` left
   estate's item. Item 5 already says "the lane's seven secrets and its ping-URL secret", and item 8
   already says "all eight secrets".

No ADR, launcher, `lane-run`, `sds-deadman.sh`, `scripts/secrets_broker.py`, crontab,
`.cred-consumers.toml`, `test_secrets_broker_row.py`, or other lane's compose or allowlist entry
changed. The G-84 "Devon's act at the cut-over gate" text is unchanged.

### Item 8 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`: `scripts/lane_run.py:70`,
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, copied into the launcher's environment at
  `:109`; `:112-113` sets `SDS_SECRETS_SOCKET` when the socket is mounted.
  `test_every_compose_variable_lane_run_must_pass_is_in_its_list` passes.
- Under the broker, the outcome alarm reads the URL by that id through the socket:
  `sds_outcome_begin` (`scripts/sds-deadman.sh:222-226`) calls `sds_deadman_resolve` on an acting
  pass, which calls `sds_deadman_resolve_by_uuid` when the id is set (`:146-150`). That accepts a
  socket in place of a token (`sds_in_lane_container`, `:106`) and calls
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (`:118`), whose one-argument form reads through the
  socket when `SDS_SECRETS_SOCKET` names one (`scripts/sds-bws.sh:77`).
  `test_with_a_ping_url_named_the_socket_serves_every_read` proves it in the harness.

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_bump_driver.py`, `test_bump_driver_rows.py`, and
  `test_secrets_broker_allowlist.py` had 215 passed.
- Mutation checks, each from a backup copy, restored with `cp` and confirmed with `cmp`. Control
  run: 13 passed.
  - `lanes = ["bump-driver"]` emptied in the keeper's manifest entry only (line 576), run with
    `test_the_repositorys_manifest_lists_the_lanes_secrets` added: the new rows test,
    `test_every_lanes_ping_url_secret_is_listed_for_that_lane`, and the manifest test fail
    (3 failed, 11 passed). My first run of this mutation also emptied two other `bump-driver`
    entries (lines 266 and 272), so I reran it on line 576 alone; this is that run.
  - The allowlist line removed: the new rows test and
    `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` fail (2 failed).
  - The compose value set back to `""`: `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
    and the allowlist test fail (2 failed).
- `make check` on `66b9e614`, database `orchestrator_test_hq_w_256`, exited 0:
  - Ruff: "All checks passed!", 1039 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: 14865 passed, 1 skipped; the log reports `10 workers [14866 items]`.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO, the same three #767
  reported (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`,
  `bws.least-privilege-scope`). The diff causes none of them.
- `/code-review` (low) on `origin/main...HEAD`: the forked review returned no findings (its result
  was empty). The vendor review covered the same diff independently.
- `code-standards risk`: `high-risk` (secrets: `.bws-secrets.toml`,
  `lanes/secrets-broker/allowlist.toml`), so I also ran `vendor-review`: Claude and Codex both
  approve, 0 findings kept, 0 dropped. agy: off by configuration.

## Files changed

- `lanes/bump-driver/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_bump_driver_rows.py`
- `tests/scripts/test_run_bump_driver.py`
- `docs/superpowers/reports/lanes-inc-5-bump-driver-ping-uuid-report.md` (this report)

## Self-review

- The diff follows `d1ea67f8`; the one difference in text is the outcome alarm's acting-only read.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- The broker serves the allowlist baked into its own release. Until the broker moves to a release
  with this entry (item 8), a lane container on this release would have its ping-URL read refused
  (`not_allowed`) and ping nothing on an acting pass, while the pass still runs. Item 8 already
  moves the broker before the lane's `--add`, so this needs no change; HQ keeps that order.
- HQ merges #771 after the executor's write (item 6); its release is the served-release half of
  item 7.
- Out of scope, so not edited: `scripts/run-bump-driver.sh:46` says the broker's allowlist lists
  "exactly the seven this launcher reads". The entry now lists eight: those seven plus the ping-URL
  keeper, whose id reaches the launcher from the compose, as the allowlist header (lines 14-16)
  states. The handoff bars launcher changes; HQ may fold that one word into a later launcher change.
  A wider sweep (`docs/operations/`, ADR-0059, the allowlist header, the launcher, the two test
  files) found no other stale bump-driver text. ADR-0059 line 4841 ("its seven secrets", citing
  #762) is a dated record of that commit and stays.
- #771 is `MERGEABLE`; `mergeStateStatus` is `BLOCKED` only on required checks and review.
- The test database `orchestrator_test_hq_w_256` still exists for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the added test catches nothing; it
  asserts directly.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a check's
  ping UUID, or a token. The only id added is the keeper's BWS secret id, labeled "not a secret,
  not the ping URL" in the compose and the test.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value; the compose maps no token at all. The scan has 0 BLOCK findings, and the
  diff adds no test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port,
  no domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits one BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL.
- **Infra Brain lesson 450** (rotation landmines): ids only; no secret value appears in a test, the
  report, or a log. The harness's stand-in ping URL is `https://ping.example/x`, unchanged.
