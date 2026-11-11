Outcome: done

# Report: the deploy watcher's registry rows (ADR-0059 increment 3, cut-over step 1)

Worker W-83. Branch `hq/lanes-inc-3-deploy-watcher-rows`, base `origin/main` at `9d0a1a3f`.
PR: PR_PLACEHOLDER. The PR is open and marked ready, and I haven't merged it.

## What I built

- **`.cred-consumers.toml`, a block "The deploy-watcher lane's token and ping URL (ADR-0059
  increment 3)",** placed right after the landing ledger's block. It holds two rows:
  - `deploy-watcher-bws-token`: class and provider `bws-lane-token`, keeper `660539eb-…`,
    `rotated_by_sds = true`, and `rotate_requested = "2026-11-10"`. Its `reach_secrets` are the
    four ids in check 1, and `reach_projects = []`. Consumers: the keeper (`bws-secret`), and
    `coolify-env` with `instance = "prod"`, `resource_type = "service"`,
    `uuid = "p12q9n6xcy5u8eib1m4gmvar"`, `key = "DEPLOY_WATCHER_BWS_ACCESS_TOKEN"`, and
    `redeploy = false`.
  - `deploy-watcher-ping-url`: class and provider `healthchecks-ping-url`, keeper `4f7f3d8b-…`,
    `rotated_by_sds = true`, and `healthchecks_check = "sds-deploy-watcher"`, with the keeper as
    its one consumer. It has no `rotate_requested`.
  - Neither row carries `created`. The block's comment describes this lane as it stands: the
    token is due, and a later change dates the ping URL. I left out the ledger's "never waited on
    a mint at once" rationale, because amendment 23 withdrew it (see deviations).
- **`lanes-read-app-private-key`:** a second `runtime-fetch` consumer, `repo = "orchestrator"`,
  `ref = "scripts/run-deploy-watcher.sh"`, `healthchecks_check = "sds-deploy-watcher"`. Its note
  says the key is read in the `sds-lane-deploy-watcher` container with the lane's own token, each
  pass, and piped into `sds-github-mint`.
  - The block comment now says both the landing-ledger and deploy-watcher accounts hold read
    grants on the read App's keeper (G-18 and G-28).
  - I confirmed the ref from `lanes/deploy-watcher/crontab` (`lane-run deploy-watcher`),
    `scripts/lane_run.py:83` (`LAUNCHER_DIR / f"run-{lane}.sh"`), and
    `scripts/run-deploy-watcher.sh:59,191-192`. Those lines read `READ_APP_KEY_UUID` with
    `sds_bws_value` and pipe it into `sds-github-mint`.
- **`.bws-secrets.toml`, a comment-only fix (lines 294-296).** The comment said `deploy-watcher`
  would get its grant on the read App key at cut-over. It now says the grant exists (G-28). No
  entry or `lanes` value changed.
- **`tests/rotation_worker/test_deploy_watcher_rows.py`,** 12 tests modelled on
  `test_landing_ledger_rows.py` as #644 shaped it (see check 2).
- **`tests/scripts/test_deploy_lanes.py`:** I renamed
  `test_the_repositorys_registry_registers_the_two_moved_lanes` to `…_three_moved_lanes`. It now
  expects `{"deploy-watcher", "execution-watch", "landing-ledger"}`.

I left the observer row's Mac `runtime-fetch` entries for `run-deploy-watcher.sh` (lines 83-85
and 365-367) unchanged. No code or test requires them to change.

## Checks

### Check 1: `reach_secrets`

The `.bws-secrets.toml` entries whose `lanes` include `deploy-watcher`:

- `f793576f-e9aa-4f9d-8089-b4a000b9e2d5`, `orchestrator-observer` (lines 108-111).
- `3b9503da-eb7e-401d-b4a7-b4a400c07efb`, `change-manager/M2M_TOKEN_OBSERVE` (lines 172-175).
- `314a966c-5654-4e3c-8358-b4fd01268775`, `lanes-read-app-private-key` (lines 297-300).

Add the ping-URL keeper, `4f7f3d8b-a83e-4ae5-a665-b4ff0004b875` (`deploy-watcher-ping-url`). That
gives four ids, which is exactly the G-28 grant set. The ping-URL keeper has no `.bws-secrets.toml`
entry yet. The test therefore asserts `reach == lane_secrets | {ping_keeper} == GRANTED`, as #644's
version did for the ledger. HEAD's ledger test asserts `ping_keeper in lane_secrets`, but this
lane's manifest can't satisfy that yet.

### Check 2: the rows, parsed through the code

`tests/rotation_worker/test_deploy_watcher_rows.py` reads this repository's registry at `main`
through `RepoMirror` and `rotation_worker.registry.rotated_by_sds`, as the executor does. Its 12
tests cover the following:

- Both rows are handed to the SDS.
- `bws_lane_token.check_row` passes, and `lane_variable` returns the service uuid and
  `DEPLOY_WATCHER_BWS_ACCESS_TOKEN`. The compose file maps that variable to `BWS_ACCESS_TOKEN`.
- `declared_reach` returns no projects and the four granted secrets.
- **The no-two-rows-share-a-reach rule,** parametrized over `execution-watch-bws-token` and
  `landing-ledger-bws-token`:
  - Each other row's reach intersects this one: the observer bearer for both, and the read App
    key for the ledger.
  - `sharing_reach` still returns `[]`.
  - A twin given the other row's whole reach set returns `[other]`, so the rule compares whole
    sets.
- `healthchecks_ping_url.check_row` passes, and `healthchecks_check == "sds-deploy-watcher"`.
- Only the token row is due. Neither row has `created` or `last_rotated`.
- `plan_for(..., first_issue=True)` derives an issue plan for both rows.
- The read App key's reader for `scripts/run-deploy-watcher.sh` is `runtime-fetch`, in repo
  `orchestrator`, pinging `sds-deploy-watcher`. The script names the key's id.

Mutation checks, run after committing (`cfc8d977`). Before each mutation I copied the registry to
`/tmp/w83-mut/orig.toml` and restored it with `cp` afterwards. Each mutation failed exactly one
test of the 12:

| Mutation | Failing test |
|---|---|
| `redeploy = true` on the token's `coolify-env` | `test_the_token_row_is_one_the_lane_token_adapter_acts_on` |
| `3b9503da-…` dropped from `reach_secrets` | `test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper` |
| `rotate_requested = "2026-11-10"` added to the ping-URL row | `test_only_the_token_row_is_due` |
| The reader's `healthchecks_check` set to `sds-landing-ledger` | `test_the_read_app_key_is_read_by_the_lane_s_launcher` |

After the restores, `/usr/bin/git status --short` printed nothing.

### Check 3: due-ness

From infraops `src/security-drift/cred-rotation.ts` (checkout `c3359bf`):

- `bws-lane-token` has `maxAgeDays: 365` (line 95). With no `created`, `rotationAnchor` (lines
  209-210) finds no anchor, so the token row gets no age finding.
- `healthchecks-ping-url` has `maxAgeDays: Number.POSITIVE_INFINITY` (lines 97-98), so it's never
  due by age.
- `cred.rotation-requested` (lines 279-290) fires when `now >= Date.parse(rotate_requested)` and
  nothing has rotated since. A row with no `rotate_requested` gives `NaN`, so the comparison is
  false.

To run `cred-findings`, I made a scratch copy of the checkout with `rsync`, excluding `.git`, at
`/tmp/w83-infraops.6Jmj`. Its built `dist` already knows both classes, so I didn't build. I set
`INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` to a scratch config. That config listed only
this worktree's `.cred-consumers.toml` and held an empty mode-0600 state file. Then I ran
`node dist/cli/security-drift-cli.js cred-findings --now 2026-11-11T03:00:00Z`:

- **This branch's registry** exits 0 with 6 findings. The deploy-watcher findings are only
  `{"check": "cred.rotation-requested", "id": "deploy-watcher-bws-token", "class": "bws-lane-token",
  "rotate_requested": "2026-11-10", "rotated_by_sds": true}`. No finding names
  `deploy-watcher-ping-url`. `last_rotated` is `null` for both rows. The other five findings are
  the drift-reporter, execution-watch, and landing-ledger rows. They show only because the scratch
  state is empty.
- **Control:** a scratch copy with `rotate_requested = "2026-11-10"` on the ping-URL row reports
  both `deploy-watcher-bws-token` and `deploy-watcher-ping-url`.
- `git status` in `~/Projects/infraops-mcp-server` printed nothing. I didn't edit or build that
  checkout, and I didn't use `~/.config/infra-drift/cred-consumers.list` to run anything. I read
  it only to cite check 5.

**Both rows could have been due at once.** ADR-0055 amendment 23, item 3 says: "the ping URL's
unit no longer waits on a person's mint, so `other_mints` … never lists it, and the order that
kept the two units from waiting together is no longer needed. HQ still makes the row due, and may
make both rows due in one change." The code agrees:

- `rotation_worker/types.py:57-59` gives `healthchecks-ping-url` `machine_mint=True`.
- The `inbox.py` docstring says "A ping URL is never pasted".

As the handoff instructs, I still didn't date the ping-URL row.

### Check 4: fixtures and existing tests

I grepped `src`, `scripts`, and `tests` for `lanes_in_registry`, `registered_lanes`,
`lanes-read-app-private-key`, the lane token ids, `LANDING_LEDGER_BWS_ACCESS_TOKEN`,
`bws-lane-token`, `healthchecks-ping-url`, and `cred-consumers`. Then I read the bodies of the
matches.

- **`tests/scripts/test_deploy_lanes.py:843-847`** reads the real registry through
  `deploy.lanes_in_registry`. The new `coolify-env` consumer matches `LANE_TOKEN_KEY` and is in
  `LANE_CHECKS` (`scripts/deploy_orchestrator.py:955`). I updated this test, as described in "What
  I built".
- **`registered_lanes`** (`deploy_orchestrator.py:1255-1257`) reads
  `origin/main:.cred-consumers.toml`. Once this merges it returns
  `{"deploy-watcher", "execution-watch", "landing-ledger"}`.
  - The release hold then covers `deploy-watcher` only after its `--add` passes
    (`require_lanes_current`, 1265-1291, which skips a lane with no finished run).
  - The other `registered_lanes` assertions (lines 786, 800, and 819) use the `lanes` fixture's
    synthetic registry, so they don't change.
- **`lane_secrets`** (1226) reads `.bws-secrets.toml`, not the registry. This change adds no
  manifest entry, so its result doesn't change.
- **`tests/rotation_worker/test_execution_watch_rows.py`** and
  **`test_landing_ledger_rows.py`** call `sharing_reach` over every lane row. Both still pass,
  because the deploy watcher's reach set differs from each.
- **`tests/rotation_worker/test_registry_array_strings.py`** passes, because no new array string
  holds a comma.
- **`tests/scripts/test_run_deploy_watcher.py:29`** pins the key's uuid in the launcher, not in
  the registry.
- The other matches use synthetic registries or fixtures (`test_lane_credentials.py`,
  `test_registry_and_compare.py`, `test_deploy_orchestrator.py:109`, and the
  `rotation_package_issue_*.json` fixtures). No agreement fixture pins the lane rows or the App key
  rows.

### Check 5: how the rows go live

- **The 03:00 scan and `cred-findings`** read the files listed in
  `~/.config/infra-drift/cred-consumers.list` (infraops `src/security-drift/paths.ts:46`, and
  `src/cli/security-drift-cli.ts:119,171-175`). The orchestrator entry is
  `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main tree's working copy.
- **The rotation proposer** gets what's due only from that `cred-findings` answer
  (`scripts/run-rotation-proposer.sh:7-12`, "Neither file is opened by this program").
- **The main tree moves only by fast-forward,** through each lane launcher's activation helper
  (orchestrator ADR-0031). For example, `scripts/run-deploy-watcher.sh:61-81` sources
  `~/.claude/bin/activate-checkout.sh` and calls `activate_checkout "$REPO_ROOT"` on the Mac.
- **The executor** reads the rows at GitHub `main` through `RepoMirror`
  (`rotation_worker/registry.py:136`). It acts only on a unit the proposer created, so due-ness
  starts nothing until the main tree's working copy holds this change.

## Tests and checks run

- **Targeted tests** (the six row and lane test files): 142 passed.
- **`make check`** against `orchestrator_test_hq_w_83`, with `SECURITY_STANDARDS_DIR` set to the
  fixture and `PGTZ=UTC`: exit 0. ruff check and format (1039 files) passed, pyright reported 0
  errors, and pytest reported 14317 passed, 1 skipped, 11 warnings in 302.91 s.
- **Security scan from the repo root:** exit 0 and no BLOCK finding. It reported two WARNs, and
  both are already on `origin/main`:
  - `bws.secret-files-gitignored`: `*-migration/`.
  - `bws.manifest-matches-usage`: `aaff45ca-…`, an id in older reports.
  The scan also reported one judgment INFO.
- **`/code-review` (low) on `origin/main...HEAD`:** no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_deploy_watcher_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-3-deploy-watcher-rows-report.md` (this report)

## Self-review

- **The test follows #644's reach and due-ness shape, not HEAD's.** The ping keeper has no
  manifest entry yet, and the ping-URL row is undated.
- **The block comment avoids the ledger's mint-ordering rationale.** Amendment 23 withdrew it, and
  a comment stating it would be false for this lane.
- **The `.bws-secrets.toml` edit touches a comment only.** It keeps that file true about the G-28
  grant. The handoff asked that comments stay true, and this comment is the same fact the App-keys
  block states.

## Deviations

- **One comment-only edit outside `.cred-consumers.toml`,** in `.bws-secrets.toml` lines 294-296.
  It isn't named in the handoff. It corrects a sentence that the G-28 grant made false.
- **The block comment doesn't copy the ledger's "never waited on a mint at once" sentence.**
  Amendment 23 says that order is no longer needed (check 3).

## Follow-ups

- A one-line change dates `deploy-watcher-ping-url` due. Amendment 23 allows it at any time; it
  doesn't need to wait for the token's `store` unit.
- `.bws-secrets.toml` has no entry for the ping-URL keeper `4f7f3d8b-…` with
  `lanes = ["deploy-watcher"]`, and `lanes/deploy-watcher/compose.yaml` still sets
  `SDS_DEADMAN_PING_URL_UUID: ""`. The compose comment names a later change that writes the id,
  as the ledger's lane got. Once that change lands, this test's reach assertion can match HEAD's
  ledger form (`ping_keeper in lane_secrets`).
- change-manager's `M2M_TOKEN_OBSERVE` row is the separate change HQ plans.

## Concerns

None beyond the follow-ups. Logging, error handling, and secrets handling are covered by the
standards in the next section, and the change touches no concern the handoff's standards leave
out.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** the change has no logging
  change. It edits registry rows, comments, and tests only.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing. Each
  adapter refusal propagates as a test failure, which the mutation checks show.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, a ping URL's
  UUID, or the App key. The rows and tests name BWS secret ids and a Coolify resource uuid only.
  The `cred-findings` output quoted here carries ids, classes, and dates only.
- **Infra Brain 591, 592, and 593 (no BWS token in tracked files or history, bootstrap token never
  inline):** the diff holds no token-shaped value, and the security scan reports no BLOCK. The test
  uses the registry's real ids, which are secret identifiers, not values.
- **Infra Brain 2890 (never commit a ping URL):** the diff holds no
  `https://healthcheck.alobar.net/ping/` string. The ping-URL row names its check by name and its
  keeper by BWS id.
- **Infra Brain 1032 (`cred.exposure-rotate`):** neither row declares `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable, because the change makes no domain
  change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a test, a
  log, or this report. The rows name ids only. The scratch state file held no values.
