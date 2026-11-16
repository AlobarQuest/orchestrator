Outcome: done

# Report: the work carrier's registry rows (ADR-0059 increment 4, step 3f-2, cut-over item 3)

Worker W-190. Branch `hq/lanes-inc-4-work-carrier-rows`, base `origin/main` at `80070b0f`, with
`origin/main` (`849d6a71`, #728) merged in afterwards. PR: #729, ready for review, not merged. Code
commit `0aa62b3b`; merge `3f3ebb78`; the PR head is the report commit after it.

The repository facts in the handoff (line numbers, the launcher's reads, the manifest's lanes, the
lane-row pattern) were confirmed at the base. The two keeper ids, the service uuid
`am99g0klcsa7u8dpb4t19xjr` and gate G-67 come from the handoff alone: no BWS or Coolify read was
allowed. I compared each id mechanically against the handoff file with `grep -c`. That is the only
verification, and the new test pins the same strings, so it can't catch a transcription error in
the handoff itself.

## What I built

The first of item 3's two changes (`docs/operations/deploy.md` "Registered lanes", the work
carrier's cut-over item 3, lines 637-640), mirroring the rotation decomposer's first rows PR, #693
(`da044d6d`):

- `.cred-consumers.toml`, a new block "The work-carrier lane's token and ping URL (ADR-0059
  increment 4)" after the rotation decomposer's block, the last lane block:
  - `work-carrier-bws-token`: `bws-lane-token`, keeper `7667794c-…`, `rotated_by_sds = true`,
    `rotate_requested = "2026-11-16"`, `reach_secrets` of nine (check 1), `reach_projects = []`.
    Consumers are the keeper (`bws-secret`) and `coolify-env` on the `sds-lane-work-carrier`
    service `am99g0klcsa7u8dpb4t19xjr`, key `WORK_CARRIER_BWS_ACCESS_TOKEN`, `redeploy = false`.
  - `work-carrier-ping-url`: `healthchecks-ping-url`, keeper `55fbae20-…`, `rotated_by_sds = true`,
    `healthchecks_check = "sds-work-carrier"`, one keeper consumer, and no `rotate_requested`.
  - Neither row has `created`. The block's comment says the token is due, the ping URL is dated by
    a later change once the token's `store` unit completes, and both keepers came at G-67. Unlike
    #693's comment, it doesn't say the account got its reads at that gate: the grants are cut-over
    item 4, after this PR.
- `lanes-read-app-private-key`: a `runtime-fetch` consumer for `scripts/run-work-carrier.sh` in the
  container, modelled on the change proposer's reader in the same row. The launcher reads the key
  as `READ_APP_KEY_UUID` (`scripts/run-work-carrier.sh:148`) and pipes it straight into
  `sds-github-mint` on every pass, bare or not (`:318-321`); it pings only on `--register`. The
  block comment gains one sentence: this reader lands with the rows (item 3), ahead of its
  account's grant (item 4). Without it, the comment's "hold read grants … so that row lists each
  lane's reader" would be false for this lane.
- `factory-events-read-password`: the `runtime-fetch` reader's note now names the work-carrier lane
  account as the holder, in place of `Keychain Claude/BWS_ACCESS_TOKEN_SDS (sds-operator) until
  ADR-0059 step 3f-2`. `kind`, `repo`, `ref` and `healthchecks_check` are unchanged.
- `lanes/work-carrier/compose.yaml`: the comment that said the VM root creates the ping-URL keeper
  at cut-over was false after G-67. It now names `work-carrier-ping-url`, created at G-67, and says
  a later change writes its id. `SDS_DEADMAN_PING_URL_UUID` stays `""`.
- `tests/rotation_worker/test_work_carrier_rows.py` (new, 20 tests) and
  `tests/scripts/test_deploy_lanes.py` (check 5).

## Checks 1-7

### 1. `reach_secrets`

I parsed `.bws-secrets.toml` with `tomllib` for each `[[secret]]` whose `lanes` names
`work-carrier`. There are eight, the same set HQ found:

| UUID | Name | `uuid` line | `lanes` line |
|---|---|---|---|
| `221a48d5-…` | `ORCHESTRATOR_M2M_CREDENTIALS` | 23 | 26 |
| `a9843d17-…` | `work-carrier-observer-bearer` | 69 | 72 |
| `f793576f-…` | `orchestrator-observer` | 126 | 129 |
| `314f276d-…` | `change-manager/M2M_TOKEN_READ` | 190 | 193 |
| `acccb346-…` | `change-manager/M2M_TOKEN_PROPOSE` | 196 | 199 |
| `b415d03b-…` | `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` | 281 | 284-287 (multi-line array) |
| `314a966c-…` | `lanes-read-app-private-key` | 337 | 340 |
| `2f708d08-…` | `factory-events-read-password` | 465 | 468 |

With the ping-URL keeper `55fbae20-…`, the row's reach is nine. deploy.md lines 598-604 list the
same eight by role. `test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper`
asserts that the reach equals the manifest's set plus the ping keeper, and equals the pinned set.

The ping keeper isn't in `.bws-secrets.toml` yet. That's correct for this step: the decomposer's
keeper entered the manifest in #697 (deploy.md step 6, `5a781dbb`), not in its rows PR.

### 2. Change-manager's bearer rows (follow-up for HQ, no change made)

I read change-manager `origin/main` at `67111f7` (`git show origin/main:.cred-consumers.toml`; I
ran `git fetch` there to refresh the ref, and changed nothing):

- `change-manager-read-bearer` (`314f276d-…`), lines 123-129: one `runtime-fetch` reader of
  `scripts/run-work-carrier.sh`, note "launchd com.devon.work-carrier 07:05 (fetched on every
  run); Keychain Claude/BWS_ACCESS_TOKEN_VPS_BACKUP (broad account); …".
- `change-manager-propose-bearer` (`acccb346-…`), lines 193-199: one `runtime-fetch` reader, note
  "launchd com.devon.work-carrier 07:05 (--register; fetched only by a registering run); Keychain
  Claude/BWS_ACCESS_TOKEN_VPS_BACKUP (broad account); …".

Follow-up: once item 4 makes both direct grants to the lane account, each row's work-carrier note
needs the "two readers of this launcher" form its other moved lanes already use (the
membership-check, rotation-decomposer and change-proposer readers in the same file). That form
names the launchd job with the broad account and the `sds-lane-work-carrier` container with the
lane's own BWS token. For READ, every pass. For PROPOSE, `--register` passes only. After the Mac
job retires (item 9), each note drops its launchd reader.

### 3. The rows, parsed through the code

`tests/rotation_worker/test_work_carrier_rows.py` follows #693's
`test_rotation_decomposer_rows.py` (`git show da044d6d:…`, not HEAD, where #695 made both rows
due). It reads this repository's registry at `main` through `RepoMirror` and
`rotation_worker.registry.rotated_by_sds`, then checks the following:

- `bws_lane_token.check_row` and `lane_variable`: uuid `am99g0klcsa7u8dpb4t19xjr`, key
  `WORK_CARRIER_BWS_ACCESS_TOKEN`, `redeploy is False` read from the row itself, and the compose
  maps `BWS_ACCESS_TOKEN: ${WORK_CARRIER_BWS_ACCESS_TOKEN}`.
- `healthchecks_ping_url.check_row` and `healthchecks_check == "sds-work-carrier"`.
- Each row's keeper consumer uuid equals its `bws_uuid`.
- Only the token row is due (`rotate_requested == "2026-11-16"`; none on the ping URL).
- Neither row has `created` or `last_rotated`, and `plan_for(..., first_issue=True)` derives an
  issue plan for both.
- No two rows share a reach: parametrized over all nine other lane tokens. I computed the overlaps
  from the file, and every other lane token shares at least one secret with this one (the observer,
  the read App's key, the `sds-bridge` sender, or a change-manager bearer). So the test asserts a
  non-empty intersection for each, that `sharing_reach` returns `[]` for the real row, and that a
  twin given the other lane's whole reach is caught as `[other]`. The rule compares whole sets and
  passes for all nine.

Mutation checks, each run on a copy (`cp` to `/tmp`, restored with `cp`, `cmp` clean, and
`__pycache__` cleared before the control run):

| Mutation | Result |
|---|---|
| `redeploy = true` on the work-carrier `coolify-env` | 1 failed (`test_the_token_row_is_one_the_lane_token_adapter_acts_on`) |
| `rotate_requested = "2026-11-16"` added to the ping-URL row | 1 failed (`test_only_the_token_row_is_due`) |
| The token's keeper consumer uuid altered by one digit | 2 failed (the adapter test and `test_each_row_s_keeper_consumer_is_its_bws_uuid[work-carrier-bws-token]`) |
| Restored | 20 passed |

### 4. Due-ness

In infraops `src/security-drift/cred-rotation.ts` at `c3359bf` (GitHub `main`), lines 279-291 add
`cred.rotation-requested` when `now >= Date.parse(rotate_requested)` and there's no rotation
recorded on or after it. The age finding (lines 266-277) needs an anchor, and neither row has one.

I ran it. I cloned the checkout into `/tmp/w190-scratch/infraops`, fetched GitHub `main` there,
built it there, and pointed `INFRADRIFT_CONFIG_DIR` at a scratch list naming a copy of this
branch's registry, with an empty `0600` state file. I didn't edit or build
`~/Projects/infraops-mcp-server`, and I didn't read `~/.config/infra-drift/cred-consumers.list`.
`cred-findings --now 2026-11-16T22:00:00Z` exited 0. Its work-carrier entries, filtered and
printed by a Python one-liner from the JSON it wrote, were the following:

```
{'check': 'cred.rotation-requested', 'id': 'work-carrier-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-16', 'rotated_by_sds': True}
last_rotated {'work-carrier-bws-token': None, 'work-carrier-ping-url': None}
```

The ping-URL row yields no finding.

### 5. Fixtures and existing tests

- `tests/scripts/test_deploy_lanes.py::test_the_repositorys_registry_registers_the_nine_moved_lanes`
  reads the real registry through `deploy.lanes_in_registry`. It's renamed `…_ten_…` and adds
  `work-carrier`.
- `test_every_lanes_ping_url_secret_is_listed_for_that_lane` reads each compose's
  `SDS_DEADMAN_PING_URL_UUID`. `placeholders = {"work-carrier"}` stays. Only the docstring changed:
  "No lane is waiting for its keeper" became "The work carrier's keeper was created at HQ gate G-67;
  a later change writes its id."
- Other tests that name lane tokens (`test_lane_credentials.py`, `rotation_proposer/test_pass.py`,
  `test_generate.py`, `test_rotation_edges.py`, `test_standing_approval_gate.py`) and the fixtures
  (`rotation_destination_format.json`, `rotation_class_policy.json`,
  `rotation_package_issue_bws-lane-token.json`) use synthetic rows, not this registry, so nothing
  changes there.

Once this merges, `registered_lanes` (`scripts/deploy_orchestrator.py:1255`) returns ten lanes:
`change-proposer`, `deploy-watcher`, `execution-watch`, `landing-ledger`, `membership-check`,
`pin-watcher`, `revision-watcher`, `rotation-decomposer`, `unit-verifier` and `work-carrier`.
`work-carrier` is already in `LANE_CHECKS` (`:968`), so `lanes_in_registry` doesn't refuse it.

Nothing treats the work carrier as registered in a way that acts before its token exists. The
function has two callers, and both skip a lane with no passed `--add`:

- `require_lanes_current` (`:1265-1291`), the release hold: `last is not None` skips it.
- `lanes_to_follow` (`:1294-1300`): `last_finished(...) is not None` filters it out.

`--add` is item 7, after the token's mint. Until then a deploy neither waits on nor touches
`sds-lane-work-carrier`. Once `--add` passes, the lane holds every release until it follows the
served tag.

### 6. How the rows go live

ADR-0031 decision 1 (`docs/decisions/0031-…md:35-47`): each scheduled launcher fast-forwards the
working copy whose code it runs, as its first step, with `~/.claude/bin/activate-checkout.sh`
(`merge --ff-only '@{u}'`, line 93). Line 45 limits this to what a launcher executes, not what it
reads as data, and the registry is data. So the main tree's copy of `.cred-consumers.toml` moves
when an orchestrator launcher activates the main tree to run its own code: every Mac launcher in
`scripts/` that runs from `$REPO_ROOT` (for example `run-work-carrier.sh:164-178`, which activates
before it runs). `run-rotation-proposer.sh:99-107` is a different activation: it fast-forwards the
intent-packages checkout, on `--submit` only.

That the 03:00 scan and the rotation proposer read the main tree's working copy is HQ's statement.
I didn't read `~/.config/infra-drift/cred-consumers.list`, as the handoff told me. The executor
reads the registry at `main` through `RepoMirror`, independent of any working copy (the new test
reads it the same way).

### 7. The membership check

`hq/membership-check-d59-narrow` merged as #728 (`849d6a71`, 2026-11-16 22:19 UTC) after my base,
so I merged `origin/main` into the branch and judged the change on it.

`src/membership_check/cli.py:115-126` (`_flagged_repositories`) reads every registry through the
lane's `RepoMirror`, keeps the `rotated_by_sds` rows `of_a_type` passes (types the executor
rotates), and returns only the set of repositories they come from. After #728,
`consistency.py:170-197` reports a repository whose `.cred-consumers.toml` has a
`rotated_by_sds = true` row while sitting outside `REGISTRY_REPOSITORIES`. Both are per repository.
The orchestrator was already in both sets through its other lane rows, and it is in
`REGISTRY_REPOSITORIES`, so the pass is unchanged. The new rows can't add a finding.

## Tests and checks run

- New and touched tests: `test_work_carrier_rows.py` and `test_deploy_lanes.py`, 110 passed.
- `make check` with `orchestrator_test_hq_w_190`, at `0aa62b3b`: exit 0; 15,247 passed, 1 skipped
  (lint, format and pyright clean). After the merge of `origin/main`, at `3f3ebb78`: exit 0; 15,264 passed, 1 skipped.
- CI on the PR head: CIRESULT.
- Security scan from the repo root: BLOCK 0, WARN 2, INFO 1. Both WARNs come from before this
  change: glob `*-migration/` not covered, and the undeclared UUID `aaff45ca-…` that earlier
  reports name (`adr-0055-a22-increment-2-report.md:183`). Neither is in this diff.
- `/code-review` (low) on `origin/main...HEAD`: no findings (the non-test hunks are TOML rows and compose comments).

## Files changed

- `.cred-consumers.toml`
- `lanes/work-carrier/compose.yaml` (comment only)
- `tests/rotation_worker/test_work_carrier_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-4-work-carrier-rows-report.md` (this report)

## Self-review and deviations

- The block comment leaves out #693's "where the lane's account got its four reads", which is false
  for this lane: G-67 created the keepers only.
- The read-App block comment gained a clause because the new reader comes before its grant.
- The reach test's list is "all nine overlap", computed from the file, not the decomposer's
  overlapping and disjoint split.
- The read password's note is a rewrite, not a swap of the holder clause. Before:
  `launchd com.devon.work-carrier 07:05 (--register), with the switch file present; Keychain
  Claude/BWS_ACCESS_TOKEN_SDS (sds-operator) until ADR-0059 step 3f-2; passed to work-carrier alone,
  never work-watcher`. After: `read in the sds-lane-work-carrier container with the lane's own
  token, each pass (bare or --register); held by the work-carrier lane account (ADR-0059 step
  3f-2); passed to work-carrier and its chain verify alone, never work-watcher`. Swapping only the
  holder would have left a launchd sentence naming a container account. The rewrite drops the
  launchd schedule and the switch-file clause. It describes a read that starts only after items
  4-7: the grant, the token's mint and the `--add`.

## Concerns

- **The read password has two readers until the Mac job retires.** The registry note now names the
  lane account as the holder. But the Mac launchd job still fetches `2f708d08-…` through
  `sds-operator` behind the switch file (`scripts/run-work-carrier.sh:467-469`, "With an
  `sds-operator` identity, the read password is fetched through it"). deploy.md item 9 (lines
  672-673) removes that read at the Mac job's retirement. Until then, the row records only the
  container reader. It doesn't block anything: the class isn't `rotated_by_sds`, and both readers
  fetch fresh each run. A hand rotation reaches both without the registry naming the Mac.
- The lane account holds no grant on any of its nine secrets yet (item 4). The read-App reader and
  the read password's holder are recorded ahead of those grants, as item 3 orders.

## Follow-ups

- change-manager: the two bearer rows' work-carrier readers take the "two readers of this
  launcher" form once item 4 makes both direct grants (check 2).
- The second rows PR (item 5): date `work-carrier-ping-url` with `rotate_requested` once the
  token's `store` unit completes.
- The step-6 change: the compose names the ping-URL secret's id, `.bws-secrets.toml` lists
  `55fbae20-…` for `work-carrier`, and `work-carrier` leaves `placeholders`.
- After the Mac job retires (item 9): the read password's note can say the lane account is its one
  holder, and the Mac `runtime-fetch` entries for `run-work-carrier.sh` go.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** no logging change. The diff
  adds registry rows, a comment and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test has no `try`/`except`.
  Every failure is an assertion or a raised adapter error that fails the test.
- **Code Brain rule 5 (never log secrets):** nothing prints a token, ping URL, bearer or password.
  The rows and tests name BWS secret ids, a Coolify resource uuid and a check name only. The
  ping-URL row names its keeper's secret id (`55fbae20-…`), not the UUID inside the ping URL.
- **Infra Brain 591, 592, 593:** no BWS token in any tracked file or in history, and no inline
  bootstrap token. The scan reports BLOCK 0. The test holds no token-shaped value.
- **Infra Brain 2890 (never commit a ping URL):** no `https://healthcheck.alobar.net/ping/` URL in
  the diff. The compose still holds `SDS_DEADMAN_PING_URL_UUID: ""`.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. There's no domain change.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a test or a
  log. The rows name ids only, and the `cred-findings` run used an empty state file and printed ids
  and dates.
