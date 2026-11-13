Outcome: done

# Report: the rotation decomposer's registry rows (W-135)

- **PR:** AlobarQuest/orchestrator#693, ready for review, not merged.
- **Base:** `origin/main` at `261d99bf`.
- **Branch:** `hq/lanes-inc-4-rotation-decomposer-rows`.
- **Code commits:** `58d3b6c0` (rows and tests), then a follow-up that reflows the compose comment
  to 100 columns and tightens this report. The PR's final head is in HQ's closing message.

## What I built

The `rotation-decomposer` lane's two registry rows, in the state #688 left the change proposer's:
the token row is due, and the ping-URL row isn't yet.

In `.cred-consumers.toml`, I added the block "The rotation-decomposer lane's token and ping URL
(ADR-0059 increment 4)" right after the change proposer's block. It has two rows:

- **`rotation-decomposer-bws-token`**
  - Class and provider: `bws-lane-token`.
  - `bws_uuid = "623e61a6-…"`.
  - `rotated_by_sds = true`, `rotate_requested = "2026-11-13"`.
  - `reach_secrets`: the four ids from check 1. `reach_projects = []`.
  - Consumers:
    - Its keeper (`bws-secret`; only the executor reads it).
    - `coolify-env` on prod: service `hcghb8xli5qqo6kl8enuu080`, key
      `ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN`, `redeploy = false`.
- **`rotation-decomposer-ping-url`**
  - Class and provider: `healthchecks-ping-url`.
  - `bws_uuid = "936bbbf2-…"`.
  - `rotated_by_sds = true`, `healthchecks_check = "sds-rotation-decomposer"`.
  - One keeper consumer.
  - No `rotate_requested`.

Neither row carries `created`. The block comment says the token is due and that a later change
dates the ping URL once the token's `store` unit completes. It names G-53 for both keepers and for
the account's four reads.

I updated comments that the rows made false:

- **`lanes/rotation-decomposer/compose.yaml`, lines 13-15:** the comment said the VM root creates
  the ping-URL keeper at cut-over. It now says the keeper was created at HQ gate G-53 and that a
  later change writes its id. The `SDS_DEADMAN_PING_URL_UUID: ""` value is unchanged.
- **`tests/scripts/test_deploy_lanes.py`,
  `test_every_lanes_ping_url_secret_is_listed_for_that_lane` docstring:** "The rotation
  decomposer's keeper is created at its cut-over" became "was created at HQ gate G-53; a later
  change writes its id." `placeholders` is unchanged, because the compose value is still `""`.

The Mac `runtime-fetch` entries for `run-rotation-decomposer.sh` (`.cred-consumers.toml` lines
146-148 and 216) are untouched. No `.bws-secrets.toml` change was needed. That file's comments say
nothing about the account's grants coming later. Its ping-URL keeper entry belongs to the later
compose change (deploy.md step 6), as it did for #688.

## Checks

### 1. `reach_secrets`

I parsed `.bws-secrets.toml` at `261d99bf` for every `[[secret]]` whose `lanes` includes
`rotation-decomposer`. It returned exactly three:

| UUID | Name | Project | Lines |
|---|---|---|---|
| `221a48d5-3f29-4898-b300-b4820140c880` | `ORCHESTRATOR_M2M_CREDENTIALS` | SDS Operator | 22-26 |
| `314f276d-55ca-4ddc-a24d-b4a3013508cd` | `change-manager/M2M_TOKEN_READ` | Rotation / Keepers | 177-181 |
| `b415d03b-f92d-46b1-8a9d-b4f10103df49` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` | SDS Operator | 267-273 |

Adding the ping-URL keeper `936bbbf2-…` gives the four reads granted at G-53. That matches HQ's
set, so no question was needed. The test pins it two ways: `secrets == _lane_secrets() |
{ping_keeper}`, and `secrets == GRANTED`.

### 2. The READ bearer's owning registry (follow-up for HQ)

I read change-manager's local `origin/main` ref, `515c168` (2026-11-13 07:16 UTC). I didn't fetch
or edit that checkout. Its `change-manager-read-bearer` row (`bws_uuid = "314f276d-…"`, lines
67-136) behaves as follows:

- It lists `run-membership-check.sh` only as a Mac `runtime-fetch`. The note describes the launchd
  job and the broad Keychain account. Nothing names the membership-check lane's container as a
  reader.
- It has **no** consumer for `run-rotation-decomposer.sh` at all, for the Mac or the container.
  The orchestrator's `.bws-secrets.toml` (lines 168-176) names run-rotation-decomposer as a reader.

The gap is wider than a missing container reader. change-manager's registry doesn't record the
rotation decomposer as a reader of the READ bearer, and it doesn't record the membership check's
container either. I made no change to change-manager.

### 3. Rows parsed through the code

`tests/rotation_worker/test_rotation_decomposer_rows.py` mirrors `test_change_proposer_rows.py` as
it stood at #688. It runs 19 cases:

| What it checks | How |
|---|---|
| Rows are handed to the SDS | `rotated_by_sds(mirror)` |
| Token row | `bws_lane_token.check_row`, and `lane_variable` gives uuid `hcghb8xli5qqo6kl8enuu080` and key `ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN` |
| `redeploy` | Read from the row: `env["redeploy"] is False` |
| Compose mapping | The compose maps `BWS_ACCESS_TOKEN: ${ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN}` |
| Reach | Equals the manifest's lane secrets plus the ping keeper, and equals `GRANTED` |
| Ping-URL row | `healthchecks_ping_url.check_row`, and the check is `sds-rotation-decomposer` |
| Keepers | Each keeper consumer's uuid equals its row's `bws_uuid` |
| Due-ness | Only the token row has `rotate_requested` |
| Age anchor | Neither row has `created` or `last_rotated` |
| Issue plan | `plan_for(..., first_issue=True)` derives an issue plan for each row |

The no-two-rows-share-a-reach rule runs over all eight other lane-token rows. Five of them share a
secret with this lane:

- `membership-check` shares the READ bearer.
- `execution-watch`, `pin-watcher`, `revision-watcher`, and `unit-verifier` share the `sds-bridge`
  sender.

Three are disjoint: `change-proposer`, `deploy-watcher`, and `landing-ledger`.

For each other row, the test does three things:

1. Asserts the overlap is exactly as listed.
2. Asserts `sharing_reach(...) == []` for this row.
3. Gives a twin row the other lane's whole reach, and asserts `sharing_reach` returns that lane.

So the rule compares whole reach sets, and the real row passes.

I didn't port #688's `test_the_read_app_key_is_read_by_the_lane_s_launcher`. `deploy.md` says this
lane calls no GitHub API, so it has no reader on the read App's key.

**Mutation check.** I copied `.cred-consumers.toml` to `/tmp/w135-ccs.bak` before each mutation and
restored it from that copy afterward.

| Mutation | Result |
|---|---|
| Add `rotate_requested` to the ping-URL row and set `redeploy = true` | 2 failed (`test_the_token_row_is_one_the_lane_token_adapter_acts_on`, `test_only_the_token_row_is_due`), 17 passed |
| Drop `936bbbf2-…` from `reach_secrets` | 1 failed (`test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper`), 18 passed |
| Restored file | `diff -q` against the copy is clean; 19 passed |

### 4. Due-ness

The rule is in infraops `src/security-drift/cred-rotation.ts` at `c3359bf`. I didn't edit or build
that checkout.

- `'bws-lane-token'` has `maxAgeDays: 365` (line 95), so a row with no anchor never ages.
- `'healthchecks-ping-url'` has `maxAgeDays: Number.POSITIVE_INFINITY` (lines 97-102).
- `credFindings` (lines 279-291) raises `cred.rotation-requested` when
  `nowMs >= Date.parse(rotate_requested)` and the credential has never rotated or rotated before
  the request. A row with no `rotate_requested` gives `NaN`, and `nowMs >= NaN` is false, so it
  gets no finding.

I ran `cred-findings` from a scratch export (`git archive HEAD` to `/tmp/w135-infraops`, with
`node_modules` symlinked in and no build). The setup:

- `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at `/tmp/w135-cfg`.
- A list file named only this worktree's `.cred-consumers.toml`.
- An empty rotation state.
- `main(['cred-findings', '--now', '2026-11-13T23:00:00Z'])` was called through `tsx`.
- `~/.config/infra-drift/cred-consumers.list` wasn't used.

Result for this lane:

```
[{'check': 'cred.rotation-requested', 'id': 'rotation-decomposer-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-13', 'rotated_by_sds': True}]
last_rotated: {'rotation-decomposer-bws-token': None, 'rotation-decomposer-ping-url': None}
```

The token row is due, and the ping-URL row has no finding.

### 5. Fixtures and existing tests

- **`test_the_repositorys_registry_registers_the_eight_moved_lanes`:** its body asserts
  `deploy.lanes_in_registry(<repo .cred-consumers.toml>)` equals a fixed set. It's now
  `..._nine_moved_lanes`, with `"rotation-decomposer"` added and the docstring extended.
- **`placeholders`:** its test asserts that the set of lanes whose compose has
  `SDS_DEADMAN_PING_URL_UUID: ""` equals `placeholders`. The compose value is still `""`, so
  `{"change-proposer", "rotation-decomposer"}` stays. Only the docstring changed.
- **Other `lanes_in_registry` and `registered_lanes` tests** (`test_deploy_lanes.py` lines
  786-840) use synthetic fixtures and don't read the repository's file. They're unaffected.
- **`LANES` and `LANE_CHECKS`** (`scripts/deploy_orchestrator.py` lines 951-970) already contain
  `rotation-decomposer`, so `lanes_in_registry` doesn't raise "unknown lane".
- No agreement fixture pins the lane rows. `make check` is green.

**What `registered_lanes` returns once this merges:** nine lanes. They are `change-proposer`,
`deploy-watcher`, `execution-watch`, `landing-ledger`, `membership-check`, `pin-watcher`,
`revision-watcher`, `rotation-decomposer`, and `unit-verifier`.

**Does anything treat the decomposer as registered before its token is issued?** It's in the set,
but nothing acts on that until the lane's `--add` passes. `registered_lanes` has two callers
(`scripts/deploy_orchestrator.py` lines 1255-1300):

- `require_lanes_current` checks `last_finished(load_runs(world, lane))` and skips a lane with no
  finished run. Its docstring says, "A registered lane with no passed `--add` holds nothing and is
  skipped."
- `lanes_to_follow` keeps only lanes with a finished `--add`.

So a release isn't held, and the lane isn't followed, until `--add` passes (deploy.md step 7).

**Interaction with #692** (`hq/lanes-inc-4-change-proposer-ping-uuid`): it's open and not merged
at my base. Both PRs edit the same docstring and the `placeholders` line in
`test_every_lanes_ping_url_secret_is_listed_for_that_lane`. Whichever merges second gets a textual
conflict there. The correct result:

- Drop the change proposer's sentence.
- Keep this PR's G-53 sentence.
- Set `placeholders = {"rotation-decomposer"}`.

The registry block and the nine-lane test don't overlap #692.

### 6. How the rows go live

The 03:00 scan and the rotation proposer both read the main tree's `.cred-consumers.toml`.

- The rotation proposer gets "due" from `security-drift-cli cred-findings`, "which loads the
  listed registries and the rotation state exactly as the 03:00 scan does"
  (`src/rotation_proposer/findings.py` lines 3-16).
- The registry list (`~/.config/infra-drift/cred-consumers.list`) names the main tree's file:
  `grep -c` for the exact line `/Users/devon/Projects/orchestrator/.cred-consumers.toml` returns 1.
  I read nothing else from it.

ADR-0031 §1 says, "Each scheduled launcher fast-forwards the working copy whose code it is about to
run, as its first step." Every orchestrator lane launcher calls
`activate_checkout "$REPO_ROOT" "$0" "$@"`. Examples are `scripts/run-change-proposer.sh:102`,
`run-rotation-decomposer.sh:69`, and `run-unit-verifier.sh:64`. Activation is best-effort
(ADR-0031 §2), so the main tree reaches `origin/main` on the next launcher pass that can
fast-forward. The 03:00 scan and the next rotation-proposer pass read the rows from there.

## Tests and checks

| Check | Result |
|---|---|
| `make check` (`orchestrator_test_hq_w_135`, `PGTZ=UTC`, fixture `SECURITY_STANDARDS_DIR`) | Exit 0. Lint: "All checks passed!" pyright: 0 errors. pytest: **14850 passed, 1 skipped**. My first run failed on one line over 100 characters in the new test; I wrapped it and reran. |
| Targeted run (new test, `test_change_proposer_rows.py`, `test_deploy_lanes.py`) | 128 passed |
| Security scan from the repo root | 0 BLOCK, 2 WARN, 1 INFO. See the following note. |
| `/code-review` (low) on `origin/main...HEAD` | The skill returned an empty result, `(none)`. I record that as no reported findings, not as a confirmed clean review. |

The two scan WARNs aren't from this diff:

- `bws.secret-files-gitignored`: the glob `*-migration/` isn't covered.
- `bws.manifest-matches-usage`: id `aaff45ca-…` is used in code but undeclared.

## Files changed

- `.cred-consumers.toml`: the new block, 49 lines.
- `lanes/rotation-decomposer/compose.yaml`: comment only.
- `tests/scripts/test_deploy_lanes.py`: the nine-lane test, plus the placeholder docstring.
- `tests/rotation_worker/test_rotation_decomposer_rows.py`: new.
- `docs/superpowers/reports/lanes-inc-4-rotation-decomposer-rows-report.md`: this report.

## Self-review and deviations from #688

- **Overlap assertion.** #688's reach-overlap test asserted `mine & theirs` for every other lane.
  Here three lanes are disjoint, so the test asserts the exact overlap per lane instead of dropping
  the assertion.
- **Read-App test not ported.** `test_the_read_app_key_is_read_by_the_lane_s_launcher` has no
  counterpart, because this lane has no read-App reader.
- **Grant-comment edits not ported.** #688 edited comments in `.bws-secrets.toml` and the Lane Apps
  block about the read App's keeper grants. This lane's account has no grant on that keeper.

## Concerns and follow-ups for HQ

1. **change-manager registry gap (check 2).** change-manager's `change-manager-read-bearer` row
   doesn't list `run-rotation-decomposer.sh` as a reader. It also doesn't list the
   membership-check lane's container.
2. **Conflict with #692 (check 5).** The PRs conflict textually in one docstring and the
   `placeholders` line. The resolution is in check 5.
3. **Stale cut-over list.** The decomposer's list in `docs/operations/deploy.md` (lines 513-520)
   still names steps 2 (keepers) and 4 (grants), which G-53 completed. #688 and #691 left the change
   proposer's equivalent paragraph alone, so I didn't edit this one.
4. **Scratch files.** `/tmp/w135-ctl`, `/tmp/w135-infraops`, and `/tmp/w135-cfg` remain. Removing
   them recursively is denied in this session. They hold no secrets.
5. **Test database.** `orchestrator_test_hq_w_135` still exists, for HQ to drop at teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff adds registry
  rows, comments, and tests only.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try` and no
  `except`. Every failure surfaces as an assertion or an adapter exception.
- **Code Brain rule 5 (never log secrets):** nothing prints a token, a ping URL, the UUID inside a
  ping URL, or a bearer. The rows and the test name only BWS secret ids, which are distinct from
  any ping URL's UUID. The `cred-findings` output I quote has ids and dates only.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; bootstrap token never
  inline):** the diff has no token. The only values are BWS secret UUIDs, a Coolify resource uuid,
  and variable names. The security scan reports 0 BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** the diff has no
  `https://healthcheck.alobar.net/ping/` URL. The ping-URL row names its keeper's id and the check
  name only.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The diff makes no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the test, a
  plan, or a log. The rows name ids only, and the test reads ids and field names.

## Merge with main

W-136 merged `origin/main` (`b4a48f7c`, PR #692) into this branch as merge commit `32048c7c`
(parents `eaee401c` and `b4a48f7c`), pushed fast-forward.

- **Conflict:** only the docstring of `test_every_lanes_ping_url_secret_is_listed_for_that_lane`
  in `tests/scripts/test_deploy_lanes.py`. The resolution drops the change proposer's G-49
  sentence, which #692 made false, and keeps this branch's G-53 sentence about the rotation
  decomposer's keeper. The `placeholders` line merged without a conflict to
  `{"rotation-decomposer"}`. The G-53 sentence is reflowed by one word so the line stays within
  100 characters (ruff E501). Nothing else changed.
- **`make check`:** green on `32048c7c` against `orchestrator_test_hq_w_136` with `PGTZ=UTC`:
  14851 items collected, 14850 passed, 1 skipped, pyright 0 errors.
