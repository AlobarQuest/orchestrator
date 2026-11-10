Outcome: done

# Report: make the landing ledger's ping-URL row due

PR: [AlobarQuest/orchestrator#645](https://github.com/AlobarQuest/orchestrator/pull/645), branch
`hq/lanes-inc-3-landing-ledger-ping-url-due`, base `origin/main` at `21ce13b0`. Code commit
`7ceef21f`; this report is the commit after it (the PR head). The PR is ready for review (not a
draft) and isn't merged.

## What I built

The `landing-ledger-ping-url` row is due for its first issue, by ADR-0055 amendment 19 item 8
step 5. The change mirrors #641 (`fb485d2c`) for execution-watch. HQ's handoff states the
precondition: the token's `store` unit completed, and no `rotation-mint` or
`rotation-unit-failed` alert is open. I confirmed the first half read-only:
`~/Library/Logs/unit-verifier.log:5747` reads `landing-ledger-bws-token-store completed`.

## Items 1-6

1. **Registry row.** `.cred-consumers.toml:769` adds `rotate_requested = "2026-11-10"` after
   `rotated_by_sds = true`, the token row's order. Neither row gains `created` or `last_rotated`.
2. **Comments.** The block at `.cred-consumers.toml:728-735` states the rule as it stands, in the
   execution-watch block's wording: both rows are due for their first issue by `rotate_requested`,
   and the ping URL's row was dated only after the token's `store` unit completed, so the two
   issue units never waited on a mint at once. The comment over the ping-URL row (`:762`) keeps
   only "No `created`, for the reason the block above gives." The text holds no correction
   history.
3. **Test.** `tests/rotation_worker/test_landing_ledger_rows.py:98-103`: `test_both_rows_are_due`
   replaces `test_only_the_token_row_is_due`. It asserts the token's `2026-11-09` and the ping
   URL's `2026-11-10`, and its docstring keeps the reason. The module docstring now says both
   rows are due. A grep of `tests/`, `src/`, and `scripts/` for `landing-ledger-ping-url` found no
   other assertion that the row isn't due.
4. **Proposer first issue.** `test_a_first_issue_derives_the_issue_plan[landing-ledger-ping-url]`
   (`test_landing_ledger_rows.py:114-119`) passes. `src/rotation_proposer/cli.py:413-418`
   `_first_issue` returns `True` when infraops serves `last_rotated` as null for the id; both
   `_revision_body` (`:400`) and `_generate` (`:439`) pass that value to the plan. The row has no
   `created`, and infraops serves `last_rotated: null` for it (item 5), so the proposer takes it
   as a first issue with no anchor.
5. **Due-ness, measured.** I ran infraops' `security-drift-cli cred-findings` from a scratch copy
   (`/tmp/w64-credfind.T5qE`) holding `dist`, `package.json`, and a symlink to `node_modules`.
   The checkout is at `c3359bf`, and its `dist/security-drift/cred-rotation.js` was built
   November 7, 2026, after that commit, and contains both new classes. I didn't edit or build the
   checkout. `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only this worktree's file,
   and `SECURITY_DRIFT_STATE_DIR` at a copy of `~/.config/infra-drift/cred-rotation-state.json`
   (modified 2026-11-10 11:30 UTC). I didn't use `~/.config/infra-drift/cred-consumers.list`.
   The run exited 0 and printed these landing-ledger results:
   - `{'check': 'cred.rotation-requested', 'id': 'landing-ledger-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-10', 'rotated_by_sds': True}`
   - `{'check': 'cred.rotation-requested', 'id': 'landing-ledger-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-09', 'rotated_by_sds': True}`
   - `last_rotated`: `{'landing-ledger-bws-token': None, 'landing-ledger-ping-url': None}`

   For the control, the same command over `origin/main`'s file listed only
   `landing-ledger-bws-token`, so the ping-URL finding comes from this change.

   **The token row still lists, with `last_rotated` null.** The state copy holds no recorded
   rotation for `landing-ledger-bws-token`. For comparison, it serves `2026-11-09T00:00:00.000Z`
   for both execution-watch rows, which no longer list. So the token's `store` completed but its
   `record-rotation` hadn't reached infraops' state when the state file was last written (11:30 UTC). I reported this and didn't
   change it.

   **Parse.** `doCredFindings` throws on a registry that doesn't parse
   (`src/cli/security-drift-cli.ts` docstring over `doCredFindings`), and both runs exited 0, so
   the file still parses under infraops' TOML subset.
6. **Mutation check.** I backed up `.cred-consumers.toml` to `/tmp/w64-registry.bak` and deleted
   the new line. `test_both_rows_are_due` failed (1 failed, 10 passed). After I restored from the
   copy, all 11 passed and `git diff --stat` matched the intended change.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_64` (`PGTZ=UTC`) exited 0: ruff passed, pyright
  reported 0 errors, and pytest reported **14207 passed, 1 skipped**.
- After a docstring reflow, `test_landing_ledger_rows.py` passed (11), and ruff check and format
  passed on the file.
- Security scan from the repo root: **0 BLOCK**, 2 WARN, and 1 INFO. The WARNs are
  `glob not covered: *-migration/` and `undeclared UUID in code: aaff45ca-…`, the same two #641's
  report recorded. Neither comes from this diff.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_landing_ledger_rows.py`
- `docs/superpowers/reports/lanes-inc-3-landing-ledger-ping-url-due-report.md` (this report)

## Self-review

The diff is two comment rewrites, one registry line, one test rewritten, and a module docstring.
The `cred-findings` run and its `origin/main` control measure due-ness with infraops' own code.
The change adds no logging.

## Concerns and follow-ups

- infraops' rotation state has no entry for `landing-ledger-bws-token`, so infraops keeps
  reporting `cred.rotation-requested` for it until the proposer records the rotation
  (`_record_retired`, `src/rotation_proposer/cli.py:550`). The same state records both
  execution-watch rows rotated on 2026-11-09, so that path has recorded the sibling lane.
- Out of scope, as the handoff says: the `SDS_DEADMAN_PING_URL_UUID` compose change and its
  `.bws-secrets.toml` row.
- Teardown after merge: the scratch directory `/tmp/w64-credfind.T5qE`, the backup
  `/tmp/w64-registry.bak`, the test database `orchestrator_test_hq_w_64`, and the worktree.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the change adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the rewritten test has no `try`
  and catches nothing; a failure fails the assertion.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, or a ping
  URL's UUID. The `bws_uuid` values in the rows are BWS secret ids, which differ
  from a ping URL's UUID.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token to any tracked file or to history, and
  the test adds no test values. The security scan reports 0 BLOCK.
- **Infra Brain 2890:** the diff and this report contain no `https://healthcheck.alobar.net/ping/`
  URL.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`, so
  the rule doesn't apply.
- **Infra Brain lesson 450 (rotation landmines):** the registry, test, and report name ids and
  dates only. No secret value enters them.
