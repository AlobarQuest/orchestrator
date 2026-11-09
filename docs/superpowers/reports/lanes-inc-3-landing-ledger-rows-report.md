Outcome: done

# Report: the landing ledger's registry rows (ADR-0059 increment 3, cut-over step 1)

- Worker: W-63
- Branch: `hq/lanes-inc-3-landing-ledger-rows`, based on `55579cb1`.
- PR: #644, open and marked ready, not merged.
- Code head: `f2f2d43d` (the report and all code are in this commit or earlier).

## Concerns, most urgent first

1. **`origin/main`'s registry is unreadable by infraops, so tonight's 03:00 scan and the 06:30
   proposer pass are already broken.** The two lane App key rows from #642 (`7d8a1140`) each have a
   comma inside their first `rotation_preconditions` string, for example `"rotate it with
   lanes-push-app-private-key, in one sitting"`. infraops' reader (`parseValue`,
   `src/security-drift/cred-consumers.ts` ~124-131) splits an array on every `,` before it checks
   for quotes.
   - `cred-findings` run on `git show origin/main:.cred-consumers.toml` exits 1 with
     `line 748: arrays may contain only quoted strings`.
   - The main tree activated `7d8a1140` at 2026-11-09 14:34 -0500 (main-tree reflog). The proposer
     last ran at 10:51 that day, before activation, so no pass has hit the error yet.
   - Effect on the 03:00 scan: `credScan` (`src/cli/security-drift-cli.ts` 130-157) catches the
     error and posts one FAIL, `cred.registry-error`, in place of every credential finding from
     all four registries.
   - Effect on the rotation proposer: `cred-findings` throws, so the proposer exits 2 ("an
     unparseable registry", `scripts/run-rotation-proposer.sh` header). That pass proposes nothing
     for any credential, including execution-watch's.
   - I fixed it in this PR (a separate commit, `84e750ff`) by removing the comma from both strings.
     I also added `tests/rotation_worker/test_registry_array_strings.py`, which fails on
     `origin/main`'s text. It flags `credential[12]` and `credential[13]`, which are the two App
     key rows.
   - Merging #644 alone doesn't fix the scan. The scan reads the main tree's working copy, so the
     fix takes effect at the first activation pass after the merge (check 5). Merging and
     activating before 03:00 avoids one failed scan. The infraops reader's split isn't
     quote-aware, and that belongs to infraops; I didn't change it.
2. **`other_mints` is estate-wide, and its refusal is mutual.**
   - `other_mints` (`rotation_worker/inbox.py` 111-126) lists every active rotation unit, from any
     other revision, that has never been claimed and comes straight after a person's mint.
   - `bind` refuses while that list isn't empty, and `worker.py:685` passes the list on every bind.
   - Each waiting unit appears in the other's list, so two issues waiting on mints at once refuse
     both binds.
   - The proposer log (10:51) shows `execution-watch-ping-url` proposed as item 190, approved.
     Suppose that issue still waits on Devon's paste when the landing-ledger token's issue unit
     reaches its mint. Then neither binds until one of them is cancelled.
   - Ordering is HQ's call. The diff doesn't depend on it.
3. **The `.bws-secrets.toml` comment is stale.** The comment on `lanes-read-app-private-key`
   (~281) still says "no grant exists until the lane's cut-over", but G-18 granted it. The
   manifest also doesn't list `ebabdd63-…`, and `lanes/landing-ledger/compose.yaml` still has
   `SDS_DEADMAN_PING_URL_UUID: ""`. The handoff assigns both to the later
   `SDS_DEADMAN_PING_URL_UUID` change, so I left them. That change also has to add `ebabdd63` to
   `.bws-secrets.toml` with `lanes = ["landing-ledger"]`. After that, `deploy_orchestrator`'s
   `lane_secrets` (1230, used at 1526 by `--add`) reads it.
4. **Check 5's premise doesn't hold as worded.** Other readers fetch the registry themselves (see
   check 5). None of them starts a rotation, so this changes nothing about the rows.
5. **The worktree scan shows two WARNs that a clean archive doesn't.** See "Tests and checks".

## What I built

- **`.cred-consumers.toml`, the block "The landing-ledger lane's token and ping URL (ADR-0059
  increment 3)"**, at lines 728-776:
  - `landing-ledger-bws-token` (738): class and provider `bws-lane-token`,
    `bws_uuid = "d7813bd9-0d9d-4781-ae86-b4fd017539ff"`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-09"`. `reach_secrets` is the three ids from check 1, and
    `reach_projects = []`.
    - Its keeper consumer is `bws-secret` (the executor alone reads it).
    - Its `coolify-env` consumer has `instance = "prod"`, `resource_type = "service"`,
      `uuid = "gs1z8orebdnq9ug574sia6fs"`, `key = "LANDING_LEDGER_BWS_ACCESS_TOKEN"`, and
      `redeploy = false`.
  - `landing-ledger-ping-url` (766): class and provider `healthchecks-ping-url`,
    `bws_uuid = "ebabdd63-2eff-4b8e-99b7-b4fd01754eae"`, `rotated_by_sds = true`, no
    `rotate_requested`. Its keeper is its only consumer.
  - Neither row has `created`.
  - The block comment describes the lane's state now: the token is due, and a later change dates
    the ping URL after the token's `store` unit completes. It also says why there's no `created`.
    The sentence "the VM root's `create-keeper` made both keepers … each holding the inbox's empty
    marker" comes from `state/goal.md` 923 (Devon ran both `create-keeper` commands on G-18) and
    `docs/operations/rotation-executor.md` ~1255 (a keeper holds the inbox's empty marker).
- **`lanes-read-app-private-key`** (791): I added a `runtime-fetch` consumer (806-811) with
  `repo = "orchestrator"`, `ref = "scripts/run-landing-ledger.sh"`, and
  `healthchecks_check = "sds-landing-ledger"`. Its note says it's read in the
  `sds-lane-landing-ledger` container with the lane's own token and piped into `sds-github-mint`.
  - The block comment now says that the landing-ledger lane's account holds a grant (G-18), and
    that the push App's keeper still has no reader.
  - I removed "no reader holds a grant yet" from the read App keeper's note and kept it on the push
    App's keeper.
  - The fields follow the existing `runtime-fetch` rows. The code requires only `repo` and `ref`
    (`rotation_worker/destinations.py:47`). infraops parsed the row without complaint (check 3).
  - To confirm the `ref`, I traced the container's path. The crontab runs
    `lane-run landing-ledger`, and `scripts/lane_run.py:83` runs `run-landing-ledger.sh`. That
    script reads `READ_APP_KEY_UUID` (41) and pipes it into `sds-github-mint` (154-158).
- **The precondition comma fix**, described in concern 1.
- **The observer row's Mac `runtime-fetch` entries** (`.cred-consumers.toml` 118 and 393) are
  unchanged. No code or test asks for a change.

## Checks 1-5

### Check 1: `reach_secrets`

The `.bws-secrets.toml` entries whose `lanes` include `landing-ledger`:

- `f793576f-e9aa-4f9d-8089-b4a000b9e2d5`: `orchestrator-observer` (108-111,
  `lanes = ["execution-watch", "landing-ledger"]`).
- `314a966c-5654-4e3c-8358-b4fd01268775`: `lanes-read-app-private-key` (283-286).

The ping URL's keeper adds `ebabdd63-2eff-4b8e-99b7-b4fd01754eae` (`landing-ledger-ping-url`).
That makes three ids, the same set as G-18's three direct grants (`state/goal.md` 924), so there
was nothing to ask. The test pins the set both ways: as the manifest set plus the ping keeper, and
literally as `GRANTED`.

### Check 2: rows parsed through the code

`tests/rotation_worker/test_landing_ledger_rows.py` has 11 tests. It reads this repository's file
at `main` through `RepoMirror` and `rotation_worker.registry.rotated_by_sds`, as the execution-watch
test does. It covers the following:

- Both rows are handed to the SDS.
- `bws_lane_token.check_row` passes, and `lane_variable` returns the service uuid and key.
- `declared_reach` equals the manifest secrets plus the ping keeper, which is `GRANTED`.
- **The shared-reach rule.** The two lanes' reaches intersect (on `orchestrator-observer`), and
  `sharing_reach(...)` for the token row is `[]`. A negative control copies the row with
  execution-watch's reach, and `sharing_reach` then returns `["execution-watch-bws-token"]`. This
  shows the rule compares whole reach sets (`bws_lane_token.py` 108-122, equality of the
  `(projects, secrets)` pair).
- `healthchecks_ping_url.check_row` passes on the ping row.
- Only the token row is due, and neither row carries `created` or `last_rotated`.
- `plan_for(..., first_issue=True)` derives an issue plan for both rows.
- The read App key's `runtime-fetch` reader names `scripts/run-landing-ledger.sh` and
  `sds-landing-ledger`, and that script contains the row's `bws_uuid`.

I ran four mutations, each restored by `cp` from a copy and run after clearing `__pycache__`.
Each one failed exactly one test (`1 failed, 10 passed`):

1. `redeploy = true` on the lane's `coolify-env` consumer.
2. The ping keeper in `reach_secrets` swapped for execution-watch's ping keeper.
3. `rotate_requested = "2026-11-09"` added to the ping row.
4. `healthchecks_check` on the new reader changed to `sds-execution-watch`.

`git status` was clean after the restores.

### Check 3: due-ness

From infraops `src/security-drift/cred-rotation.ts` (checkout at `c3359bf`):

- `credFindings` (220-308) emits `cred.rotation-requested` when `now >= rotate_requested` and
  neither `state.lastRotated[id]` nor `last_rotated` is later.
- A row with no `rotate_requested` gives `NaN`, so that test is false.
- `healthchecks-ping-url` has `maxAgeDays: Infinity` (~96-101), and `rotationAnchor` (209-210)
  finds no anchor, so the row gets no age finding.
- `bws-lane-token` has `maxAgeDays: 365`, with no anchor while `created` is absent.

I ran `cred-findings` from a scratch copy of the checkout (`rsync` to `/tmp/w63-infraops.Lgey`).
Its built `dist` already knows `bws-lane-token`, so I didn't build. `INFRADRIFT_CONFIG_DIR`
pointed at a scratch config with my own list and an empty mode-0600 state file
(`{"resolvedExposures":{},"lastRotated":{}}`), and I passed `--now 2026-11-10T03:00:00Z`:

- **This branch's registry alone** exits 0. The landing-ledger findings are
  `{"check": "cred.rotation-requested", "id": "landing-ledger-bws-token", "class": "bws-lane-token",
  "rotate_requested": "2026-11-09", "rotated_by_sds": true}` and nothing for
  `landing-ledger-ping-url`. `last_rotated` is `null` for both rows. The execution-watch rows also
  show as due, but only because the scratch state is empty.
- **Control: ping row dated.** A scratch copy with `rotate_requested = "2026-11-09"` added to the
  ping row reports both `landing-ledger-bws-token` and `landing-ledger-ping-url`.
- **Control: `origin/main`'s registry** exits 1 with `line 748: arrays may contain only quoted
  strings` (concern 1).
- **The four real registries,** with the main tree's file replaced by this branch's, exit 0 with 9
  findings. The only landing-ledger finding is `landing-ledger-bws-token`.
- The rotation proposer uses this same checkout:
  `ROTATION_PROPOSER_INFRAOPS_CHECKOUT = /Users/devon/Projects/infraops-mcp-server`
  (`~/Library/LaunchAgents/com.devon.rotation-proposer.plist` 67-68).
- I didn't use `~/.config/infra-drift/cred-consumers.list` to run anything. I read it only to learn
  which four files the scan lists.

### Check 4: fixtures and existing tests

I grepped `tests`, `scripts`, `src`, and `docs/operations` for `lanes_in_registry`,
`lanes-read-app`, `314a966c`, `reach_secrets`, `svkt4ux8o16dnl7n3k6qcd99`, `5a21575b`,
`in one sitting`, and every test that reads the real `.cred-consumers.toml`.

- `tests/scripts/test_deploy_lanes.py:811` reads the real registry through
  `deploy.lanes_in_registry`. With the new `coolify-env` consumer it returns `landing-ledger` too
  (`landing-ledger` is in `LANE_CHECKS`, `scripts/deploy_orchestrator.py:965`). I renamed the test
  `test_the_repositorys_registry_registers_the_two_moved_lanes` and changed it to expect
  `{"execution-watch", "landing-ledger"}`.
- `tests/unit_verifier/test_unit_verifier_launcher.py:38` reads the bootstrap-token row only, so
  this change doesn't touch it.
- `tests/rotation_worker/test_execution_watch_rows.py` calls `sharing_reach` over all lane rows,
  and it still passes because the reaches differ.
- The other matches use synthetic registries (`test_lane_credentials.py`,
  `test_registry_and_compare.py`, and the fixtures `rotation_package_issue_*.json`), so they don't
  read these rows.
- `tests/scripts/test_run_landing_ledger.py` pins the key uuid in the launcher, not in the
  registry.
- No agreement fixture pins the App key rows.

### Check 5: how the rows go live

These readers use the main tree's working copy:

- The 03:00 scan and `cred-findings` read the files in `~/.config/infra-drift/cred-consumers.list`.
  The orchestrator entry is `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main
  tree's working copy (`src/security-drift/paths.ts:46`, and `doCredFindings` at
  `security-drift-cli.ts` 164-176).
- The rotation proposer gets what's due only from `cred-findings` (`scripts/run-rotation-proposer.sh`
  7-12, "Neither file is opened by this program").
- The main tree moves only by fast-forward. Each lane launcher's activation helper calls
  `activate_checkout "$REPO_ROOT"` (for example `scripts/run-landing-ledger.sh` 45-60, ADR-0031),
  and the three most recent main-tree reflog entries are each `merge @{u}: Fast-forward`.

The handoff's "nothing else fetches it" isn't true as written. These readers fetch the registry
themselves:

- The executor (`rotation_worker.registry.rotated_by_sds`, `registry.py:132`) reads it at GitHub
  `main`, using `RepoMirror.sync`, which runs `git fetch origin +refs/heads/main`
  (`git_mirror.py:73`).
- `deploy_orchestrator.registered_lanes` reads `origin/main:.cred-consumers.toml`
  (`deploy_orchestrator.py:1261`).
- `membership_check` checks whether each member's default branch holds the file
  (`membership_check/declaration.py:277`).

None of these starts a rotation. The executor acts only on a unit the proposer created from a
`cred-findings` answer. The deploy hold covers a registered lane only after its `--add` has passed
(`require_lanes_current`, 1269-1273). So due-ness reaches the SDS only through the main tree's
working copy after activation, as the handoff intends.

## Tests and checks run

- **`make check`** with `TEST_DATABASE_URL` on `orchestrator_test_hq_w_63`, on the clean
  committed tree at `f2f2d43d` (re-run after the last test edit): ruff check and format are clean (1036 files), pyright reports
  0 errors, and pytest reports **14207 passed, 1 skipped**, 11 warnings, in 320s, exit 0.
- **Targeted run** before committing: 112 passed (the new test, the execution-watch rows,
  `test_deploy_lanes.py`, and `test_unit_verifier_launcher.py`).
- **Security scan from the worktree root:** exit 0 with 0 BLOCK and 2 WARN.
  - `bws.secret-files-gitignored`: `glob not covered: *-migration/` in `.gitignore`.
  - `bws.manifest-matches-usage`: `aaff45ca-…` in `scripts/install-rotation-worker-launchd.sh`.
  - My diff touches neither file. `git archive` trees of both `origin/main` and `HEAD`, scanned
    the same way, report 0 BLOCK and 0 WARN (1 INFO). The two WARNs come from worktree context,
    not from this change.
- **`/code-review` (low) on `origin/main...HEAD`:** it returned an empty result, `(none)`, with no
  findings listed.

## Files changed

- `.cred-consumers.toml`: the two rows, the read App key's reader, the App key block comment and
  notes, and the two precondition strings.
- `tests/rotation_worker/test_landing_ledger_rows.py` (new).
- `tests/rotation_worker/test_registry_array_strings.py` (new).
- `tests/scripts/test_deploy_lanes.py`: one test renamed and its expected value updated.
- `docs/superpowers/reports/lanes-inc-3-landing-ledger-rows-report.md` (this report).

## Self-review and deviations

- **One deviation from the handoff:** the precondition fix (concern 1). It's in a block the
  handoff already has me edit, check 3 can't pass without it, and it stays correct if infraops'
  reader is fixed later.
- The execution-watch test checks that the ping keeper is in `_lane_secrets()`. That doesn't carry
  over, because `ebabdd63` isn't in `.bws-secrets.toml` yet. The new test checks the reach against
  the manifest set plus the ping keeper, and against G-18's literal set.
- The regression test asserts only the observed rule: no comma inside an array string. It doesn't
  port infraops' parser.
- I left `.bws-secrets.toml` and `compose.yaml` untouched (concern 3).

## Follow-ups

- infraops: make `parseValue`'s array split quote-aware, so that a comma inside a string isn't a
  parse error.
- The `SDS_DEADMAN_PING_URL_UUID` change: add `ebabdd63` to `.bws-secrets.toml` with
  `lanes = ["landing-ledger"]` and fix the stale "no grant exists" comment.
- The later change that adds `rotate_requested` to `landing-ledger-ping-url` once the token's
  `store` unit completes. That change must also update `test_only_the_token_row_is_due` in
  `tests/rotation_worker/test_landing_ledger_rows.py`, so it's more than one line.
- Scratch to delete: `/tmp/w63-infraops.Lgey` (a copy of the infraops checkout without `.git`,
  which includes its gitignored files), `/tmp/w63-mainscan.*`, and `/tmp/w63-headscan.*`. The
  catastrophic-command deny rule blocked my `rm -rf`.
- Session teardown: worktree, `orchestrator_test_hq_w_63`, and the merged branch.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff adds
  registry rows and tests only.
- **Code Brain rule 4 (never swallow an exception silently):** the new tests catch nothing. Every
  adapter `ContractError` and registry error propagates and fails the test.
- **Code Brain rule 5 (never log secrets):** nothing added prints or holds a token, a ping URL, a
  ping URL's check UUID, or the App key.
  - The rows and tests name BWS secret ids (`d7813bd9-…`, `ebabdd63-…`, `f793576f-…`,
    `314a966c-…`) and a Coolify resource uuid only.
  - A BWS secret id isn't the ping URL's UUID, and nothing here names the latter.
  - The `cred-findings` output I quote carries ids, classes, and dates only.
- **Infra Brain 591, 592, and 593:** no BWS token in tracked files or history. The scan reports
  0 BLOCK, and the only test values are the existing synthetic registry strings, which don't match
  the token shape.
- **Infra Brain 2890:** no ping URL is committed. No `https://healthcheck.alobar.net/ping/` string
  appears in the diff.
- **Infra Brain 1032 (`cred.exposure-rotate`):** neither new row, nor the edited App key rows,
  declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff changes no domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a test, the
  PR, or this report. The rows name ids only. The scratch state file held no values, and the
  `cred-findings` runs read registries only.
