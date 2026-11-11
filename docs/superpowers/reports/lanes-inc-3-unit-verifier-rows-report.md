Outcome: done

# Report: the unit verifier's registry rows (ADR-0059 increment 3, cut-over step 1)

- **PR:** [#663](https://github.com/AlobarQuest/orchestrator/pull/663), ready for review, not
  merged. Branch `hq/lanes-inc-3-unit-verifier-rows`. The change commit is `1cfab1ee`; the report
  commit follows it.
- **Base:** `origin/main` at `f0213b76` (#661). Every fact the handoff read at `2b5a18d6` held at
  this base.

## What I built

I built change 1 of the two changes for the `unit-verifier` lane, modelled on the revision
watcher's block from #658.

- **`.cred-consumers.toml`:** I added the block "The unit-verifier lane's token and ping URL
  (ADR-0059 increment 3)" at lines 880-925, right after the revision watcher's block. It holds two
  rows:
  - `unit-verifier-bws-token` (line 886) has class and provider `bws-lane-token`,
    `bws_uuid = "c8381bef-…"`, `rotated_by_sds = true`, and `rotate_requested = "2026-11-11"`.
    Its `reach_secrets` lists four ids (see check 1), and `reach_projects = []`. Its consumers are
    the keeper (`bws-secret`, the same uuid) and `coolify-env` with `instance = "prod"`,
    `resource_type = "service"`, `uuid = "y144dc0981smrtzl587hqa4f"`,
    `key = "UNIT_VERIFIER_BWS_ACCESS_TOKEN"`, and `redeploy = false`.
  - `unit-verifier-ping-url` (line 914) has class and provider `healthchecks-ping-url`,
    `bws_uuid = "1cfc8352-…"`, `rotated_by_sds = true`, and
    `healthchecks_check = "sds-unit-verifier"`. Like the revision watcher's row, it has no
    `rotate_requested`. Its one consumer is the keeper.
  - Neither row carries `created`. The block comment says the token is due, a later change dates
    the ping URL, and both keepers were created at HQ gate G-33.
- **The `lanes-read-app-private-key` row:** I added a `runtime-fetch` consumer at lines 977-983:
  `repo = "orchestrator"`, `ref = "scripts/run-unit-verifier.sh"`, and
  `healthchecks_check = "sds-unit-verifier"`. Its note follows the revision watcher's wording. I
  updated the block comment at lines 938-941 to name the unit-verifier account and gate G-33.
- **`.bws-secrets.toml` (comment only, lines 297-299):** The read App key's comment said
  "`unit-verifier` names the grant its account gets at its cut-over". It now says the account
  holds the grant (G-33). #658 made the same edit for the revision watcher. The handoff didn't
  name this file, so I've listed it under deviations.
- **`tests/rotation_worker/test_unit_verifier_rows.py` (new, 16 tests):** It's modelled on
  `test_revision_watcher_rows.py`, with two additions that #658's review asked for:
  `variable["redeploy"] is False`, and each row's `bws-secret` consumer uuid equals its
  `bws_uuid`.
- **`tests/scripts/test_deploy_lanes.py:843-855`:** The registry test now expects five moved lanes
  instead of four.

I left the existing Mac `runtime-fetch` entries for `run-unit-verifier.sh` unchanged, at lines
164-169 (the bootstrap-token row) and 256-261. `tests/unit_verifier/test_unit_verifier_launcher.py:37-44`
pins the first one.

## Checks

### Check 1: `reach_secrets`, derived

These are the `.bws-secrets.toml` entries whose `lanes` include `unit-verifier`:

- `660d5846-abcb-4751-be86-b483012899eb`, `orchestrator-verifier-bearer` (lines 55-58,
  `lanes = ["unit-verifier"]`).
- `b415d03b-f92d-46b1-8a9d-b4f10103df49`, `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` (lines 250-253).
- `314a966c-5654-4e3c-8358-b4fd01268775`, `lanes-read-app-private-key` (lines 301-304).

The fourth id is the ping-URL keeper, `1cfc8352-c960-4afc-bd49-b4ff00e8783e`. Like the revision
and deploy watchers' keepers, it has no `.bws-secrets.toml` entry. This set matches HQ's three and
the four grants made at G-33. `test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper`
asserts that `declared_reach` equals the manifest set plus the keeper, and also equals `GRANTED`.

### Check 2: the rows, parsed through the code

`test_unit_verifier_rows.py` commits this repository's registry to a scratch origin. It reads the
registry through `rotation_worker.registry.rotated_by_sds` at `main` using `RepoMirror`, as the
executor does. The tests cover the following:

- Both rows are handed to the SDS.
- `bws_lane_token.check_row` and `lane_variable` accept the token row. The variable has uuid
  `y144dc0981smrtzl587hqa4f`, key `UNIT_VERIFIER_BWS_ACCESS_TOKEN`, and `redeploy is False`.
  `lanes/unit-verifier/compose.yaml:23` maps that key to the container's `BWS_ACCESS_TOKEN`.
- `healthchecks_ping_url.check_row` accepts the ping-URL row, and its check is
  `sds-unit-verifier`.
- **The reach rule:** for each of the four other lane-token rows (deploy-watcher, execution-watch,
  landing-ledger, and revision-watcher), the test does three things. First, it asserts that the
  reach sets overlap. Execution-watch and revision-watcher share the alert sender; landing-ledger,
  deploy-watcher, and revision-watcher share the read App key. Second, it asserts that
  `sharing_reach` returns `[]` for the real row. Third, it builds a twin with the other row's reach
  and asserts that `sharing_reach` returns `[other]`. Together these show the rule compares whole
  reach sets and that this row passes.
- Each row's keeper consumer uuid equals its `bws_uuid`.
- Only the token row is due. Neither row has `created` or `last_rotated`. `plan_for(…,
  first_issue=True)` derives an issue plan for both.
- The read App key's `runtime-fetch` reader for `scripts/run-unit-verifier.sh` is
  `repo = "orchestrator"` with check `sds-unit-verifier`. The launcher names the key's
  `bws_uuid`: `READ_APP_KEY_UUID` is at line 45 and is piped into `sds-github-mint` at lines
  135-136.

**Mutation check.** I copied `.cred-consumers.toml` to `/tmp`, mutated it, ran the file, and
restored the copy. Each mutation made exactly one test fail:

1. `redeploy = true` on the token's `coolify-env`: 1 failed, 15 passed
   (`test_the_token_row_is_one_the_lane_token_adapter_acts_on`). `check_row` refuses first
   ("redeploy false"), so the direct `is False` assertion is a second line of defense behind it.
2. Changing the ping keeper consumer's uuid `1cfc8352` to `1cfc8353`: 1 failed, 15 passed
   (`test_each_row_s_keeper_consumer_is_its_bws_uuid[unit-verifier-ping-url]`).

After the restore, the file gave 16 passed and `git diff` showed only the intended edits.

### Check 3: due-ness

From infraops `src/security-drift/cred-rotation.ts`, at infraops `c3359bf`:

- `bws-lane-token` has a 365-day maximum age (line 95), but neither row has `created` or
  `last_rotated`. `rotationAnchor` (lines 209-211) therefore returns no anchor, and there's no age
  finding.
- `healthchecks-ping-url` has `maxAgeDays: Number.POSITIVE_INFINITY` (lines 96-102), so it's never
  due by age.
- `cred.rotation-requested` (lines 279-291) fires when `nowMs >= Date.parse(rotate_requested)` and
  no rotation is recorded after it. On the token row, `2026-11-11` with nothing rotated fires. On
  the ping-URL row, with no `rotate_requested`, `Date.parse` isn't called, so `requestedMs` is
  `NaN` and the comparison is false.

**The `cred-findings` run.** I copied `~/Projects/infraops-mcp-server` with `rsync`, excluding
`.git`, to `/tmp/w90-infraops.FuZE`, and used its existing `dist` without building. I set
`INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` to a scratch config. That config listed
only this worktree's `.cred-consumers.toml` and held a mode-0600 state file with no rotations
recorded (`{"resolvedExposures":{},"lastRotated":{}}`). Then I ran
`node dist/cli/security-drift-cli.js cred-findings --now 2026-11-11T17:00:00Z`.

- **This branch's registry:** exit 0, 9 findings, all `cred.rotation-requested`. The only
  unit-verifier finding is `unit-verifier-bws-token bws-lane-token 2026-11-11`; there's none for
  `unit-verifier-ping-url`. The other eight are the drift-reporter row and the execution-watch,
  landing-ledger, deploy-watcher, and revision-watcher rows that the empty state leaves unrotated.
  `last_rotated` lists both new rows as `null`.
- **Control:** I ran a copy of the registry with `rotate_requested = "2026-11-11"` added to the
  ping-URL row. It gave 10 findings, which added `unit-verifier-ping-url`.
- `git status --porcelain` in `~/Projects/infraops-mcp-server` printed nothing. I didn't use
  `~/.config/infra-drift/cred-consumers.list` for any run. I read it only to cite check 5.

### Check 4: fixtures and existing tests

I grepped `tests/`, `scripts/`, and `src/` for readers of `.cred-consumers.toml`, of the lane rows,
and of the App key rows. Then I read each test's loading code.

**Tests that read the repository's file:**

- `tests/scripts/test_deploy_lanes.py:843-855` reads the file through `deploy.lanes_in_registry`.
  I updated it from four lanes to five.
- `test_deploy_watcher_rows.py`, `test_landing_ledger_rows.py`, `test_revision_watcher_rows.py`,
  and `test_execution_watch_rows.py` each read their own rows. Each selects the App key reader by
  its own launcher's `ref`, and each runs the reach rule only against the other rows it names. The
  new rows don't change what they assert. All of them pass.
- `test_registry_array_strings.py:39` asserts the file has no comma inside an array string. The
  new rows pass it.
- `tests/unit_verifier/test_unit_verifier_launcher.py:37-44` selects the bootstrap-token row by
  `keeper_account` and pins its Mac reader for `run-unit-verifier.sh`, which I left unchanged.
- `tests/rotation_proposer/test_standing_approval.py:55-58` reads only the landing ledger's rows.

**Tests that use inline fixtures or fakes:** the other `lanes_in_registry` and `registered_lanes`
tests (`test_deploy_lanes.py:786-840`), `test_deploy_orchestrator.py:109`,
`test_registry_and_compare.py`, and the fork-sync and membership-check tests. None needed a
change.

`test_every_lanes_ping_url_secret_is_listed_for_that_lane` (`test_deploy_lanes.py:1183-1203`)
reads the lane compose files, not the registry. It keeps `unit-verifier` in `placeholders`, because
the compose's `SDS_DEADMAN_PING_URL_UUID` is still blank. Change 2 owns that edit.

**`registered_lanes` once this merges:** `registered_lanes(world)`
(`scripts/deploy_orchestrator.py:1255-1257`) is `lanes_in_registry` over `origin/main`'s
registry. It returns `{"deploy-watcher", "execution-watch", "landing-ledger", "revision-watcher",
"unit-verifier"}`. `unit-verifier` is already in `LANE_CHECKS` (line 967), so the matcher accepts
it. A registered lane holds no release until its `--add` passes (`require_lanes_current`, lines
1265 onward).

### Check 5: how the rows go live

- **The 03:00 scan and `cred-findings`** read the files listed in
  `~/.config/infra-drift/cred-consumers.list`. See infraops `src/security-drift/paths.ts:46` and
  `src/cli/security-drift-cli.ts:119,171-173`. The orchestrator entry, at line 4 of the list, is
  `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main tree's working copy.
- **The rotation proposer** gets what's due only from that `cred-findings` answer
  (`scripts/run-rotation-proposer.sh:7-12`: "Neither file is opened by this program").
- **The main tree reaches `origin/main`** through each launcher's activation helper (ADR-0031,
  `docs/decisions/0031-a-merged-change-is-not-activated-until-the-consumer-pulls.md`). For example,
  `scripts/run-unit-verifier.sh:54-64` sources `~/.claude/bin/activate-checkout.sh` and calls
  `activate_checkout`, which fast-forwards with `git merge --ff-only '@{u}'` (line 93). After the
  merge, the next Mac launcher pass that activates brings the rows into the working copy that the
  scan and the proposer read.

## Gates

- `make check` ran with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` set to
  `orchestrator_test_hq_w_90`, `SECURITY_STANDARDS_DIR` set to the fixture, and `PGTZ=UTC`. It
  exited 0. Lint and format passed, and pyright reported 0 errors. The test run gave
  `14432 passed, 1 skipped, 11 warnings in 410.90s`.
- Before `make check`, I ran a targeted set: the five lane-row files, `test_deploy_lanes.py`,
  `test_registry_array_strings.py`, `tests/unit_verifier`, and `test_standing_approval.py`. It
  gave 307 passed.
- The security scan from the repo root exited 0 with BLOCK 0, WARN 2, and INFO 1. The WARNs
  predate this diff and match #658's report:
  - `bws.secret-files-gitignored`: glob `*-migration/`.
  - `bws.manifest-matches-usage`: undeclared `aaff45ca-…`, which doesn't appear in this diff.

  The INFO is the judgment rule `bws.least-privilege-scope`.
- `/code-review` (low) on `origin/main...HEAD` returned no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_unit_verifier_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-3-unit-verifier-rows-report.md` (this report)

## Self-review

- I checked every literal value against its source in the repository: the compose key (line 23),
  the launcher's `READ_APP_KEY_UUID` (line 45), `LANE_CHECKS` (line 967), and the manifest lanes.
  The service uuid `y144dc0981smrtzl587hqa4f` and the two keeper ids appear nowhere else in the
  repository. They come from the handoff (G-33), and I couldn't confirm them against a source
  without a Coolify or BWS call, which the handoff forbids.
- The new test differs from `test_revision_watcher_rows.py` only in the lane's values, the added
  revision-watcher row in `OTHER_LANE_TOKENS`, a corrected docstring on the reach test (this lane
  doesn't hold the observer bearer), and the two added assertions.

## Deviations

- The `.bws-secrets.toml` comment edit (lines 297-299) wasn't named in the handoff. Without it, the
  comment would still say the unit-verifier grant is to come. #658 made the same edit for its
  lane.

## Concerns

None. The handoff's Standards section covers everything this change touches.

## Follow-ups

- Change 2 dates the ping-URL row. It also fills `SDS_DEADMAN_PING_URL_UUID` in
  `lanes/unit-verifier/compose.yaml` and drops `unit-verifier` from `placeholders` in
  `test_every_lanes_ping_url_secret_is_listed_for_that_lane`.
- I left the test database `orchestrator_test_hq_w_90` and the `/tmp/w90-*` scratch files (the
  infraops copy, the configs, and the findings JSON) for HQ's teardown. None of them holds a
  secret value.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** This change has no logging; it's
  registry data and tests.
- **Code Brain rule 4 (never swallow an exception silently):** The new tests catch nothing. Every
  failure, including the adapters' `ContractError`, reaches pytest.
- **Code Brain rule 5 (never log secrets):** Nothing added prints or holds a token, a ping URL, the
  UUID inside a ping URL, or the App key. The rows and the tests name BWS secret ids, which aren't
  ping-URL UUIDs.
- **Infra Brain 591, 592, and 593:** The diff and its history contain no BWS token and no inline
  bootstrap token. The new test needs no placeholder values. The security scan reports BLOCK 0.
- **Infra Brain 2890:** The diff contains no `https://healthcheck.alobar.net/ping/` URL.
- **Infra Brain 1032 (cred.exposure-rotate):** Neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** Doesn't apply, because this change touches no
  domain.
- **Infra Brain lesson 450 (rotation landmines):** No secret value enters the registry, a test, or a
  log. The rows name ids only, and the scratch `cred-findings` state held no values.
