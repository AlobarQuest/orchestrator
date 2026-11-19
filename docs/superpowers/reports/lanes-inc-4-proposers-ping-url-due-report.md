Outcome: done

# Build report: both proposers' ping-URL rows are due

Handoff: `lanes-inc-4-proposers-ping-url-due` (worker W-228). Branch
`hq/lanes-inc-4-proposers-ping-url-due`, base `origin/main` at `3fffa6e5`, as the handoff read.
PR: PR_PLACEHOLDER.

## What I built

`bump-proposer-ping-url` and `rotation-proposer-ping-url` take `rotate_requested = "2026-11-19"`,
so the proposer generates each one's first issue (deploy.md, the bump and rotation proposers'
cut-over item 5). Both lanes' block comments state the rule as it stands, and the row test asserts
all four rows carry their dates. The change mirrors #731 (`2b56b525`).

## Items 1 to 7

1. Rows: `.cred-consumers.toml:1289` (`bump-proposer-ping-url`, `bws_uuid` `786f75e2-…`) and
   `:1340` (`rotation-proposer-ping-url`, `bws_uuid` `506a5436-…`) each gain
   `rotate_requested = "2026-11-19"` straight after `rotated_by_sds = true`, the order the
   `work-carrier-ping-url` row uses. Neither row gains `created` or `last_rotated`.
2. Comments: both lanes' blocks (`:1247` and `:1300`) now read "Both are due for their first issue
   by `rotate_requested`. The ping URL's row is dated only after the token's `store` unit
   completed, so the two issue units never waited on a mint at once.", the work carrier's wording
   after #731 (`:1194`). The rest of each block is unchanged, including the bump proposer's note on
   the account's Mac token (`bws-bump-proposer-token`). The "Not yet due" line above each ping-URL
   row is removed; the "No `created`" line stays, as above `work-carrier-ping-url`. There's no
   correction history.
3. Tests: `test_only_the_token_row_is_due` became `test_both_rows_are_due`
   (`tests/rotation_worker/test_proposer_lane_rows.py:193`), parametrized over both lanes. It
   asserts the token row's `2026-11-18`, read from the file at the base (`:1261` and `:1313` there),
   and the ping-URL row's `2026-11-19`. The module docstring now says both rows are due. A grep of
   `tests/`, `src/` and `scripts/` for `proposer-ping-url` finds no other assertion that either
   proposer's ping-URL row isn't due: the other hits are the change proposer's rows
   (`test_change_proposer_rows.py`, which asserts both its rows due) and a comment in
   `tests/scripts/test_run_change_proposer.py:411`.
4. Two ping-URL issue units due at once don't meet the inbox's refusal:
   - The ping-URL plan is `derive_issue(...)` (`src/rotation_worker/adapters/healthchecks_ping_url.py:61`).
     Its segments, read in-process, are one `UnitPlan` `store` with steps `MINT, STAGE, VERIFY_NEW,
     STORE, VERIFY_CONSUMERS`, and no `HumanAct`.
   - The refusal is `inbox.bind`'s first test, `if others_waiting:`
     (`src/rotation_worker/inbox.py:149-154`). `others_waiting` is `other_mints`
     (`:120-135`), which lists a unit only when `person_mints_before` is true (`:130-132`).
     `person_mints_before` (`:106-117`) asks `person_acts_beside(plan, unit, -1, StepKind.MINT)`,
     which is true only for a preceding `HumanAct` holding a mint
     (`src/rotation_worker/plans.py:198-206`); for `store`, index −1 is out of range (`:203-204`),
     so it returns false. In-process, `person_mints_before("store", "healthchecks-ping-url")` is
     `False`. A ping-URL unit is never counted as waiting on a mint.
   - Nor does a ping-URL unit reach `bind`: `worker._mint_waits` returns `False` at its first test,
     `if not person_acts_beside(approved.plan, approved.unit, -1, StepKind.MINT)`
     (`src/rotation_worker/worker.py:645-646`), before it reads the inbox or calls `_bind_inbox`
     (`:757`). The unit's wait is `_read_mint_waits` (`:693`, called at `:468`), which reads the
     Healthchecks listing and never the inbox. Each ping-URL unit's read also refuses a URL another
     ping-URL keeper holds (`:711-719`), so the two checks can't cross.
   - So the two units don't block each other. They'd still block a third rotation's inbox bind
     only if they were person mints, which they aren't.
5. Issue plan: the proposer derives the plan with `first_issue=_first_issue(last_rotated,
   due.credential_id)` in `_generate` (`src/rotation_proposer/cli.py:444`, the call at `:462`) and
   in `_revision_body` (`:409`, `:423`). `_first_issue` (`:436-441`) returns `True` when infraops'
   `last_rotated` for the id is null; both runs in item 6 have no rotation recorded for either row.
   `test_a_first_issue_derives_the_issue_plan` (`test_proposer_lane_rows.py:226`) is parametrized
   over both lanes and over `token` and `ping_url`, and asserts `plan_for(row,
   first_issue=True)` yields an issue plan.
6. Due-ness:
   - Setup: infraops' `cred-findings` from a scratch `rsync` copy (no `.git`) of
     `~/Projects/infraops-mcp-server` at `c3359bf` (`dist/cli/security-drift-cli.js`), in
     `/tmp/w228-infraops.W5MS`. `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at a
     scratch directory per run, each holding a one-file list and an empty (`{}`, mode `0600`)
     rotation state, at `--now 2026-11-19T01:00:00Z`. I didn't touch the real list or the checkout,
     and built nothing.
   - Treated (this branch's file): exit 0 and 25 findings, all `cred.rotation-requested`,
     including
     `{'check': 'cred.rotation-requested', 'id': 'bump-proposer-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-19', 'rotated_by_sds': True}`
     and
     `{'check': 'cred.rotation-requested', 'id': 'rotation-proposer-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-19', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 23 findings, none for either row.
   - Treated minus control, as `(check, id)` pairs, is exactly those two; control minus treated is
     empty.
   - Neither run raised a parse error or `cred.invalid-rotate-requested`, so the file parses under
     infraops' TOML subset. Other rows show as due in both runs only because the scratch state is
     empty.
   - The source rule is `src/security-drift/cred-rotation.ts:279-290` in infraops.
7. Mutation check: I copied the file, removed each new `rotate_requested` line in turn by exact
   pattern, and ran the row tests. Removing the bump proposer's line gave 1 failed and 25 passed
   (`test_both_rows_are_due[bump-proposer]`, `KeyError: 'rotate_requested'` at `:197`); removing
   the rotation proposer's gave 1 failed and 25 passed (`test_both_rows_are_due[rotation-proposer]`,
   same error). After each I restored from the copy; the final run gave 26 passed, and `cmp`
   showed the file equal to the copy.

## Compose files at the base (out of scope)

`lanes/bump-proposer/compose.yaml:30` and `lanes/rotation-proposer/compose.yaml:32` both hold
`SDS_DEADMAN_PING_URL_UUID: ""`, with a comment (`:17`, `:19`) that a later change writes the id of
the lane's ping-URL keeper. I left both, and `test_deploy_lanes.py`'s `placeholders`, unchanged
(deploy.md item 6).

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_228`, `PGTZ=UTC`, `SECURITY_STANDARDS_DIR` at the
  test fixture, migrated to head first): exit 0. Lint passed, pyright reported 0 errors, and pytest gave 15779 passed, 1 skipped and 11 warnings in 312 s.
- Security scan from the repo root: exit 0, no BLOCK finding. It reported two WARNs
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and one INFO judgment
  (`bws.least-privilege-scope`). This diff touches neither `.gitignore` nor `.bws-secrets.toml`, so
  the WARNs come from the base.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`: two `rotate_requested` lines and the two lanes' comments.
- `tests/rotation_worker/test_proposer_lane_rows.py`: the module docstring and
  `test_both_rows_are_due`.
- `docs/superpowers/reports/lanes-inc-4-proposers-ping-url-due-report.md`: this report.

## Self-review

The diff is the #731 shape applied to two lanes. Item 4 is the one fact #731 didn't have to check;
the code shows a ping-URL unit is neither counted by `other_mints` nor routed to `bind`, so two due
at once is safe. A first `make check` attempt didn't run because `rtk` isn't on the background
shell's `PATH` (exit 127 before any target ran); the run reported here called `make` directly.

## Concerns

None. No logging changed, so Code Brain rule 3 isn't engaged.

## Follow-ups

- deploy.md item 6 for each lane: write the ping-URL keeper's id into the compose
  `SDS_DEADMAN_PING_URL_UUID` and update `test_deploy_lanes.py`'s `placeholders`, once each issue's
  `store` unit completes.
- Teardown: drop `orchestrator_test_hq_w_228` and remove `/tmp/w228-infraops.W5MS` after the PR
  merges (HQ's call).

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout, not files): no logging change; the diff is a
  registry file, comments and a test.
- Code Brain rule 4 (never swallow an exception silently): no helper or handler added; the changed
  test asserts by key lookup, so a missing date raises `KeyError` and fails, as item 7 shows.
- Code Brain rule 5 (never log secrets): nothing added prints a token, a ping URL, or the UUID
  inside a ping URL. The rows name BWS secret ids only, which aren't the ping URLs' UUIDs.
- Infra Brain 591, 592, 593: no BWS token in any tracked file or the branch's history, and no
  bootstrap token inline; the scan found no BLOCK. The diff adds no test values.
- Infra Brain 2890: no ping URL committed; the diff contains no `healthcheck.alobar.net/ping/`
  string (grep count 0).
- Infra Brain 1032 (cred.exposure-rotate): neither row declares a `[[credential.exposure]]`.
- Infra Brain 212 (HTTPS for public FQDNs): not applicable; no domain change.
- Infra Brain lesson 450 (rotation landmines): no secret value enters the registry, a plan, a
  test, or a log; the rows name ids and a check name only.
