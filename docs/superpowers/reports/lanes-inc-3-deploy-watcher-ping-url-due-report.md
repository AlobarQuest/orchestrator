Outcome: done

# Build report: the deploy watcher's ping-URL row is due

Handoff: `lanes-inc-3-deploy-watcher-ping-url-due` (ADR-0055 amendment 19 item 8, step 5 of the
deploy watcher's cut-over in `docs/operations/deploy.md`). Worker W-88.

- PR: #661 (https://github.com/AlobarQuest/orchestrator/pull/661), ready for review, not merged.
- Branch: `hq/lanes-inc-3-deploy-watcher-ping-url-due`, base `origin/main` at `2b5a18d6`. The
  change commit is `a4513fce`; the PR head is the branch head, which is the commit that adds this
  report.

Every fact the handoff cites matched its source at `2b5a18d6`: the ping URL's row at
`.cred-consumers.toml` ~815-823, the block comment at ~782-786, the landing ledger's model comment
at ~729-735, and `test_only_the_token_row_is_due` at ~109-112.

## What I built

1. **The row is dated.** `deploy-watcher-ping-url` takes `rotate_requested = "2026-11-11"` after
   `rotated_by_sds = true`, the order the landing ledger's ping-URL row uses
   (`.cred-consumers.toml:822`). Neither row gains `created` or `last_rotated`.
2. **The comment states the rule as it stands.** The deploy watcher's block comment
   (`.cred-consumers.toml:782-786`) now reads that both rows are due for their first issue by
   `rotate_requested`, and that the ping URL's row is dated only after the token's `store` unit
   completed, so the two issue units never waited on a mint at once. It keeps the G-28 keeper
   sentence and carries no correction history.
3. **The test asserts both dates.** `test_only_the_token_row_is_due` is replaced by
   `test_both_rows_are_due` (`tests/rotation_worker/test_deploy_watcher_rows.py:108-112`): token
   `2026-11-10`, ping URL `2026-11-11`, as #645 did for the landing ledger. The module docstring
   (lines 1-5) now says both rows are handed to the SDS and due. A grep of `tests/` for
   `rotate_requested" not in` finds one other assertion, at
   `tests/rotation_worker/test_revision_watcher_rows.py:118`. It's about the revision watcher's
   ping-URL row, which the handoff puts out of scope, so I left it. No other test references
   `deploy-watcher-ping-url`'s due-ness.
4. **The proposer takes the row as a first issue.** `_first_issue`
   (`src/rotation_proposer/cli.py:413-418`) returns `True` when infraops serves the row's
   `last_rotated` as null, and both `_generate` (`cli.py:439`) and the revision path (`cli.py:400`)
   pass that to the plan. infraops serves null for this row (item 5), because the row has no
   `created` or `last_rotated` anchor. `test_a_first_issue_derives_the_issue_plan[deploy-watcher-ping-url]`
   (`test_deploy_watcher_rows.py:124`) passes on the branch.
5. **Due-ness, measured with infraops' own code.** I ran `security-drift-cli cred-findings` from a
   scratch copy, `/tmp/w88-credfind.NqQB`, holding `dist`, `package.json`, and a symlink to
   `node_modules`. The infraops checkout is at `c3359bf`; its `dist/security-drift/cred-rotation.js`
   was built November 7, 2026, and contains `healthchecks-ping-url`. I didn't edit or build the
   checkout. `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only one registry file, and
   `SECURITY_DRIFT_STATE_DIR` at a copy of `~/.config/infra-drift/cred-rotation-state.json`
   (modified 2026-11-10 19:38 UTC). I didn't use `~/.config/infra-drift/cred-consumers.list`.
   - Over this branch's file, the run exited 0 and listed:
     - `{'check': 'cred.rotation-requested', 'id': 'deploy-watcher-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-10', 'rotated_by_sds': True}`
     - `{'check': 'cred.rotation-requested', 'id': 'deploy-watcher-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-11', 'rotated_by_sds': True}`
     - `last_rotated`: `{'deploy-watcher-bws-token': None, 'deploy-watcher-ping-url': None}`
   - Control, over `origin/main`'s file: exit 0, and only the `deploy-watcher-bws-token` finding.
     The ping-URL finding comes from this change.

   Both runs load the file through infraops' `loadCredConsumerFiles`, so the file still parses
   under its TOML subset.
6. **Mutation check.** I copied the file to `/tmp/w88-registry.bak` and removed only the new line,
   anchored on the row's keeper id. A first attempt anchored on `rotated_by_sds = true` plus the
   date matched twice (the revision watcher's token row is also dated `2026-11-11`), so its guard
   aborted before writing. The mutated run failed one test:
   `FAILED test_deploy_watcher_rows.py::test_both_rows_are_due` with
   `KeyError: 'rotate_requested'` (1 failed, 11 passed). After I restored from the copy, the file
   matched the commit and the module passed (12 passed).

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_88`: exit 0. Ruff "All checks
  passed!", pyright "0 errors, 0 warnings", pytest "14377 passed, 1 skipped, 11 warnings".
- Security scan from the repo root: 0 BLOCK, 2 WARN (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`), 1 INFO (`bws.least-privilege-scope`). None is in a changed file.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_deploy_watcher_rows.py`
- `docs/superpowers/reports/lanes-inc-3-deploy-watcher-ping-url-due-report.md` (this report)

## Self-review

The diff is the #645 shape applied to the deploy watcher: one row date, one comment rewrite, one
test replaced. The mutation shows the new test detects the date's absence, and the infraops control
shows the finding comes from the new line.

## Concerns and follow-ups

- Out of scope, as the handoff says: the compose's `SDS_DEADMAN_PING_URL_UUID`, the removal of
  `deploy-watcher` from `test_deploy_lanes.py`'s `placeholders` (deploy.md step 6), and the
  revision watcher's and unit verifier's ping-URL rows.
- Teardown after merge: the scratch directory `/tmp/w88-credfind.NqQB`, the backup
  `/tmp/w88-registry.bak`, the env file `/tmp/w88-env.sh`, the test database
  `orchestrator_test_hq_w_88`, and the worktree.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** the change adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the rewritten test has no `try` and
  catches nothing; a missing key fails it, as the mutation showed.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, or a ping
  URL's UUID. The `bws_uuid` values in the rows are BWS secret ids, which are distinct from the
  UUID inside a ping URL.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token to a tracked file or to history, no
  bootstrap token inline, and no token-shaped test values; the test compares dates only. The
  security scan reports 0 BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** the diff names the Healthchecks check by its slug
  `sds-deploy-watcher` (unchanged) and contains no `https://healthcheck.alobar.net/ping/` URL.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; the change touches no domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test, or a log; the rows name ids and a date only, and the `cred-findings` output quoted in item
  5 carries ids, classes, and dates.
