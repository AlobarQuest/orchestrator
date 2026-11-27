Outcome: done

# Report: remove the 13 retired `<lane>-bws-token` registry rows (ADR-0055 Amendment 32 item 5.3)

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/828 (open, not merged). Branch
  `hq/broker-lane-token-rows-removal`, base `origin/main` `d5309617`. The head is this report's
  commit on that branch, which follows `bb633c67`.
- **Production acts:** none. No BWS, Coolify, Healthchecks, VPS, intent-packages or GitHub call
  besides `git push` and `gh pr create`. The intent-packages and infraops-mcp-server checkouts were
  only read with `grep`.

## What I built

1. **`.cred-consumers.toml`.** Removed 13 `[[credential]]` rows, each with its two
   `[[credential.consumer]]` entries (keeper and Coolify variable) and its in-row comments. I
   confirmed before removal that each row had `class = "bws-lane-token"`, and that none carried an
   `exposure` table. The removed rows:
   `execution-watch-bws-token`, `landing-ledger-bws-token`, `pin-watcher-bws-token`,
   `unit-verifier-bws-token`, `membership-check-bws-token`, `deploy-watcher-bws-token`,
   `change-proposer-bws-token`, `rotation-decomposer-bws-token`, `rotation-proposer-bws-token`,
   `work-carrier-bws-token`, `bump-proposer-bws-token`, `revision-watcher-bws-token` and
   `inert-landing-bws-token`.
   - The file goes from 52 rows to 39. Every remaining row parses equal to `HEAD`'s
     (`tomllib`, dict equality), and the removed ids are exactly those 13. `secrets-broker-bws-token`
     is the one `bws-lane-token` row left. The handoff mentions keeping "the change-window and
     follow-up-mint lane tokens". Neither lane has a lane-token row: both read through the broker,
     and their rows are ping URLs and work secrets, which stay unchanged.
   - Comments: each lane's section header described "the lane's token and ping URL". It now
     describes only the ping URL row, keeps its keeper's provenance (create-keeper or HQ gate), and
     notes that the token row was removed under item 5.3 and that the token's keeper stays in
     `Rotation / Keepers`. About 15 later blocks point at the `created` explanation in the
     execution-watch and landing-ledger blocks, so both keep it. The GitHub App keys' comment
     said "their accounts' grants stay until the lane tokens are revoked". It now records that the
     tokens were revoked and their rows removed.
2. **Tests** (each named below).
3. **ADR-0055 Amendment 32:** no change. The amendment has no place where items record their
   merging pull request: PR numbers appear only inline in its narrative, and its Open items record
   answers, not merges. So I skipped the edit, as the handoff allows.
4. **`docs/operations/`.**
   - `secrets-broker.md`, "What the broker doesn't change": the lanes "keep their variables and
     token rows until the lane tokens are revoked" became a statement that the tokens were revoked,
     the variables deleted and the rows removed, with the keepers left in place.
   - `rotation-executor.md`: the `helper_target` example `revision-watcher-bws-token` became
     `secrets-broker-bws-token`. "Lane tokens and ping URLs" gained a paragraph saying broker lanes
     have no token row. The `create-keeper` example uses `execution-watch-ping-url`, and the TOML
     template uses `<lane>` and `<LANE>` placeholders. The helper-target rules paragraph no longer
     says the broker lanes' rows "name none and keep the `PATCH` proof".
   - `deploy.md`, "Registered lanes": added one sentence saying every lane is registered by its
     table, and replaced "its token entry lands with its cut-over".

## Tests changed, and the guards the removal broke

Before the test changes, the removal failed 200 tests. Each change follows.

- **The 13 lanes' rows tests** (11 files: `test_{execution_watch,landing_ledger,deploy_watcher,`
  `revision_watcher,unit_verifier,pin_watcher,membership_check,change_proposer,rotation_decomposer,`
  `work_carrier,inert_landing}_rows.py`, and `test_proposer_lane_rows.py` for the two proposers).
  - Removed: `test_the_token_row_is_one_the_lane_token_adapter_acts_on`,
    `test_the_token_row_declares_the_lane_s_manifest_secrets_and_its_ping_url_keeper` and
    `test_no_other_lane_token_row_declares_the_same_reach`. Also removed:
    `test_the_two_proposers_share_secrets_and_still_differ`, which existed only for the two token
    rows, and the `TOKEN`, `OTHER_LANE_TOKEN(S)`, `GRANTED`, `REACH` and `SERVICE` constants.
  - Narrowed to the ping URL row only: the tests parametrized over `[TOKEN, PING_URL]` (keeper
    consumer, no age anchor, issue plan, is due, handed to the SDS).
  - Moved: the "ping keeper is in the lane's `.bws-secrets.toml` secrets" assertion, from the
    deleted token test into each file's ping-URL test.
  - Kept: inert landing's compose check (no `${INERT_LANDING_BWS_ACCESS_TOKEN}`, the broker mount),
    as `test_the_lane_reads_through_its_broker_socket`. Also kept: the bump proposer's Mac-token
    test, minus its "the lane token is a lane-token row" assertion.
  - No exit manifest or doc names any removed or renamed test.
    `docs/operations/wave-exit-manifest.toml`, `exit-criteria-claims.toml`, `docs/` and the CLAUDE.md
    files return no match for the names.
- **New: `tests/rotation_worker/test_retired_lane_token_rows.py`**, parametrized over the 13 lanes.
  - It asserts that no row in the whole parsed registry (read whole, not through `rotated_by_sds`)
    has the id `<lane>-bws-token`.
  - It asserts that no consumer has the key `<LANE>_BWS_ACCESS_TOKEN`.
  - It asserts that no lane compose maps `${<LANE>_BWS_ACCESS_TOKEN}`.
- **`test_secrets_broker_row.py`:** `test_no_other_lane_token_row_declares_the_same_reach`
  asserted `len(lane_rows) > 1`. It is now `test_the_row_is_the_only_lane_token_row`, which asserts
  that the lane-token rows are exactly `[secrets-broker-bws-token]`.
- **`test_lane_token_project_reach.py`:**
  `test_the_real_broker_row_takes_the_project_path_and_every_lane_row_the_exact_one` asserted
  `len(rows) > 1` and ran `build_provider` over every real lane row. It is now
  `..._and_a_lane_row_the_exact_one`. It asserts the broker row is the only lane-token row, and it
  runs the exact-reach path on a copy of that row with `reach_projects = []` and a fake id. The
  synthetic `_provider` tests still cover the exact path, and `test_lane_credentials.py` still
  covers `sharing_reach`.
- **`tests/scripts/test_deploy_lanes.py`:**
  - `test_the_repositorys_registry_registers_the_thirteen_moved_lanes` is now
    `test_the_repositorys_registry_registers_no_token_lane`, which asserts
    `lanes_in_registry == set()`.
  - `test_the_repositorys_registry_and_allowlist_register_seventeen_lanes` now asserts no token
    lane and 17 allowlisted lanes.
  - The deploy command still registers all 17 lanes: every one has a table in
    `lanes/secrets-broker/allowlist.toml`, and `registered_lanes` is the union, so no lane leaves
    the release hold and no code changes.
- **Standing approval** (`tests/rotation_proposer/test_standing_approval.py`, and through its
  helpers `tests/work_carrier/test_standing.py` and `tests/api/test_work_carrier_observation_ingest.py`;
  61 failures):
  - These read `landing-ledger-bws-token` from the real registry as a lane token on the issue
    plan. No real row is one any more: the broker's row has `created`, so it takes the rotation
    plan. They now publish `LANE_TOKEN_ROW`, a synthetic row in the removed row's shape with
    placeholder UUIDs (the `5555…`, `6666…` and `7777…` patterns already used in that file).
  - The test copies none of the retired keeper or service UUIDs.
  - `_row(credential_id)` picks the synthetic row for the token and the real row otherwise.
  - The new `test_the_lane_token_row_is_one_the_executor_acts_on_with_the_issue_plan` runs
    `bws_lane_token.check_row` and `plan_for` on the synthetic row, so it can't drift from what the
    executor accepts. Three corrupted copies (missing `reach_secrets`, a key that names no lane
    token, `redeploy = true`) are each refused with `ContractError`.

## Checks run, with results

- **`make check`** (worktree DB `orchestrator_test_hq_w_331`, `SECURITY_STANDARDS_DIR` at the
  fixture, `PGTZ=UTC`): exit 0. ruff and pyright are clean (0 errors). **15,014 passed, 1
  skipped.** For comparison, the base suite collected 15,084 (14,883 passed and 200 failed after
  the removal, plus 1 skipped). Before the review fix, the affected directories passed 2,645
  tests.
- **Fails with a row restored:** I copied my file to a protected location and spliced
  `landing-ledger-bws-token`'s 28-line block from `HEAD` back in before its ping URL row. Then I
  ran `tests/rotation_worker` and `tests/scripts/test_deploy_lanes.py`: **6 failed, 1,973 passed.**
  The failures:
  - `test_retired_lane_token_rows.py::test_the_lane_s_token_row_is_absent[landing-ledger]`
  - `test_retired_lane_token_rows.py::test_no_row_names_the_lane_s_token_variable[landing-ledger]`
  - `test_secrets_broker_row.py::test_the_row_is_the_only_lane_token_row`
  - `test_lane_token_project_reach.py::test_the_real_broker_row_takes_the_project_path_and_a_lane_row_the_exact_one`
  - `test_deploy_lanes.py::test_the_repositorys_registry_registers_no_token_lane`
  - `test_deploy_lanes.py::test_the_repositorys_registry_and_allowlist_register_seventeen_lanes`

  I then restored my file from the copy; `cmp` showed it identical.
- **Token-shape grep of the added lines** (437 `+` lines of the staged diff): no match for the BWS
  token shape (a `0.` prefix, a UUID, a dot), and no inline `BWS_ACCESS_TOKEN=` assignment. The
  only UUIDs added are the three placeholders
  `55555555-5555-4555-8555-555555555555`, `66666666-…` and `77777777-…`.
- **`code-standards risk`:** `high-risk`. The matched rules were `auth` on
  `test_lane_token_project_reach.py` and `test_retired_lane_token_rows.py`, and `secrets` on
  `test_secrets_broker_row.py`, so I ran both reviews:
  - **`/code-review` (high):** 7 nits.
    - Acted on: `LANE_TOKEN_ROW` could drift from the adapter (pinned by the new test), and an
      overlong `deploy.md` line (rewrapped).
    - Confirmed: `sharing_reach` keeps synthetic coverage in `test_lane_credentials.py`.
    - Not changed, by the handoff's scope: the `.bws-secrets.toml` comments and the lane compose
      comments (both in Concerns).
    - Not changed, because they describe the still-present mechanism and no row: `deploy.md`'s
      generic token-entry mechanism and `rotation-executor.md`'s generic lane-token procedure.
      Each now carries a sentence that the 13 rows are gone.
  - **`vendor-review`:** the review is complete for the seats that ran: Claude ok, Codex ok
    (approve, 0 findings), agy off by configuration. It dropped no finding and kept 1 low finding,
    my own: `test_the_lane_s_compose_maps_no_token` matches only the literal
    `${<LANE>_BWS_ACCESS_TOKEN}`.
    - **Reproduced:** a landing-ledger compose mutated to map `BWS_ACCESS_TOKEN: ${SOME_OTHER_TOKEN}`
      passes that guard.
    - Covered elsewhere: the deploy command's `token_refusals` refuses any mapping but the lane's
      own variable, `broker_lane` fails on any `BWS_ACCESS_TOKEN` key, and
      `test_the_allowlists_tables_are_exactly_the_broker_lanes_composes` holds the composes to
      that. I left the guard as is.

## Files changed

- `.cred-consumers.toml`
- `docs/operations/deploy.md`, `docs/operations/rotation-executor.md`,
  `docs/operations/secrets-broker.md`
- `tests/rotation_worker/test_retired_lane_token_rows.py` (new)
- `tests/rotation_worker/test_{execution_watch,landing_ledger,deploy_watcher,revision_watcher,`
  `unit_verifier,pin_watcher,membership_check,change_proposer,rotation_decomposer,work_carrier,`
  `inert_landing,proposer_lane}_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`,
  `tests/rotation_worker/test_lane_token_project_reach.py`
- `tests/scripts/test_deploy_lanes.py`
- `tests/rotation_proposer/test_standing_approval.py`, `tests/work_carrier/test_standing.py`
- This report

## Self-review

- The diff is the smallest that removes the rows and keeps every remaining test meaningful. No file
  outside `tests/`, `docs/operations/` and the registry changes, and no source code changes.
- A first removal script stopped at the first in-row comment and left the rest of each row
  attached to the row before it. I caught it from the diff, restored the file from `HEAD` (my edit
  was the only change in it), and reran with the right stop line. The parse-equality check then
  confirmed the result.
- **Lost real-registry coverage** (deliberate, now synthetic or gone):
  - Each lane's token reach equal to its manifest secrets: gone, with the rows.
  - The no-two-lane-rows-share-a-reach rule on real rows: now synthetic only, in
    `test_lane_credentials.py`.
  - The exact-reach bind through `build_provider` on real lane rows: now on a no-project copy of
    the broker row.
  - The standing approval's lane-token issue-plan path: now on `LANE_TOKEN_ROW`.
- These code paths stay live code with no real subject: `bws_lane_token`'s issue plan, its exact
  reach, `sharing_reach`, the standing approval's `bws-lane-token` branch, and
  `deploy.lanes_in_registry`, which returns the empty set on the real file.

## Concerns: places that still name a removed row id or a retired keeper UUID (unchanged)

The 13 keeper UUIDs, from the removed rows' `bws_uuid`:

| Row | Keeper UUID |
| --- | --- |
| execution-watch | `5a21575b-b25b-42b5-bf05-b4fc00cd30e0` |
| landing-ledger | `d7813bd9-0d9d-4781-ae86-b4fd017539ff` |
| deploy-watcher | `660539eb-97ac-422f-b776-b4ff0004b7c2` |
| revision-watcher | `e6c8d8cc-c64e-4e91-bb5b-b4ff00cba872` |
| unit-verifier | `c8381bef-f8a0-4b73-8158-b4ff00e87766` |
| pin-watcher | `824605ad-d05b-4bd0-b9b1-b50000e8886e` |
| membership-check | `eb70b29d-faa2-4ff2-ba2e-b5000129d84f` |
| change-proposer | `141260f5-bc4b-44d8-8ac8-b50100c0d40a` |
| rotation-decomposer | `623e61a6-d613-4e89-9626-b501013afbeb` |
| work-carrier | `7667794c-5691-4385-8351-b5040164d2e2` |
| bump-proposer | `3299bd70-04af-4626-8fb8-b506013129e6` |
| rotation-proposer | `540f1cb7-2487-4b1a-8179-b50601312b40` |
| inert-landing | `2e6d746e-603c-4ab7-b3bc-b50701315811` |

Where they're still named:

- **`.bws-secrets.toml`** (not to be changed, by the handoff): 12 comments say the executor reads
  a lane's token keeper "by the id its `.cred-consumers.toml` row names (`<lane>-bws-token`)", at
  lines 419 (landing-ledger), 434 (deploy-watcher), 448 (revision-watcher), 463 (unit-verifier),
  477 (pin-watcher), 492 (membership-check), 506 (change-proposer), 520 (rotation-decomposer), 534
  (work-carrier), 548 (bump-proposer), 562 (rotation-proposer) and 577 (inert-landing). Each now
  points at a row that doesn't exist. The file names no keeper UUID.
- **Lane composes** (lane code, not changed): 13 comments call the Coolify variable kept "until
  the lane tokens are revoked" (or, for inert landing, "as a fallback"):
  `lanes/execution-watch/compose.yaml:8`, `landing-ledger:10`, `deploy-watcher:11`, `pin-watcher:12`,
  `change-proposer:12`, `revision-watcher:13`, `unit-verifier:13`, `membership-check:13`,
  `rotation-decomposer:15`, `inert-landing:15`, `bump-proposer:19`, `work-carrier:20` and
  `rotation-proposer:21`. The variables are deleted, so the comments are false.
- **intent-packages** (local checkout `00b32ef`, `main`; not fetched, so `origin` may be ahead):
  - `packages/rotation-<lane>-bws-token/package.yaml` and `lineage.yaml` for all 13 lanes.
  - `tests/test_approval_policy_rotation.py`.
  - `tests/fixtures/standing_approval/rotation-revision-watcher-bws-token/package.yaml`.
  - `tests/fixtures/package_hashes.json`.
  - `docs/superpowers/reports/adr-0055-a22-increment-1-report.md`.
  - The packages are approved and name rows that no longer exist. If the proposer or the carrier
    reads them, a missing-row refusal or a `RegistryError` is the expected answer.
- **infraops-mcp-server** (local checkout `0fe5b92`, `main`): no file names a removed id or keeper
  UUID. I didn't read infraops' live rotation state (no MCP or VPS call was allowed), so any
  `last_rotated` or rotation-state entry it holds for the 13 ids is unchecked. Once the nightly
  run reads this file from `main`, those ids stop appearing in its findings.
- **This repository, outside the change:**
  - Tests that use a removed id as a synthetic string and don't read the registry:
    - `tests/contract/test_consumer_proof_note.py:40` (`execution-watch-bws-token`).
    - `tests/rotation_worker/test_lane_token_loaded.py:105`.
    - `tests/rotation_worker/test_lane_credentials.py:130, 944, 947, 954, 955, 962, 978, 979, 982`.
    - `tests/rotation_proposer/test_standing_approval.py:51` and `tests/work_carrier/test_standing.py:37`
      (`TOKEN = "landing-ledger-bws-token"`, the synthetic row's id).

    All pass; I left them.
  - Historical records: `docs/decisions/0055-…md` (lines 2696 and 4644) and many reports under
    `docs/superpowers/reports/`. Six of those reports quote keeper UUIDs:
    `lanes-inc-3-landing-ledger-rows-report.md:62`, `lanes-inc-3-pin-watcher-rows-report.md:17`,
    `lanes-inc-4-change-proposer-rows-report.md:19`, `lanes-inc-2-ping-uuid-report.md:89,101`,
    `lanes-inc-2-registry-rows-report.md:16` and `lanes-inc-3-revision-watcher-rows-report.md:17`.
    These are records of past work, so I left them.

## Standards followed

- **Code Brain 3 (logging):** no logging added. The change is a registry file, tests and docs.
- **Code Brain 4 (error handling):** no `except` added. The new tests assert directly.
- **Code Brain 5 (security):** nothing logs or prints a secret. The tests read only ids, keys and
  compose text.
- **Infra Brain 591, 592, 593 (no token in tracked files, in history, or inline):** the grep of
  the added lines found no token-shaped string and no inline `BWS_ACCESS_TOKEN` assignment. The
  only UUIDs added are three placeholders. The diff removes only UUIDs, which identify secrets and
  aren't secret values.
- **Infra Brain 1032 (`cred.exposure-rotate`):** none of the 13 removed rows carried an
  `[[credential.exposure]]` (checked on `HEAD`'s parse), so no stop. No exposure was added, and
  every remaining row's exposure and consumer fields are unchanged (parse equality with `HEAD`).
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The change adds no endpoint.
- **Not covered by the Standards section:** test-design conventions (synthetic fixtures versus real
  rows). I followed the repository's existing pattern of placeholder UUIDs and synthetic rows
  (`test_lane_credentials.py`, `WATCH_TOKEN_ENTRY`).

## Teardown (for HQ)

The worktree, its database `orchestrator_test_hq_w_331` and the branch stay in place while PR #828
is open. Remove them after it merges.
