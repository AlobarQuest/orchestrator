Outcome: done

# Report: the pin watcher's registry rows (ADR-0059 increment 3, cut-over step 1)

Worker W-105, branch `hq/lanes-inc-3-pin-watcher-rows`, base `origin/main` at `7ddc3bfb` (the
handoff's base; every fact the handoff cites matched there). PR: PR_PLACEHOLDER.

## What I built

Change 1 of the two changes (ADR-0055 amendment 19 item 8 step 2; ADR-0059 "Cut-over for one lane"
step 1) for the `pin-watcher` lane, modelled on the unit verifier's block and PR #663 (`4bc30f17`).

- `.cred-consumers.toml` gains the block "The pin-watcher lane's token and ping URL (ADR-0059
  increment 3)" directly after the unit verifier's and before "The two lane GitHub Apps' private
  keys". It holds two rows:
  - `pin-watcher-bws-token`: class and provider `bws-lane-token`, `bws_uuid`
    `824605ad-d05b-4bd0-b9b1-b50000e8886e`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-12"`, the four-id `reach_secrets` from check 1, and
    `reach_projects = []`. Consumers: its keeper (`bws-secret`, same uuid) and `coolify-env`
    (`instance = "prod"`, `resource_type = "service"`, `uuid = "athv39w06xtmvqtxnjs3m8d0"`,
    `key = "PIN_WATCHER_BWS_ACCESS_TOKEN"`, `redeploy = false`).
  - `pin-watcher-ping-url`: class and provider `healthchecks-ping-url`, `bws_uuid`
    `872684c0-899e-43b7-9c2d-b50000e88b21`, `rotated_by_sds = true`,
    `healthchecks_check = "sds-pin-watcher"`, and its keeper consumer. No `rotate_requested`.
  - Neither row carries `created`. The block comment states the lane's state (token due, ping URL
    dated by a later change) and names G-37 for both keepers.
- The `lanes-read-app-private-key` block comment says the pin-watcher account holds a read grant on
  the read App's keeper (G-37), replacing "the pin-watcher's reader lands with its launcher, ahead
  of its account's grant at cut-over". The existing container `runtime-fetch` reader for
  `scripts/run-pin-watcher.sh` is unchanged; I added no second one.
- `.bws-secrets.toml` (the `lanes-read-app-private-key` comment) says `pin-watcher` holds its read
  grant (G-37), replacing "`pin-watcher`'s account is granted at its cut-over".
- `tests/rotation_worker/test_pin_watcher_rows.py`, modelled on `test_unit_verifier_rows.py`.
- `tests/scripts/test_deploy_lanes.py`: the registered-lanes test now expects six lanes.

The Mac `runtime-fetch` entries for `run-pin-watcher.sh` on other rows (the observer bearer and the
alert sender) are unchanged.

## Checks

### Check 1: `reach_secrets`, derived

The `.bws-secrets.toml` entries whose `lanes` include `pin-watcher`, at `7ddc3bfb`:

| Id | Name | Manifest lines |
| --- | --- | --- |
| `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` | `orchestrator-observer` | 120-124 |
| `b415d03b-f92d-46b1-8a9d-b4f10103df49` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` | 259-263 |
| `314a966c-5654-4e3c-8358-b4fd01268775` | `lanes-read-app-private-key` | 310-314 |

`grep -n pin-watcher .bws-secrets.toml` finds `lanes` lists only at 124, 263, and 314. Adding the
ping-URL keeper `872684c0-899e-43b7-9c2d-b50000e88b21` gives four ids. That equals HQ's three plus
the keeper, the four grants Devon made at G-37, so I didn't need to ask.
`test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper` re-derives the
set from the manifest and asserts it equals both the row and the G-37 set.

### Check 2: the rows parsed through the code

`tests/rotation_worker/test_pin_watcher_rows.py` (17 tests) commits the repository's
`.cred-consumers.toml` to a test origin and reads it as the executor does, through
`rotation_worker.registry.rotated_by_sds` on a `RepoMirror`. It asserts the following:

- Both rows are handed to the SDS.
- `bws_lane_token.check_row` accepts the token row, and `bws_lane_token.lane_variable` resolves the
  service uuid `athv39w06xtmvqtxnjs3m8d0` and key `PIN_WATCHER_BWS_ACCESS_TOKEN`. The key matches
  `lanes/pin-watcher/compose.yaml:23` (`BWS_ACCESS_TOKEN: ${PIN_WATCHER_BWS_ACCESS_TOKEN}`).
- `redeploy is False`, read from the row's own `coolify-env` consumer, not from the destination
  parser (which reads a missing `redeploy` as false).
- The no-two-rows-share-a-reach rule, parametrized over the five other lane tokens
  (`deploy-watcher`, `execution-watch`, `landing-ledger`, `revision-watcher`, `unit-verifier`). For
  each, the test asserts the two reach sets intersect (this lane shares the observer bearer, the
  alert sender, or the read App key with every one of them), that `sharing_reach` returns `[]` for
  the real row, and that a twin carrying the other lane's whole set returns that lane. This shows
  the rule compares whole sets and passes.
- `healthchecks_ping_url.check_row` accepts the ping-URL row; its check is `sds-pin-watcher`.
- Only the token row is due (`rotate_requested == "2026-11-12"`; the ping-URL row has none).
- Each row's keeper consumer uuid equals its `bws_uuid`.
- Neither row has `created` or `last_rotated`.
- `rotation_proposer.generate.plan_for(..., first_issue=True)` derives an issue plan for each row.
- The read App key's `runtime-fetch` reader for `scripts/run-pin-watcher.sh` exists, pings
  `sds-pin-watcher`, and the launcher names the key's `bws_uuid`.

**Mutation check.** I copied `.cred-consumers.toml` to `/tmp`, applied one mutation, ran the file,
and restored from the copy; `cmp` confirmed the restore. Each mutation failed exactly one test:

| Mutation | Result |
| --- | --- |
| `redeploy = false` to `true` on the token's `coolify-env` | 1 failed, 16 passed |
| Drop the ping-URL keeper from `reach_secrets` | 1 failed, 16 passed |
| Ping-URL keeper consumer uuid differs from `bws_uuid` by one digit | 1 failed, 16 passed |
| Delete the token's `rotate_requested` | 1 failed, 16 passed |

### Check 3: due-ness

From infraops `src/security-drift/cred-rotation.ts` at `c3359bf`:

- `CLASS_POLICY['bws-lane-token']` is `{ maxAgeDays: 365 }` (line 95), and
  `CLASS_POLICY['healthchecks-ping-url']` has `maxAgeDays: Number.POSITIVE_INFINITY` (97-103), so
  the ping URL is never due by age.
- Lines 279-290: a row is `cred.rotation-requested` when `now >= rotate_requested` and it has no
  `last_rotated` (or one earlier than the request). The token row has a request and no anchor, so
  it is due from 2026-11-12. The ping-URL row has no `rotate_requested`, so `requestedMs` is `NaN`,
  the comparison is false, and it yields no finding.

I ran `cred-findings` from a scratch copy (`git archive HEAD` of `~/Projects/infraops-mcp-server`
into `/tmp`, with `node_modules` symlinked; nothing in that checkout was edited or built). It read a
scratch config directory listing only a copy of this branch's `.cred-consumers.toml`, with an empty
rotation state, not `~/.config/infra-drift/cred-consumers.list`. The CLI's `main` runs only when
the file ends in `.js`, so I called `main(['cred-findings', '--now', '2026-11-12T15:00:00Z'])`
through `tsx`. Exit 0. The pin-watcher result, quoted:

```
{'check': 'cred.rotation-requested', 'id': 'pin-watcher-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-12', 'rotated_by_sds': True}
last_rotated: {'pin-watcher-bws-token': None, 'pin-watcher-ping-url': None}
```

No finding names `pin-watcher-ping-url`.

### Check 4: fixtures and existing tests

I grepped `tests/`, `scripts/`, and `src/` for readers of `.cred-consumers.toml`, the lane rows,
and the App key rows, and read each loading body.

- `tests/scripts/test_deploy_lanes.py` `test_the_repositorys_registry_registers_the_five_moved_lanes`
  reads the repository's file through `deploy.lanes_in_registry`. I renamed it to `..._six_...` and
  added `pin-watcher` to the expected set.
- `scripts/deploy_orchestrator.py`: `registered_lanes` (1255-1257) is
  `lanes_in_registry(git show origin/main:.cred-consumers.toml)`. `lanes_in_registry` (1201-1223)
  takes each `coolify-env` consumer on a `service` whose key matches `<LANE>_BWS_ACCESS_TOKEN`, and
  refuses a lane outside `LANE_CHECKS` (`pin-watcher` is at 963). Once this merges,
  `registered_lanes` returns `{"deploy-watcher", "execution-watch", "landing-ledger",
  "pin-watcher", "revision-watcher", "unit-verifier"}`, so the release hold covers the pin watcher
  until its `--add` passes.
- `test_every_lanes_ping_url_secret_is_listed_for_that_lane` (`test_deploy_lanes.py` ~1183) reads
  the lanes' `compose.yaml` files, not the registry. `pin-watcher` stays in its `placeholders` set
  because `lanes/pin-watcher/compose.yaml` still has `SDS_DEADMAN_PING_URL_UUID: ""`. Not changed.
- `test_deploy_watcher_rows.py`, `test_landing_ledger_rows.py`, `test_execution_watch_rows.py`,
  `test_revision_watcher_rows.py`, and `test_unit_verifier_rows.py` each select their own rows by
  id (and, where they check the App key reader, by their own launcher), so a new row doesn't move
  them. Their other-lane lists (`OTHER_LANE_TOKEN` or `OTHER_LANE_TOKENS`) name earlier lanes only;
  #663 didn't backfill those lists, and I followed it.
- The other registry readers (`test_registry_and_compare.py`, `test_registry_array_strings.py`,
  `test_lane_credentials.py`, the `rotation_proposer` and `rotation_decomposer` tests, and the
  `tests/fixtures/rotation_*` files) build their own registries from fixtures; none reads the
  repository's file.

The full suite passed (see Gates), so no other test pins these rows.

### Check 5: how the rows go live

- The 03:00 scan and `cred-findings` read the files listed in
  `~/.config/infra-drift/cred-consumers.list` (infraops `src/security-drift/paths.ts:46`,
  `src/cli/security-drift-cli.ts:171-177`). Line 4 of that list is
  `/Users/devon/Projects/orchestrator/.cred-consumers.toml`, the main tree's working copy. I read
  the list only to cite this.
- The rotation proposer gets what's due only from `cred-findings`
  (`scripts/run-rotation-proposer.sh:7-12`: "Neither file is opened by this program").
- The main tree reaches `origin/main` through each Mac launcher's activation helper (ADR-0031
  decision 1, `docs/decisions/0031-a-merged-change-is-not-activated-until-the-consumer-pulls.md:35-39`:
  "Each scheduled launcher fast-forwards the working copy whose code it is about to run, as its
  first step"). For example, `scripts/run-pin-watcher.sh:61-68` sources
  `~/.claude/bin/activate-checkout.sh`, whose `activate_checkout` runs
  `git merge --ff-only '@{u}'` (line 93). After the merge, the next activating Mac launcher pass
  brings the rows into the working copy the scan and the proposer read.

## Gates

- `make check`, with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` at
  `orchestrator_test_hq_w_105`, `SECURITY_STANDARDS_DIR` at the fixture, and `PGTZ=UTC`:
  exit 0 on `7cbd0e78`. Lint and format passed (`1056 files already formatted`), pyright reported
  `0 errors`, and the test run gave `14644 passed, 1 skipped, 11 warnings in 334.93s`.
- Targeted run before it: `test_pin_watcher_rows.py`, `test_unit_verifier_rows.py`, and
  `test_deploy_lanes.py` gave `123 passed`.
- Security scan from the repo root: exit 0, `BLOCK: 0`, `WARN: 2`, `INFO: 1`. Both WARNs predate
  this change: `bws.secret-files-gitignored` (glob `*-migration/` not covered in `.gitignore`) and
  `bws.manifest-matches-usage` (undeclared `aaff45ca-…`). Neither names an id this change adds.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `.bws-secrets.toml` (comment only)
- `tests/rotation_worker/test_pin_watcher_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-3-pin-watcher-rows-report.md` (this report)

## Self-review

The diff mirrors #663 line for line, with the pin watcher's ids, service, key, check, gate, and
date. The new test is the unit verifier's with the lane's constants, the five other lane tokens,
and a docstring naming what this lane shares. The mutation check shows the test catches a changed
`redeploy`, a reach set missing the keeper, a mismatched keeper uuid, and a missing request date.

## Deviations

None from the handoff. One method note: infraops' CLI runs `main` only when invoked as a `.js`
file, so I ran it in-process through `tsx` from the scratch copy rather than building `dist`.

## Concerns and follow-ups

- The ping-URL row needs its `rotate_requested` in a later change, as the unit verifier's does.
- `lanes/pin-watcher/compose.yaml` keeps `SDS_DEADMAN_PING_URL_UUID: ""` until the lane's ping-URL
  id is written; `test_every_lanes_ping_url_secret_is_listed_for_that_lane` then needs
  `pin-watcher` removed from `placeholders`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** no logging change; the diff
  adds registry rows, comments, and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches nothing; every
  failure surfaces as an assertion or an exception.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, the UUID
  inside a ping URL, or the App key. The rows and the test name BWS secret ids only, which aren't
  ping-URL UUIDs.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; bootstrap token never
  inline; placeholders don't match the token shape):** the diff holds no token and no token-shaped
  value; the scan reports `BLOCK: 0`.
- **Infra Brain 2890 (never commit a ping URL):** `grep -c "healthcheck.alobar.net/ping"` is 0 in
  both changed files that hold ids.
- **Infra Brain 1032 (`cred.exposure-rotate`):** neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the test,
  or this report; the rows name ids only.
