Outcome: done

# Report: the revision watcher's registry rows (ADR-0059 increment 3, cut-over step 1)

- **PR:** #658, ready for review, not merged. Branch `hq/lanes-inc-3-revision-watcher-rows`.
  Base `origin/main` `fa5ce3d0`. The code commit is `b27e2831`; this report's commit follows it.
- **Worker:** W-85. Test database `orchestrator_test_hq_w_85`.

## What I built

Change 1 of the two changes (ADR-0055 amendment 19 item 8 step 2; ADR-0059 "Cut-over for one
lane" step 1) for the `revision-watcher` lane. The model is the deploy watcher's block from #656.

- `.cred-consumers.toml`, a block "The revision-watcher lane's token and ping URL (ADR-0059
  increment 3)" (lines 830-876), placed right after the deploy watcher's block:
  - `revision-watcher-bws-token`: class and provider `bws-lane-token`, keeper
    `e6c8d8cc-c64e-4e91-bb5b-b4ff00cba872`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-11"`, six `reach_secrets` (check 1), and `reach_projects = []`.
    It has two consumers: the keeper (`bws-secret`, the executor reads it) and `coolify-env`
    (`prod`, `service`, `kmjkrhlmv6c7ee76g77a8491`, `REVISION_WATCHER_BWS_ACCESS_TOKEN`,
    `redeploy = false`).
  - `revision-watcher-ping-url`: class and provider `healthchecks-ping-url`, keeper
    `9b2722ea-8ec1-425b-ac2e-b4ff00cba931`, `rotated_by_sds = true`,
    `healthchecks_check = "sds-revision-watcher"`, and a single `bws-secret` consumer (the keeper).
    It carries no `rotate_requested`, the same as the deploy watcher's ping-URL row.
  - Neither row carries `created`. The block comment says the token row is due, a later change
    dates the ping-URL row, and both keepers came from G-30.
- In the `lanes-read-app-private-key` row, I added a new `runtime-fetch` consumer (lines 921-926)
  with these fields:
  - `repo = "orchestrator"`.
  - `ref = "scripts/run-revision-watcher.sh"`. I confirmed the script: line 60 sets
    `READ_APP_KEY_UUID`, and lines 216-217 pipe the key into `sds-github-mint`.
  - `healthchecks_check = "sds-revision-watcher"`.
  - A note saying the key is read in the `sds-lane-revision-watcher` container with the lane's own
    token, each pass, and piped into `sds-github-mint`.

  I also updated the block comment: three lane accounts now hold grants (G-18, G-28, and G-30).
- `.bws-secrets.toml`: the comment on `lanes-read-app-private-key` now says the revision-watcher
  account holds a grant (G-30). Only `unit-verifier` still waits for its grant. No `[[secret]]`
  entry changed.
- `tests/rotation_worker/test_revision_watcher_rows.py`, a new test with 13 cases, modelled on
  `test_deploy_watcher_rows.py`.
- `tests/scripts/test_deploy_lanes.py`: renamed to
  `test_the_repositorys_registry_registers_the_four_moved_lanes`, which now expects
  `revision-watcher` too.

I didn't touch the Mac `runtime-fetch` entries for `run-revision-watcher.sh` on other rows (lines
139 and 407). No code or test says otherwise.

## Checks

### Check 1: `reach_secrets`, derived

These are the `.bws-secrets.toml` entries whose `lanes` include `revision-watcher`:

- `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` `orchestrator-observer` (line 111).
- `bbd71f41-b7df-4ae9-8fdb-b41501447308` `prod-coolify-api-token` (line 139).
- `726a18ba-7a38-4ecc-aa03-b49a015fd302` `APP_BRAIN_READ_KEY` (line 204).
- `b415d03b-f92d-46b1-8a9d-b4f10103df49` `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` (line 250).
- `314a966c-5654-4e3c-8358-b4fd01268775` `lanes-read-app-private-key` (line 301).

The sixth is the ping-URL keeper, `9b2722ea-8ec1-425b-ac2e-b4ff00cba931`
(`revision-watcher-ping-url`). Those six are exactly the six G-30 grants. The test asserts both
equalities: the reach equals the manifest's lane secrets plus the keeper, and it equals the granted
set (`test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper`).

### Check 2: the rows, read through the code

`test_revision_watcher_rows.py` commits this repository's registry to a scratch origin and reads it
through `rotation_worker.registry.rotated_by_sds` from a `RepoMirror`, the way the executor reads
it. It then checks the following:

- `bws_lane_token.check_row` and `lane_variable` (`adapters/bws_lane_token.py:78,95`) return the
  service uuid and `REVISION_WATCHER_BWS_ACCESS_TOKEN`. The test also checks that
  `lanes/revision-watcher/compose.yaml` maps that variable to `BWS_ACCESS_TOKEN`.
- `healthchecks_ping_url.check_row` (`adapters/healthchecks_ping_url.py:64`) passes, and the row
  names `sds-revision-watcher`, the check that `run-revision-watcher.sh:90` arms.
- The no-two-rows-share-a-reach rule (`sharing_reach`, `bws_lane_token.py:108-122`) compares whole
  sets with `declared_reach(other) == mine`. The test runs against each of the three other lane
  tokens (deploy-watcher, execution-watch, landing-ledger). For each, it asserts the reach sets
  overlap (all share `orchestrator-observer`), that `sharing_reach` returns `[]`, and that a twin
  with the other row's exact reach is reported.
- Only the token row is due. Neither row has `created` or `last_rotated`. `plan_for(...,
  first_issue=True)` derives an issue plan for both rows.
- The read App key's row lists a `runtime-fetch` reader for `scripts/run-revision-watcher.sh`
  with `sds-revision-watcher`, and that script names the key's id.

Result: 13 passed.

**Mutation check:** I copied `.cred-consumers.toml` to `/tmp`, set the row's `redeploy = true`,
and cleared the `__pycache__` folders. The run gave `1 failed, 12 passed`, with this error:

```
ContractError: a lane token's coolify-env has redeploy false: the lane redeploys itself
```

That error comes from `bws_lane_token.py:85`. I then restored the file from the copy, and the rerun
gave `13 passed`, which confirms the restore.

### Check 3: due-ness

In infraops `src/security-drift/cred-rotation.ts` (checkout `c3359bf1`):

- `bws-lane-token` has `maxAgeDays: 365` (line 95). With no `created`, `rotationAnchor` (lines
  209-210) has no anchor, so there's no age finding.
- `healthchecks-ping-url` (lines 97-98) is never due by age.
- `cred.rotation-requested` (lines 279-290) fires when `now >= Date.parse(rotate_requested)` and
  no rotation is recorded since. With no `rotate_requested` the value is `NaN`, so the comparison
  is false.

To run `cred-findings`, I made a scratch copy of the checkout with `rsync`, excluding `.git`, at
`/tmp/w85-infraops.Wg4H`. I used its existing `dist` and didn't build. I pointed
`INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` at a scratch config. That config listed only
this worktree's `.cred-consumers.toml` and held an empty mode-0600 state file. Then I ran
`node dist/cli/security-drift-cli.js cred-findings --now 2026-11-11T13:00:00Z`.

- **This branch's registry** exits 0 with 7 findings, all `cred.rotation-requested`. The only
  revision-watcher finding is `revision-watcher-bws-token` (`bws-lane-token`,
  `rotate_requested 2026-11-11`). There's none for `revision-watcher-ping-url`. The other six are
  the drift-reporter row and the execution-watch, landing-ledger, and deploy-watcher token rows,
  plus the execution-watch and landing-ledger ping-URL rows. They show only because the scratch
  state is empty. `last_rotated` lists both new rows as `null`.
- **Control:** I made a scratch copy of the registry with `rotate_requested = "2026-11-11"` on the
  ping-URL row. That run gave 8 findings, adding `revision-watcher-ping-url`.
- `git status --porcelain` in `~/Projects/infraops-mcp-server` printed nothing. I read
  `~/.config/infra-drift/cred-consumers.list` (check 5) and didn't use it to run anything.

### Check 4: fixtures and existing tests

I grepped `tests/` for every reader of `.cred-consumers.toml` and of the lane and App key rows.
Then I read each hit's loading code to see whether it reads the repository's file or a fixture.

**Tests that read the repository's file:**

- `tests/scripts/test_deploy_lanes.py:843-853`: reads the file through `deploy.lanes_in_registry`.
  It pinned three lanes, so I updated it to four.
- `test_deploy_watcher_rows.py` and `test_landing_ledger_rows.py`: each selects the App key's
  reader by its own launcher's `ref`. Each runs the reach rule against named other rows: the
  deploy watcher against execution-watch and landing-ledger, the landing ledger against
  execution-watch. The new rows don't change what they assert.
- `test_execution_watch_rows.py`: reads only its own two rows and the manifest's
  `execution-watch` secrets. It has no App key or reach-rule test, because the read App key's
  `lanes` doesn't name `execution-watch`.
- `test_registry_array_strings.py:39`: asserts the file has no comma inside an array string,
  because infraops's TOML subset would split on it. The new rows' notes and `reach_secrets` pass.
- `tests/unit_verifier/test_unit_verifier_launcher.py:38`: selects the bootstrap-token row by
  `keeper_account`, which none of my edits touch.

**Tests that use inline fixtures or fakes, not the repository's file:**

- The other `lanes_in_registry` and `registered_lanes` tests in `test_deploy_lanes.py` (lines
  786-840).
- `test_registry_and_compare.py` (a `CANARY` string).
- `tests/scripts/test_deploy_orchestrator.py:109` (a `REGISTRY` string).
- `tests/rotation_proposer/test_pass.py`, `tests/rotation_decomposer/test_pass.py`, and
  `tests/work_carrier/test_operational_branch.py` (raised `RegistryError` fakes).
- `tests/membership_check/test_rotation_scope.py`, `test_declaration.py`, and
  `tests/architecture/test_membership_edges.py` (fake API paths).
- `tests/fork_sync/test_onboard.py` and `test_watch.py` (factory-runner's registry, faked).

None of these needed a change.

**Fixtures:** I grepped `tests/fixtures/rotation_*` for `revision-watcher`, `deploy-watcher`, and
`lanes-read`, and found no hits, so no fixture enumerates the lane rows. I didn't open each
fixture file. No file under `tests/fixtures/` has "agreement" in its name. The agreement checks
(`test_hold_agreement.py`, `test_target_grant.py`, and others) don't read the registry; none
appeared in the grep.

Before `make check`, `tests/rotation_worker`, `test_deploy_lanes.py`, `tests/rotation_proposer`,
`tests/membership_check`, and `tests/unit_verifier` gave 2153 passed.

**`registered_lanes` once this merges:** `registered_lanes(world)`
(`scripts/deploy_orchestrator.py:1255-1257`) is `lanes_in_registry` over `origin/main`'s registry.
It returns `{"deploy-watcher", "execution-watch", "landing-ledger", "revision-watcher"}`.
`revision-watcher` is already in `LANE_CHECKS` (line 964), so the matcher accepts it. A registered
lane holds no release until its `--add` passes (`require_lanes_current`, lines 1265-1290).

### Check 5: how the rows go live

- **The 03:00 scan and `cred-findings`** read the files listed in
  `~/.config/infra-drift/cred-consumers.list`. See infraops `src/security-drift/paths.ts:46` and
  `src/cli/security-drift-cli.ts:119,171-173`. The orchestrator entry, at line 4 of the list, is
  `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main tree's working copy.
- **The rotation proposer** gets what's due only from that `cred-findings` answer
  (`scripts/run-rotation-proposer.sh:7-12`: "Neither file is opened by this program").
- **The main tree moves to `origin/main`** through each lane launcher's activation helper
  (ADR-0031, `docs/decisions/0031-a-merged-change-is-not-activated-until-the-consumer-pulls.md`).
  For example, `scripts/run-revision-watcher.sh:65-80` sources
  `~/.claude/bin/activate-checkout.sh` and calls `activate_checkout`. That helper fast-forwards
  with `git merge --ff-only '@{u}'` (line 93). After the merge, the next Mac launcher pass that
  activates brings the rows into the working copy that the scan and the proposer read.

### Gates

- `make check` with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` at
  `orchestrator_test_hq_w_85`, `SECURITY_STANDARDS_DIR` at the fixture, and `PGTZ=UTC`: exit 0.
  Lint and format passed; pyright reported 0 errors. The test run gave
  `14377 passed, 1 skipped, 11 warnings in 367.13s`.
- Security scan from the repo root: exit 0, `BLOCK: 0`, `WARN: 2`, `INFO: 1`. Neither WARN involves
  this diff:
  - `bws.secret-files-gitignored`: glob `*-migration/`.
  - `bws.manifest-matches-usage`: undeclared `aaff45ca-…`, an id this diff doesn't touch.

  The INFO is the judgment rule `bws.least-privilege-scope`.
- `/code-review` (low) on `origin/main...HEAD`: no findings. The first run returned an empty
  result, so I ran it again. The second run said it reviewed `.bws-secrets.toml` and
  `.cred-consumers.toml`, skipped the tests and this report, and found nothing.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_revision_watcher_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-3-revision-watcher-rows-report.md` (this report)

## Self-review

- The rows mirror the deploy watcher's field for field. Only the ids, the service uuid, the key,
  the check, the date, the gate, and the reach differ, and each value comes from the handoff or
  the files it names.
- The test is the deploy watcher's test with this lane's values. It adds the deploy watcher's
  token to the reach-rule comparisons, so it covers every other lane-token row.

## Deviations

- I edited one comment in `.bws-secrets.toml` (the read App key's grants), which the handoff
  didn't list. I made that edit so that comment stays true, the same way #656 edited it for
  `deploy-watcher`.

## Concerns and follow-ups

- `lanes/revision-watcher/compose.yaml` still has `SDS_DEADMAN_PING_URL_UUID: ""`. Its header
  says a later change writes the keeper's id there. That change is outside this handoff, along
  with dating the ping-URL row.
- `.bws-secrets.toml` has no `[[secret]]` entry for the ping-URL keeper `9b2722ea-…` with
  `lanes = ["revision-watcher"]`. The landing-ledger and execution-watch keepers have one, and the
  deploy watcher's keeper doesn't either (#656's report flagged the same gap). The reach test
  passes because it adds the keeper separately. The compose change that writes the id is the
  natural place to add that entry.
- Teardown: the worktree and `orchestrator_test_hq_w_85` stay until HQ merges the PR.
  `git -C ~/Projects/orchestrator status --porcelain` printed nothing, so this session left no
  stray files in the main tree.
  `/tmp/w85-*` holds scratch files only: the infraops copy, configs, logs, and a registry copy.
  None holds a value.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff is registry
  data, comments, and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try` or
  `except`. Each check raises or asserts.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, a ping URL's
  UUID, or the App key. The rows and the test name BWS secret ids only, and none of those is the
  UUID inside a ping URL. The `cred-findings` output quoted here carries ids, classes, and dates
  only.
- **Infra Brain 591, 592, and 593 (no BWS token in tracked files or history; the bootstrap token
  never inline):** the diff holds no token-shaped value, and the security scan reports no BLOCK.
  The test uses the registry's real secret ids, which identify secrets and aren't values.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` URL
  appears in the diff. The row names only the check, `sds-revision-watcher`.
- **Infra Brain 1032 (`cred.exposure-rotate`):** neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because nothing changes a domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test, or a log. The rows name ids only, and the scratch state file held no values.
