Outcome: done

# Report: lanes-inc-9-follow-up-mint-ping-uuid

ADR-0059 increment 9 (Amendment 10), step 3j-inc-9. This change is the code half of
`docs/operations/deploy.md`'s follow-up-minting cut-over item 7 (line 1249 after the change, 1247
at base): "A served release whose compose names the ping-URL secret's id, with `follow-up-mint`
removed from `test_deploy_lanes.py`'s `placeholders`". It follows #771 (`8c0f97b7`) and #767
(`d1ea67f8`).

- Base: `origin/main` at `db28c356`. The handoff's line references matched at that commit.
- Branch: `hq/lanes-inc-9-follow-up-mint-ping-uuid`.
- PR: #774, open and ready for review (not a draft), not merged.
- Code head: `6e93281f`, which `make check` and both reviews ran on. The report commit follows it
  on the same branch.

## What I built

The keeper id `46e714ca-32b7-4e89-9817-b509013363a5` is a BWS secret id. It isn't a secret, a ping
URL, or a check's ping UUID. It matches `.cred-consumers.toml`'s `follow-up-mint-ping-url` row
(`bws_uuid`) and `tests/rotation_worker/test_follow_up_mint_rows.py` (`KEEPER`).

1. `lanes/follow-up-mint/compose.yaml`:
   - Line 28: `SDS_DEADMAN_PING_URL_UUID: 46e714ca-32b7-4e89-9817-b509013363a5`.
   - Lines 13-19: I rewrote the header paragraph in #771's shape. Unlike the bump driver's, it says
     the outcome alarm reads the URL on every pass. `scripts/run-follow-up-mint.sh` takes no
     arguments (lines 45-48), so it has no `--dry-run`. It calls
     `sds_outcome_begin "$CHECK_NAME" follow-up-mint` with no `acting` argument (line 58), which
     defaults to 1 (`scripts/sds-deadman.sh:223`). The alarm pings `/start`, success, and `/fail`
     on every non-zero exit (launcher header lines 11-14).
2. `lanes/secrets-broker/allowlist.toml` line 53: the keeper id, with `# follow-up-mint-ping-url`,
   at the end of `[lanes.follow-up-mint]`.
   `tests/scripts/test_secrets_broker_allowlist.py`'s
   `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` already holds each entry
   equal to the launcher's UUID literals plus the compose's `SDS_DEADMAN_PING_URL_UUID`. It needed
   no change: once the compose names the id, the test requires this line. The mutation checks
   in the following section confirm this.
3. `.bws-secrets.toml` lines 578-589: one `[[secret]]` entry after `bump-driver-ping-url`, in its
   comment style: `name = "follow-up-mint-ping-url"`, `project = "Rotation / Keepers"`, and
   `lanes = ["follow-up-mint"]`. The comment says the lane reads it through the broker on every
   pass and has no token keeper.
4. `tests/scripts/test_deploy_lanes.py`, `test_every_lanes_ping_url_secret_is_listed_for_that_lane`:
   - `follow-up-mint` leaves `placeholders`, which now holds `change-window` only.
   - Line 1408: `follow-up-mint` joins the `<= set(named)` set, in alphabetical order.
   - The docstring is unchanged. It describes `placeholders` in general, which stays true for
     `change-window`.
5. `tests/rotation_worker/test_follow_up_mint_rows.py`: no existing assertion there said the
   compose value was empty. I added
   `test_the_ping_url_keeper_is_in_the_lane_s_manifest_secrets_and_its_broker_entry`
   (line 109 onward), with `tomllib`, `load_allowlist`, and `ALLOWLIST`. It's the same test as the
   bump driver's from #771.
6. `tests/scripts/test_run_follow_up_mint.py`. Unlike the bump driver's harness, this harness's
   default environment already names a stand-in ping-URL id (`PING_URL_UUID`, line 31). The three
   cases that set `SDS_DEADMAN_PING_URL_UUID=""` do so deliberately, to test the empty-id
   fallthrough, so all three stay:
   - Line 35: new constant `PING_URL_KEEPER`, the keeper id, distinct from the harness's stand-in.
   - Line 242, `test_without_a_ping_url_secret_a_pass_still_runs_and_pings_nothing`: kept. It tests
     the fallthrough to the name lookup. Its docstring said "The compose's empty
     `SDS_DEADMAN_PING_URL_UUID`" and now says "An empty `SDS_DEADMAN_PING_URL_UUID`".
   - Line 282: kept, but renamed from
     `test_on_the_socket_with_the_composes_empty_ping_id_no_lane_secret_reaches_bws` to
     `test_on_the_socket_with_an_empty_ping_id_no_lane_secret_reaches_bws`. Its docstring said "As
     the compose ships: the ping-URL id is empty" and now says "With the ping-URL id empty". It
     tests the same fallthrough on the socket. The shipped case, with the id named, is
     `test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it` (line 267).
   - Line 318, the dead-socket test: unchanged. The empty id keeps its assertions about the
     bearer's read alone, and its docstring doesn't mention the compose.
   - Line 341, `test_the_compose_meets_the_add_refusals_and_maps_nothing_else`: this test stood in
     for the compose, so I updated it. It expects `PING_URL_KEEPER` in the environment, with #771's
     comment, and the allowlist entry to be `{SYSTEM_UUID, PING_URL_KEEPER}`.
   - Line 387: `test_the_repositorys_manifest_lists_the_lanes_one_secret` becomes
     `test_the_repositorys_manifest_lists_the_lanes_secrets`. It expects the bearer and the keeper,
     and checks that the launcher names the bearer but not the keeper, whose id comes from the
     compose through `lane-run`. The manifest change fails the old test, so this follows #771's
     `test_run_bump_driver.py` change.
   - No empty-id case was removed. The fallthrough stays tested at lines 242, 282, and 318.
7. Docs this change made stale:
   - `docs/operations/deploy.md` lines 1205-1208: "Each pass reads one secret ... the broker's
     allowlist lists exactly that one" becomes "Each pass reads two secrets ... the SYSTEM bearer
     ... and its ping-URL secret ... the broker's allowlist lists exactly these two".
   - `docs/operations/deploy.md` lines 1215-1217: "Its ping-URL secret joins its secrets, and the
     broker's allowlist, when it exists; until then the compose's `SDS_DEADMAN_PING_URL_UUID` is
     empty and the lane is in `test_deploy_lanes.py`'s `placeholders`" becomes a sentence saying
     that the compose names the id of `follow-up-mint-ping-url`, which the outcome alarm reads
     through the socket on every pass, and that `.bws-secrets.toml` lists it.
   - `docs/operations/credentials.md` line 98: "its own ping-URL secret once that exists" becomes
     "its own ping-URL secret".
   - I left item 7 (deploy.md line 1249) unchanged, as #771 and #767 left theirs. Item 5 already
     says "the lane's two secrets", and item 8 says "both secrets".

No ADR, launcher, `lane-run`, `sds-deadman.sh`, `scripts/secrets_broker.py`, crontab,
`.cred-consumers.toml`, `test_secrets_broker_row.py`, or other lane's compose or allowlist entry
changed.

### Item 8 confirmations (unchanged files)

- `lane-run` passes `SDS_DEADMAN_PING_URL_UUID`: `scripts/lane_run.py:70` defines
  `COPIED = (TOKEN, "TZ", "SDS_DEADMAN_PING_URL_UUID")`, and line 109 copies it into the
  launcher's environment. Lines 112-113 set `SDS_SECRETS_SOCKET` when the socket is mounted.
- Under the broker, the outcome alarm reads the URL by that id through the socket.
  `sds_outcome_begin` (`scripts/sds-deadman.sh:222-226`) calls `sds_deadman_resolve` when
  `acting` is 1. That calls `sds_deadman_resolve_by_uuid` when the id is set (lines 146-149). It
  accepts a socket in place of a token (`sds_in_lane_container`, line 106) and calls
  `sds_bws_value "$SDS_DEADMAN_PING_URL_UUID"` (line 118). That function's one-argument form reads
  through the socket when `SDS_SECRETS_SOCKET` names one (`scripts/sds-bws.sh:77`).
  `test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it` proves this in the
  harness: `bws` never runs, and the pings go to the served URL.

## Tests and checks

- Targeted: `test_deploy_lanes.py`, `test_run_follow_up_mint.py`, `test_follow_up_mint_rows.py`,
  and `test_secrets_broker_allowlist.py`: 165 passed.
- Mutation checks. Each mutation ran from a backup copy, was restored with `cp`, and was confirmed
  with `cmp`, clearing `__pycache__` around each run. The set covered the rows test file, the
  ping-URL guard, the harness's manifest and compose tests, and the allowlist test file. Control
  run: 14 passed.
  - The keeper's manifest entry with `lanes = []` (that entry only): the new rows test,
    `test_every_lanes_ping_url_secret_is_listed_for_that_lane`, and
    `test_the_repositorys_manifest_lists_the_lanes_secrets` failed (3 failed, 11 passed).
  - The allowlist line removed: the new rows test,
    `test_the_compose_meets_the_add_refusals_and_maps_nothing_else`, and
    `test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` failed (3 failed).
  - The compose value set back to `""`: `test_every_lanes_ping_url_secret_is_listed_for_that_lane`,
    the compose test, and the allowlist test failed (3 failed).
- `make check` on `6e93281f`, database `orchestrator_test_hq_w_259`, exited 0:
  - Ruff: "All checks passed!", 1040 files already formatted.
  - Pyright: 0 errors, 0 warnings.
  - Tests: the log reports `10 workers [14874 items]`. Result: 14873 passed, 1 skipped.
- Security scan from the repository root: 0 BLOCK, 2 WARN, and 1 INFO
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`, and `bws.least-privilege-scope`).
  These are the same three #771 reported, and the diff causes none of them.
- `code-standards risk`: `high-risk` (secrets: `.bws-secrets.toml` and
  `lanes/secrets-broker/allowlist.toml`), so I ran `vendor-review` on `origin/main...HEAD`. Claude
  and Codex both approve, with 0 findings kept and 0 dropped. agy: off by configuration.
- `/code-review` (low) on `origin/main...HEAD`: the forked review returned no findings.

## Files changed

- `lanes/follow-up-mint/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `.bws-secrets.toml`
- `docs/operations/deploy.md`
- `docs/operations/credentials.md`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_worker/test_follow_up_mint_rows.py`
- `tests/scripts/test_run_follow_up_mint.py`
- `docs/superpowers/reports/lanes-inc-9-follow-up-mint-ping-uuid-report.md` (this report)

## Self-review

- The diff follows #771. The one difference in its text is that the read happens on every pass,
  because the launcher has no `--dry-run`.
- I made no production call: no Coolify, BWS, Healthchecks, or VPS call. I ran nothing in
  `~/Projects/orchestrator`.

## Concerns

- The broker serves the allowlist built into its own release. Until the broker moves to a release
  with this entry (item 8), a lane container on this release would have its ping-URL read refused
  (`not_allowed`) and would ping nothing, though the pass still runs. Item 8 already moves the
  broker before the lane's `--add`, so nothing needs to change, but HQ must keep that order.
- HQ merges #774 after the executor's write (item 6). Its release is the served-release half of
  item 7.
- Out of scope, so I didn't edit it: `scripts/run-follow-up-mint.sh` lines 22-25 say the broker's
  allowlist lists "exactly this one". The entry now lists two: the bearer and the ping-URL keeper,
  whose id reaches the launcher from the compose, as the allowlist header (lines 14-16) states. The
  handoff bars launcher changes, so HQ can fold this into a later launcher change, as with #771's
  `run-bump-driver.sh:46`. ADR-0059 line 3149 ("exactly two read grants" for a lane account that
  Amendment 10 planned) is a decision record, not made stale by this diff, so it stays.
- I'm leaving the test database `orchestrator_test_hq_w_259` in place for HQ's teardown.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): the diff changes no logging.
- **Code Brain rule 4** (never swallow an exception silently): the added test catches nothing. It
  asserts directly.
- **Code Brain rule 5** (never log secrets): no log line or test output prints a ping URL, a
  check's ping UUID, or a token. The only id the diff adds is the keeper's BWS secret id. The
  compose and the harness constant label it "not a secret, not the ping URL", and the harness
  keeps it distinct from its stand-in id.
- **Infra Brain 591, 592, and 593**: no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value. The compose maps no token at all. The scan has 0 BLOCK findings, and
  the diff adds no test token values.
- **Infra Brain 1032** (exposure means rotate): I saw no secret value during the work.
- **Infra Brain 212** (HTTPS for public FQDNs): doesn't apply. The lane serves nothing: no port, no
  domain.
- **Infra Brain 2890** (never commit a ping URL): the diff commits one BWS secret id and no
  `https://healthcheck.alobar.net/ping/...` URL. The harness's stand-in URL is
  `https://hc.example/ping/follow-up-mint`, unchanged.
- **Infra Brain lesson 450** (rotation landmines): ids only. No secret value appears in a test, the
  report, or a log.
