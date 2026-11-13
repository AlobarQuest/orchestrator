Outcome: done

# Build report: the membership check's ping-URL row is due

Handoff: `lanes-inc-3-membership-check-ping-url-due` (ADR-0055 amendment 19 item 8, step 5 of the
membership check's cut-over in `docs/operations/deploy.md`). Worker W-122.

- PR: #684 (https://github.com/AlobarQuest/orchestrator/pull/684), ready for review, not merged.
- Branch: `hq/lanes-inc-3-membership-check-ping-url-due`, base `origin/main` at `54982342`. The
  change commit is `8d52af58`; the PR head is the branch head, the commit that adds this report.

Every fact the handoff cites matched its source at `54982342`: the `membership-check-ping-url` row
and its `bws_uuid`, the block comment ("a later change dates the ping URL's row"), the
`pin-watcher-ping-url` model row, and `test_only_the_token_row_is_due`. infraops' rotation state
independently confirms the trigger: it serves `last_rotated` `2026-11-13T00:00:00.000Z` for
`membership-check-bws-token` (item 5).

## What I built

1. **The row is dated.** `membership-check-ping-url` (`.cred-consumers.toml:1049`) takes
   `rotate_requested = "2026-11-13"` (`:1054`) directly after `rotated_by_sds = true`, the order
   `pin-watcher-ping-url` uses. It gains no `created` or `last_rotated`.
2. **The comment states the rule as it stands.** The membership check's block
   (`.cred-consumers.toml:1014-1019`) carries the pin watcher's wording: both rows are due for their
   first issue by `rotate_requested`, and the ping URL's row is dated only after the token's
   `store` unit completed. It keeps its gate sentence (G-39, the lane account's four reads). No
   correction history.
3. **The test asserts both dates.** `test_only_the_token_row_is_due` is replaced by
   `test_both_rows_are_due` (`tests/rotation_worker/test_membership_check_rows.py:119`): token
   `2026-11-12` (read from the file at base) and ping URL `2026-11-13`. The module docstring now
   says both rows are handed to the SDS and due. A grep of `tests/` for `rotate_requested" not in`
   finds nothing after the change, and no other test names `membership-check-ping-url`.
4. **The proposer takes the row as a first issue.** `_first_issue`
   (`src/rotation_proposer/cli.py:431-436`) returns `True` when infraops serves the row's
   `last_rotated` as null; both call sites (`cli.py:418`, the revision path, and `cli.py:457`,
   `_generate`) pass it to the plan. infraops serves null for `membership-check-ping-url` (item
   5). `test_a_first_issue_derives_the_issue_plan[membership-check-ping-url]`
   (`test_membership_check_rows.py:147`) passes.
5. **Due-ness, measured with infraops' own code.** I ran `dist/cli/security-drift-cli.js
   cred-findings` from a scratch copy, `/tmp/w122-credfind.ELbl`, holding `dist`, `package.json`,
   and a symlink to `node_modules`. The infraops checkout is at `c3359bf` with a clean status; I
   didn't edit or build it. `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only one
   registry file, and `SECURITY_DRIFT_STATE_DIR` at a copy of
   `~/.config/infra-drift/cred-rotation-state.json` (modified 2026-11-12 21:36 local). I didn't use
   `~/.config/infra-drift/cred-consumers.list`. The run was at 2026-11-13 02:48 UTC, after the
   requested date, which the rule requires (`nowMs >= requestedMs`).
   - Over this branch's file, exit 0. One finding:
     `{'check': 'cred.rotation-requested', 'id': 'membership-check-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-13', 'rotated_by_sds': True}`.
   - Control, over `origin/main`'s file: exit 0, no findings.
   - `last_rotated` in both runs: `membership-check-bws-token` `2026-11-13T00:00:00.000Z`,
     `membership-check-ping-url` `None`.

   The rule is `src/security-drift/cred-rotation.ts:279-290`. Both runs load the file through
   `loadCredConsumerFiles` (`src/cli/security-drift-cli.ts:136`), which throws on a registry that
   doesn't parse, so the file still parses under infraops' TOML subset.
6. **Mutation check.** I copied the file to `/tmp/w122-registry.bak` and removed only the new line,
   anchored on the row's keeper id with a single-match guard, and cleared `__pycache__`. The mutant
   failed exactly the matching test:
   `FAILED tests/rotation_worker/test_membership_check_rows.py::test_both_rows_are_due` (1 failed,
   17 passed). I restored from the copy and cleared `__pycache__`; `cmp` showed the file matched
   the copy, `git status` was clean, and the module passed (18 passed).

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_122`: exit 0. Ruff "All checks
  passed!", pyright "0 errors, 0 warnings, 0 informations", pytest "14735 passed, 1 skipped, 11
  warnings".
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO, the same pre-existing
  repository-level findings W-116 reported (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`, `bws.least-privilege-scope`). None is in a changed line.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_membership_check_rows.py`
- `docs/superpowers/reports/lanes-inc-3-membership-check-ping-url-due-report.md` (this report)

## Self-review

The diff is #681's shape applied to this lane: one row date, one comment rewrite, and one test
replaced. The two rows carry different dates (token 2026-11-12, ping URL 2026-11-13), which matches
the rule: the ping URL's row is dated the day the token's issue was recorded. The mutation shows
the new test detects the date's absence, and the infraops control shows the finding comes from the
new line.

## Concerns and follow-ups

- Out of scope, as the handoff says: the compose's `SDS_DEADMAN_PING_URL_UUID`
  (`lanes/membership-check/compose.yaml`) and the lane's entry in `test_deploy_lanes.py`'s
  `placeholders` (deploy.md step 6).
- Teardown after merge: `/tmp/w122-credfind.ELbl`, `/tmp/w122-registry.bak`, `/tmp/w122-env.sh`,
  `/tmp/w122-check.log`, `/tmp/w122-scan.log`, `/tmp/w122-sync.log`, `/tmp/w122-alembic.log`, the
  test database `orchestrator_test_hq_w_122`, and the worktree.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** the change adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the rewritten test has no `try` and
  catches nothing; a missing key fails it, as the mutation showed.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, or a ping
  URL's UUID. The `bws_uuid` in the row is a BWS secret id, distinct from the UUID inside a ping
  URL.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token to a tracked file or history, no
  bootstrap token inline, and no token-shaped test values; the test compares dates only. The
  security scan reports 0 BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** the row names its check by slug
  (`sds-membership-check`, unchanged), and the diff contains no `https://healthcheck.alobar.net/ping/`
  URL.
- **Infra Brain 1032 (cred.exposure-rotate):** the row declares no `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; the change touches no domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test, or a log; the row names ids and a date only, and the `cred-findings` output quoted in item
  5 carries ids, classes, dates, and flags.
