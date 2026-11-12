Outcome: done

# Build report: the pin watcher's ping-URL row is due

Handoff: `lanes-inc-3-pin-watcher-ping-url-due` (ADR-0055 amendment 19 item 8, step 5 of the pin
watcher's cut-over in `docs/operations/deploy.md`). Worker W-116.

- PR: #681 (https://github.com/AlobarQuest/orchestrator/pull/681), ready for review, not merged.
- Branch: `hq/lanes-inc-3-pin-watcher-ping-url-due`, base `origin/main` at `aff18d25`. The change
  commit is `b8278180`; the PR head is the branch head, the commit that adds this report.

Every fact the handoff cites matched its source at `aff18d25`: the `pin-watcher-ping-url` row and
its `bws_uuid`, the block comment ("a later change dates the ping URL's row"), the
`deploy-watcher-ping-url` model row, and `test_only_the_token_row_is_due`. infraops' rotation state
independently confirms the trigger: it serves `last_rotated` `2026-11-12T00:00:00.000Z` for
`pin-watcher-bws-token` (item 5).

## What I built

1. **The row is dated.** `pin-watcher-ping-url` (`.cred-consumers.toml:999`) takes
   `rotate_requested = "2026-11-12"` (`:1004`) directly after `rotated_by_sds = true`, the order
   `deploy-watcher-ping-url` uses. It gains no `created` or `last_rotated`.
2. **The comment states the rule as it stands.** The pin watcher's block
   (`.cred-consumers.toml:964-967`) carries the revision watcher's and deploy watcher's wording:
   both rows are due for their first issue by `rotate_requested`, and the ping URL's row is dated
   only after the token's `store` unit completed. It keeps its gate sentence (G-37). The membership
   check's comment is unchanged.
3. **The test asserts both dates.** `test_only_the_token_row_is_due` is replaced by
   `test_both_rows_are_due` (`tests/rotation_worker/test_pin_watcher_rows.py:118`): token
   `2026-11-12` (read from the file at base) and ping URL `2026-11-12`. The module docstring now
   says both rows are handed to the SDS and due. A grep of `tests/` for `rotate_requested" not in`
   finds only the membership check's assertion (`test_membership_check_rows.py:123`), which is out
   of scope; no other test names `pin-watcher-ping-url`.
4. **The proposer takes the row as a first issue.** `_first_issue`
   (`src/rotation_proposer/cli.py:431-436`) returns `True` when infraops serves the row's
   `last_rotated` as null; both call sites (`cli.py:418`, the revision path, and `cli.py:457`,
   `_generate`) pass it to the plan. infraops serves null for `pin-watcher-ping-url` (item 5).
   `test_a_first_issue_derives_the_issue_plan[pin-watcher-ping-url]`
   (`test_pin_watcher_rows.py:146`) passes.
5. **Due-ness, measured with infraops' own code.** I ran `dist/cli/security-drift-cli.js
   cred-findings` from a scratch copy, `/tmp/w116-credfind.Bu0v`, holding `dist`, `package.json`,
   and a symlink to `node_modules`. The infraops checkout is at `c3359bf` with a clean status; I
   didn't edit or build it. `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only one
   registry file, and `SECURITY_DRIFT_STATE_DIR` at a copy of
   `~/.config/infra-drift/cred-rotation-state.json` (modified 2026-11-12 16:17 local). I didn't use
   `~/.config/infra-drift/cred-consumers.list`.
   - Over this branch's file, exit 0. Findings: `membership-check-bws-token`,
     `pin-watcher-ping-url`, `revision-watcher-ping-url`, `unit-verifier-ping-url`. The new one:
     `{'check': 'cred.rotation-requested', 'id': 'pin-watcher-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-12', 'rotated_by_sds': True}`.
   - Control, over `origin/main`'s file: exit 0, the same findings without `pin-watcher-ping-url`.
   - `last_rotated` in both runs: `pin-watcher-bws-token` `2026-11-12T00:00:00.000Z`,
     `pin-watcher-ping-url` `None`.

   The rule is `src/security-drift/cred-rotation.ts:279-290`. Both runs load the file through
   `loadCredConsumerFiles` (`src/cli/security-drift-cli.ts:136`), which throws on a registry that
   doesn't parse, so the file still parses under infraops' TOML subset.
6. **Mutation check.** I copied the file to `/tmp/w116-registry.bak` and removed only the new line,
   anchored on the row's keeper id with a single-match guard. The mutant failed exactly the matching
   test: `FAILED tests/rotation_worker/test_pin_watcher_rows.py::test_both_rows_are_due` (1 failed,
   16 passed). I restored from the copy and cleared `__pycache__`; `cmp` showed the file matched the
   copy, and the module passed (17 passed).

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_116`: exit 0. Ruff "All checks
  passed!", pyright "0 errors, 0 warnings, 0 informations", pytest "14715 passed, 1 skipped, 11
  warnings".
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO, the same pre-existing repository-level
  findings W-113 reported (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`,
  `bws.least-privilege-scope`). None is in a changed line.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_pin_watcher_rows.py`
- `docs/superpowers/reports/lanes-inc-3-pin-watcher-ping-url-due-report.md` (this report)

## Self-review

The diff is #679's shape applied to one lane: one row date, one comment rewrite, and one test
replaced. The mutation shows the new test detects the date's absence, and the infraops control
shows the finding comes from the new line.

## Concerns and follow-ups

- Out of scope, as the handoff says: the compose's `SDS_DEADMAN_PING_URL_UUID` and the lane's entry
  in `test_deploy_lanes.py`'s `placeholders` (deploy.md step 6), and the membership check's rows.
- Teardown after merge: `/tmp/w116-credfind.Bu0v`, `/tmp/w116-registry.bak`, `/tmp/w116-env.sh`,
  `/tmp/w116-check.log`, `/tmp/w116-scan.log`, `/tmp/w116-mut.log`, `/tmp/w116-sync.log`,
  `/tmp/w116-alembic.log`, the test database `orchestrator_test_hq_w_116`, and the worktree.

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
  (`sds-pin-watcher`, unchanged), and the diff contains no `https://healthcheck.alobar.net/ping/`
  URL.
- **Infra Brain 1032 (cred.exposure-rotate):** the row declares no `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; the change touches no domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test, or a log; the row names ids and a date only, and the `cred-findings` output quoted in item
  5 carries ids, classes, dates, and flags.
