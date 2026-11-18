Outcome: done

# Report: the bump and rotation proposers' registry rows (W-224)

PR: AlobarQuest/orchestrator #746, branch `hq/lanes-inc-4-proposers-rows`, base `origin/main`
`8d6e5f8e` (unchanged at dispatch). Code commit `ff9ca705`; this report is the commit after it.
The PR is open and ready, not merged.

## What I built

This is the first rows change for both lanes, covering deploy.md "Registered lanes": the bump
proposer's cut-over item 3 and the rotation proposer's item 4. It is modelled on #729
(`e7744356`).

- `.cred-consumers.toml` gets four blocks after the work carrier's:
  - `bump-proposer-bws-token` (line 1256): `bws-lane-token`, keeper `3299bd70-…`,
    `rotate_requested = "2026-11-18"`, eight-secret `reach_secrets`, and `reach_projects = []`.
    Its consumers are the keeper and `coolify-env` on service `dsmi1mj0xbjrsu8kamdxocp8`, key
    `BUMP_PROPOSER_BWS_ACCESS_TOKEN`, `redeploy = false`.
  - `bump-proposer-ping-url` (line 1286): `healthchecks-ping-url`, keeper `786f75e2-…`, check
    `sds-bump-proposer`, and no `rotate_requested`.
  - `rotation-proposer-bws-token` (line 1308): keeper `540f1cb7-…`, service
    `chyzh4utebg7ruouxxp42d0s`, key `ROTATION_PROPOSER_BWS_ACCESS_TOKEN`, five-secret reach, and
    due 2026-11-18.
  - `rotation-proposer-ping-url` (line 1337): keeper `506a5436-…`, check `sds-rotation-proposer`,
    and not due.
  - No row carries `created`. Each block's comment gives its lane's state and names G-75. The bump
    proposer's comment also says the lane token is a second token of D-54's account.
- Item 5: the read App key's row gains a `runtime-fetch` for `scripts/run-bump-proposer.sh`
  (line 1444). The push App key's row gains readers for `run-bump-proposer.sh` (line 1466) and
  `run-rotation-proposer.sh` (line 1473), which covers item 7. The section header and the push
  keeper's note ("no reader holds a grant yet") were false after G-75, and both are corrected.
- Item 6: the append password's existing `run-bump-proposer.sh` entry (line 1504) keeps its Mac
  text and now also names the container read with the lane's token, by the same account (G-75).
  This is a deviation from adding a second entry: a destination's identity is `(repo, ref)`
  (`rotation_worker/destinations.py:47`), so a second entry for the same launcher would be a
  duplicate destination. No row in the registry has one.
- `lanes/{bump,rotation}-proposer/compose.yaml`: each comment said `SDS_DEADMAN_PING_URL_UUID` is
  empty "until the lane's ping-URL secret exists". That was false after G-75, so each now names
  its keeper, the work carrier's wording. The value itself is unchanged.
- Tests: the new `tests/rotation_worker/test_proposer_lane_rows.py` has 11 test functions, 26
  cases. In `tests/scripts/test_deploy_lanes.py`, the registry test registers twelve lanes, and
  the `placeholders` docstring's "No lane is waiting for its keeper" is replaced.

The launchers read every key and password I listed. `run-bump-proposer.sh` reads the read key
every pass (`READ_APP_KEY_UUID`, line 320). It reads the push key (`PUSH_APP_KEY_UUID`, line 335)
and the append password (`FACTORY_EVENTS_APPEND_UUID`, line 281) on `--submit` only.
`run-rotation-proposer.sh` reads the push key on `--submit` (`PUSH_APP_KEY_UUID`, line 263).
Each read goes through `sds_bws_value` with the lane's token. The bump proposer pings only on
`--submit` (`CHECK_NAME`, line 183); the rotation proposer pings every pass (`sds_deadman_arm`,
line 128). The notes say so. No Mac `runtime-fetch` entry changed.

## Checks

1. **Reach derived.** I parsed `.bws-secrets.toml` for each lane's `lanes`. The bump proposer has
   seven secrets: `f793576f` (line 134), `314f276d` (line 201), `acccb346` (line 210), `b415d03b`
   (line 302), `314a966c` (line 364), `b8f547b9` (line 378) and `2ce0dbc7` (line 511), plus the
   ping-URL keeper `786f75e2`. The rotation proposer has four: `f793576f`, `acccb346`,
   `b415d03b` and `b8f547b9`, plus `506a5436`. Both match HQ's sets and deploy.md's "seven" and
   "four". The test derives the sets the same way (`_lane_secrets`) and requires
   `reach == manifest | {ping keeper}`, with the ping keeper absent from the manifest.
2. **The new row and `bws-bump-proposer-token` pass the rule.**
   - The no-shared-reach rule is `bws_lane_token.sharing_reach` (line 108). It is fed
     `provider_entries(mirror, "bws-lane-token")` (`adapters/__init__.py`, line 156), so it
     compares only rows whose `provider` is `bws-lane-token`, and it compares whole sets
     (`declared_reach(other) == mine`).
   - `bws-bump-proposer-token` (line 1563) is `provider = "bitwarden-sm"`, `rotated_by_sds =
     false`, and has no `reach_secrets`, so the rule never compares it.
   - The inbox's other-keeper check (`worker._other_keepers`, line 827) also filters on the
     adapter's provider and on `rotated_by_sds`, so it never reads the Mac token's keeper either.
   - infraops `cred-rotation.ts` has no reach rule (`grep reach` finds none).
   - `test_the_bump_proposer_account_s_mac_token_is_no_lane_token_row` pins this. I changed
     neither the rule nor the old row. See concern 1 for what this does not distinguish.
3. **Rows parsed through the code**, one file for both lanes, parametrized:
   - The rows go through `rotated_by_sds(mirror)` at `main`, then `bws_lane_token.check_row` and
     `lane_variable` (uuid and key), and `healthchecks_ping_url.check_row` with the check name.
   - The tests assert `redeploy is False` from the raw row, each keeper consumer's uuid against
     its `bws_uuid`, that only the token rows are due, that no row has `created` or
     `last_rotated`, and that `plan_for(..., first_issue=True).issue` holds.
   - Reach rule: each new row passes `sharing_reach == []` beside all 11 other lane-token rows. A
     twin given any other row's whole reach is caught as that row. The two proposers share four
     secrets and still differ.
   - Mutation: I copied the registry to `/tmp`, then set `redeploy = true` on the rotation
     proposer's env and added `rotate_requested` to `bump-proposer-ping-url`. That failed 2 of 26
     (`test_the_token_row_is_one_the_lane_token_adapter_acts_on[rotation-proposer]` and
     `test_only_the_token_row_is_due[bump-proposer]`). After I restored from the copy, `cmp`
     reported the file identical, and the test passed 26 of 26. Both mutations were caught; none
     survived.
4. **Due-ness.** `cred-rotation.ts` lines 279 to 290 raise `cred.rotation-requested` when `now >=
   rotate_requested` and the row was never rotated. Neither ping-URL row has a `rotate_requested`,
   and their class has `maxAgeDays: Infinity` (line 97), so they raise nothing.
   - I ran `node infraops/dist/cli/security-drift-cli.js cred-findings` from a scratch copy in
     `/tmp/w224-infraops.bD6c`. `.git` was excluded, `node_modules` was a symlink, nothing was
     built, and the real checkout and `~/.config/infra-drift` were untouched. Its list named only
     this branch's registry, and its state file was empty (mode 600).
   - With `--now 2026-11-19T12:00:00Z`, it returned exactly two findings for the new ids:
     `{"check":"cred.rotation-requested","id":"bump-proposer-bws-token","class":"bws-lane-token","rotate_requested":"2026-11-18","rotated_by_sds":true}`
     and the same for `rotation-proposer-bws-token`. All four had `last_rotated` null.
   - With `--now 2026-11-17T12:00:00Z`, it returned none for the four.
   - The other 21 findings come from the empty scratch state and aren't this change's.
5. **What the rotation proposer generates.** Its package id is `rotation-<credential_id>`
   (`rotation_proposer/generate.py:295`). The next `--submit` pass after the merge and activation
   proposes revision 1 of `rotation-bump-proposer-bws-token` and
   `rotation-rotation-proposer-bws-token`. Each is a first-issue plan, because `last_rotated` is
   null (`cli._first_issue`). It proposes nothing for either ping URL.
   - The rotation proposer's own row being due doesn't change the Mac job. The Mac job reads its
     secrets with the broad Keychain identity and `sds-operator`, never the lane token. The
     issue writes only the keeper and the variable of the `sds-lane-rotation-proposer` service,
     which isn't started.
   - The two token issues wait on a person's mint at the same time. `inbox.bind` refuses to bind
     from the inbox while `other_mints` is non-empty (`inbox.py`, line 151), so a pasted value
     binds to neither. `docs/operations/rotation-executor.md` line 192 documents the procedure:
     "with two waiting, nothing binds; `rotation-worker stage` … still works for either". I didn't
     establish how the earlier same-day pairs cleared. Precedent: `revision-watcher` and `unit-verifier`
     tokens were both requested 2026-11-11, and `change-proposer` and `rotation-decomposer`
     tokens 2026-11-13. This is not a stop, but sitting 2 should expect it.
6. **Fixtures and tests.**
   - `test_the_repositorys_registry_registers_the_twelve_moved_lanes` adds `bump-proposer` and
     `rotation-proposer`. `lanes_in_registry` matches a `coolify-env` service consumer keyed
     `<LANE>_BWS_ACCESS_TOKEN` (`deploy_orchestrator.py`, line 1193), and both lanes are in
     `LANE_CHECKS` (lines 953 and 967).
   - `test_every_lanes_ping_url_secret_is_listed_for_that_lane`: both lanes stay in
     `placeholders`, and only the docstring changed.
   - The earlier per-lane rows tests list fixed `OTHER_LANE_TOKENS`, so the new rows don't
     change what they assert. About 35 test files name `.cred-consumers.toml` or `REGISTRY_PATH`.
     I didn't open each one; the full `make check` ran them all, and they pass.
   - Once this merges, `registered_lanes` (line 1256, reading `origin/main`) returns those twelve
     lanes. It has two callers, both in `scripts/deploy_orchestrator.py`.
     `require_lanes_current` (line 1283) checks a registered lane only through
     `last_finished(load_runs(...))`, so a lane with no passed `--add` holds nothing (docstring
     line 1269). `lanes_to_follow` (line 1299) keeps only lanes with a finished run. Neither lane
     has a recorded run, so nothing treats either one as registered before its token is issued
     and its `--add` passes. `lanes_in_registry` has no other caller in `scripts/` or `src/`.
7. **How the rows go live.**
   - The rotation proposer's Mac pass reads registry rows through `RepoMirror` at each
     repository's `origin/main` (`rotation_proposer/cli.py`, lines 909 to 941). It reads the due
     findings from `cred-findings` over the Mac's `cred-consumers.list`, which names the main
     tree's working copy.
   - The handoff states that the 03:00 scan reads that same working copy; I didn't read the Mac's
     list itself. A Mac launcher moves the main tree to `origin/main` with
     `activate_checkout "$REPO_ROOT"`. One example is `run-activation-sweep.sh` line 103, a job
     that stays on the Mac (`STAYS_ON_THE_MAC` in `test_deploy_lanes.py`). The rule is ADR-0031,
     "a merged change is not activated until the consumer pulls".
   - So the token rows are due on the first proposer pass after a Mac launcher fast-forwards the
     main tree past the merge.
8. **Membership check.** `src/membership_check/consistency.py` reads `rotated_by_sds` registry
   rows by repository and compares them with the `credential_rotation` grant (lines 175 to 197).
   orchestrator already has `rotated_by_sds` rows and is in `REGISTRY_REPOSITORIES`, so the
   repository set doesn't change, and neither does its pass.

## Tests and checks run

- `make check` with `TEST_DATABASE_URL` set to `orchestrator_test_hq_w_224`,
  `SECURITY_STANDARDS_DIR=tests/fixtures/security-standards` and `PGTZ=UTC` passed (rc 0): ruff
  reported "All checks passed!" and 1,092 files already formatted, pyright reported 0 errors, and
  the tests ran 15,779 passed, 1 skipped, 11 warnings.
- The targeted run (new test, `test_deploy_lanes.py` and both earlier rows tests) passed 155.
- The security scan from the repo root found 0 BLOCK, 2 WARN and 1 INFO.
  `bws.secret-files-gitignored` (`*-migration/`) and `bws.manifest-matches-usage` (`aaff45ca-…`,
  which appears only in reports on `origin/main`) both predate this diff. The INFO is the
  judgment prompt on machine-account scope.
- `/code-review` (low) on `origin/main...HEAD` returned no findings.

## Files changed

- `.cred-consumers.toml`
- `lanes/bump-proposer/compose.yaml` and `lanes/rotation-proposer/compose.yaml` (comments only)
- `tests/rotation_worker/test_proposer_lane_rows.py` (new)
- `tests/scripts/test_deploy_lanes.py`
- `docs/superpowers/reports/lanes-inc-4-proposers-rows-report.md` (this report)

## Self-review and deviations

- One deviation: item 6 extends the existing entry's note rather than adding a second
  `runtime-fetch` for the same `(repo, ref)`. The reason is in "What I built".
- The push App key row's comments were corrected beyond the handoff's list, because G-75 made
  them false.

## Concerns and follow-ups

1. **The inbox can't tell the bump proposer's Mac token from its lane token.** Both are tokens of
   the `bump-proposer` account, so they see the same reach. If a person pasted the Mac token into
   the inbox during the lane token's issue, `bind_refusal`'s reach check would accept it. The
   other-keeper check doesn't see the Mac token's keeper either, because that row isn't a
   lane-token row and isn't rotated by the SDS. The rule passes as the handoff asked, but sitting
   2 depends on the person minting a fresh token. This follows from G-75 reusing D-54's account.
2. `bws-bump-proposer-token`'s `provider_identity` still says the account "reads
   factory-events-append-password". That is stale since G-75, and I left it unchanged as the
   handoff directs. A later change can correct it when the Mac job retires.
3. Both token issues wait on a mint at once, so the inbox binds neither. `rotation-worker stage`
   is the documented way through (check 5).
4. The later change dates each ping URL's row once its token's `store` unit completes, then writes
   each id into its compose and removes the lane from `placeholders` (deploy.md items 7 and 8 for
   the bump proposer, items 7 and 8 for the rotation proposer).

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The diff is registry
  data, comments and tests.
- **Code Brain rule 4 (never swallow an exception silently):** the new test catches no exception.
  Every assertion fails loudly.
- **Code Brain rule 5 (never log secrets):** nothing added prints or holds a token, ping URL, ping
  UUID, bearer, key or password. The rows and the test name only BWS secret ids and Coolify
  resource ids. The `cred-findings` output I quote carries ids and dates only.
- **Infra Brain 591, 592 and 593:** no BWS token is in any tracked file or the history, and none is
  inline. The scan's BWS-token checks show 0 BLOCK, and the test uses no token-shaped values.
- **Infra Brain 2890:** no ping URL is committed. The ping rows name a check and a BWS secret id
  only.
- **Infra Brain 1032 (cred.exposure-rotate):** none of the four rows declares a
  `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. The change adds or alters no
  domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, the test,
  the plan or any log. The rows name ids only.
