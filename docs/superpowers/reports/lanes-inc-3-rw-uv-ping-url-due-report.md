Outcome: done

# Build report: the revision watcher's and unit verifier's ping-URL rows are due

Handoff: `lanes-inc-3-rw-uv-ping-url-due` (ADR-0055 amendment 19 item 8, step 5 of each lane's
cut-over in `docs/operations/deploy.md`). Worker W-113.

- PR: PR_NUMBER, ready for review, not merged.
- Branch: `hq/lanes-inc-3-rw-uv-ping-url-due`, base `origin/main` at `3df369b2`. The change commit
  is `bb29c4df`; the PR head is the branch head, the commit that adds this report.

Every fact the handoff cites matched its source at `3df369b2`: the two ping-URL rows and their
`bws_uuid` values, the two block comments, the deploy watcher's model block and row, and
`test_only_the_token_row_is_due` in both test modules. infraops' rotation state independently
confirms the trigger: it serves `last_rotated` `2026-11-12T00:00:00.000Z` for both
`revision-watcher-bws-token` and `unit-verifier-bws-token` (item 5).

## What I built

1. **The rows are dated.** `revision-watcher-ping-url` (`.cred-consumers.toml:904`) and
   `unit-verifier-ping-url` (`.cred-consumers.toml:954`) each take `rotate_requested = "2026-11-12"`
   directly after `rotated_by_sds = true`, the order `deploy-watcher-ping-url` uses. Neither gains
   `created` or `last_rotated`.
2. **The comments state the rule as it stands.** The revision watcher's block
   (`.cred-consumers.toml:865-869`) and the unit verifier's (`:915-919`) now carry the deploy
   watcher's wording: both rows are due for their first issue by `rotate_requested`, and the ping
   URL's row is dated only after the token's `store` unit completed. Each keeps its gate sentence
   (G-30, G-33). The pin watcher's and membership check's comments are unchanged.
3. **The tests assert both dates.** In each module, `test_only_the_token_row_is_due` is replaced by
   `test_both_rows_are_due` (`test_revision_watcher_rows.py:114`, `test_unit_verifier_rows.py:117`):
   token `2026-11-11` (read from the file at base, both token rows) and ping URL `2026-11-12`. Each
   module docstring now says both rows are handed to the SDS and due. A grep of `tests/` for
   `rotate_requested" not in` and for both ping-URL ids finds only the pin watcher's (`:122`) and
   membership check's (`:123`) assertions, which are out of scope. `tests/rotation_proposer/` and
   `tests/fixtures/` don't name either row.
4. **The proposer takes each row as a first issue.** `_first_issue`
   (`src/rotation_proposer/cli.py:431-436`) returns `True` when infraops serves the row's
   `last_rotated` as null; both call sites (`cli.py:418`, the revision path, and `cli.py:457`,
   `_generate`) pass it to the plan. infraops serves null for both ping-URL rows (item 5), since
   neither has an age anchor. `test_a_first_issue_derives_the_issue_plan[<ping-url id>]`
   (`test_revision_watcher_rows.py:131`, `test_unit_verifier_rows.py:145`) passes for both.
5. **Due-ness, measured with infraops' own code.** I ran `dist/cli/security-drift-cli.js
   cred-findings` from a scratch copy, `/tmp/w113-credfind.omln`, holding `dist`, `package.json`,
   and a symlink to `node_modules`. The infraops checkout is at `c3359bf` with a clean status; its
   `dist/security-drift/cred-rotation.js` was built November 7, 2026. I didn't edit or build the
   checkout. `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only one registry file, and
   `SECURITY_DRIFT_STATE_DIR` at a copy of `~/.config/infra-drift/cred-rotation-state.json`
   (modified 2026-11-12 14:26 local). I didn't use `~/.config/infra-drift/cred-consumers.list`.
   - Over this branch's file, exit 0, and among the four lane rows:
     - `{'check': 'cred.rotation-requested', 'id': 'revision-watcher-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-12', 'rotated_by_sds': True}`
     - `{'check': 'cred.rotation-requested', 'id': 'unit-verifier-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-12', 'rotated_by_sds': True}`
     - `last_rotated`: both tokens `2026-11-12T00:00:00.000Z`, both ping URLs `None`.
   - Control, over `origin/main`'s file: exit 0, no finding for any of the four rows, and the same
     `last_rotated`. Both ping-URL findings come from this change; the tokens are no longer due
     because infraops records them rotated.

   Both runs load the file through infraops' `loadCredConsumerFiles`, which throws on a registry
   that doesn't parse, so the file still parses under its TOML subset.
6. **Mutation check.** I copied the file to `/tmp/w113-registry.bak` and, for each row in turn,
   removed only its new line, anchored on the row's keeper id with a single-match guard. Each mutant
   failed exactly the matching test:
   - Revision watcher: `FAILED test_revision_watcher_rows.py::test_both_rows_are_due` (1 failed,
     12 passed).
   - Unit verifier: `FAILED test_unit_verifier_rows.py::test_both_rows_are_due` (1 failed, 15
     passed).

   After each, I restored from the copy and cleared `__pycache__`; `git diff` showed the file
   matched the commit, and both modules passed (29 passed).

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_113`: exit 0. Ruff "All checks
  passed!", pyright "0 errors, 0 warnings", pytest "14695 passed, 1 skipped, 11 warnings".
- Security scan from the repo root: 0 BLOCK, 2 WARN (`bws.secret-files-gitignored`,
  `bws.manifest-matches-usage`), 1 INFO (`bws.least-privilege-scope`). None is in a changed line.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_revision_watcher_rows.py`
- `tests/rotation_worker/test_unit_verifier_rows.py`
- `docs/superpowers/reports/lanes-inc-3-rw-uv-ping-url-due-report.md` (this report)

## Self-review

The diff is #661's shape applied to two lanes: one row date, one comment rewrite, and one test
replaced per lane. The mutations show each new test detects its date's absence, and the infraops
control shows both findings come from the new lines.

## Concerns and follow-ups

- Out of scope, as the handoff says: each compose's `SDS_DEADMAN_PING_URL_UUID` and the two lanes'
  entries in `test_deploy_lanes.py`'s `placeholders` (deploy.md step 6), and the pin watcher's and
  membership check's ping-URL rows.
- Teardown after merge: `/tmp/w113-credfind.omln`, `/tmp/w113-registry.bak`, `/tmp/w113-env.sh`,
  `/tmp/w113-check.log`, the test database `orchestrator_test_hq_w_113`, and the worktree.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout, not files):** the change adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the rewritten tests have no `try`
  and catch nothing; a missing key fails them, as the mutations showed.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, or a ping
  URL's UUID. The `bws_uuid` values in the rows are BWS secret ids, distinct from the UUID inside a
  ping URL.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token to a tracked file or history, no
  bootstrap token inline, and no token-shaped test values; the tests compare dates only. The
  security scan reports 0 BLOCK.
- **Infra Brain 2890 (never commit a ping URL):** the rows name their checks by slug
  (`sds-revision-watcher`, `sds-unit-verifier`, unchanged), and the diff contains no
  `https://healthcheck.alobar.net/ping/` URL.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares a `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable; the change touches no domain.
- **Infra Brain lesson 450 (rotation landmines):** no secret value enters the registry, a plan, a
  test, or a log; the rows name ids and a date only, and the `cred-findings` output quoted in item
  5 carries ids, classes, dates, and flags.
