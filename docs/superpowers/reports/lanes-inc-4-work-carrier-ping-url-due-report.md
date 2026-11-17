Outcome: done

# Report: the work carrier's ping-URL row is due

PR #731, branch `hq/lanes-inc-4-work-carrier-ping-url-due`, base `origin/main` at `6522a174`
(confirmed after `git fetch` in the worktree). Code commit `f6f8ba40`; this report is committed after
it. The PR is open and ready, not merged.

## What I built

1. In `.cred-consumers.toml`, the `work-carrier-ping-url` row (`bws_uuid` `55fbae20-…`) takes
   `rotate_requested = "2026-11-17"` at `:1236`, after `rotated_by_sds = true`. That is the order
   the `rotation-decomposer-ping-url` row uses (`:1182-1183`). The row has no `created` and no
   `last_rotated`.
2. The work carrier's block comment (`:1194-1198`) states the rule as it stands, in the rotation
   decomposer block's words after #695: both rows are due by `rotate_requested`, and the ping
   URL's row is dated only after the token's `store` unit completed. No correction history. The
   rest of the comment is unchanged; I reflowed its G-67 sentence onto the lines the new text
   left.
3. In `tests/rotation_worker/test_work_carrier_rows.py`, `test_both_rows_are_due` (`:129`)
   replaces `test_only_the_token_row_is_due`. It asserts the token row's date as read from the file
   at the base (`2026-11-16`, `:132`) and the ping-URL row's date (`2026-11-17`, `:133`). The
   module docstring matches. A grep of `tests/` for files naming the work carrier together with
   `rotate_requested` found only this file, so no other assertion said the ping-URL row isn't due.
4. Issue plan:
   - For a due row with no package, the proposer calls `_generate`
     (`src/rotation_proposer/cli.py:439`), which passes
     `first_issue=_first_issue(last_rotated, due.credential_id)` (`:457`); the revision path does
     the same at `:418`.
   - `_first_issue` (`:431-436`) returns `True` when infraops' `last_rotated` for the id is null.
     Both runs in item 5 serve `last_rotated` null for `work-carrier-ping-url`.
   - `test_a_first_issue_derives_the_issue_plan` (`test_work_carrier_rows.py:157`) is
     parametrized over `TOKEN` and `PING_URL` and asserts `plan_for(row, first_issue=True)` yields
     an issue plan.
5. Due-ness check:
   - Setup: I ran infraops' `cred-findings` from a scratch `rsync` copy (no `.git`) of
     `~/Projects/infraops-mcp-server` at `c3359bf` (`dist/cli/security-drift-cli.js`), with
     `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at a scratch directory per run.
     Each held a one-file list and an empty (`{}`, mode `0600`) rotation state. I ran at
     `--now 2026-11-17T13:00:00Z`. I didn't touch the real list or the checkout, and built nothing.
   - Treated (this branch's file): exit 0 and 21 findings, all `cred.rotation-requested`,
     including
     `{'check': 'cred.rotation-requested', 'id': 'work-carrier-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-17', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 20 findings, none for `work-carrier-ping-url`.
   - The set difference of `(check, id)` pairs, treated minus control, is exactly
     `{('cred.rotation-requested', 'work-carrier-ping-url')}`; control minus treated is empty.
   - Neither run raised a parse error or `cred.invalid-rotate-requested`, so the file parses under
     infraops' TOML subset.
   - The token row shows as due in both runs only because the scratch state is empty.
   - The source rule is `src/security-drift/cred-rotation.ts:279-290` in infraops.
6. Mutation check: I copied the file, deleted the new `rotate_requested` line by pattern, and ran
   the row tests: 1 failed and 19 passed; the failure was `test_both_rows_are_due` (`KeyError:
   'rotate_requested'` at `:133`). I restored the file from the copy; the rerun gave 20 passed, and
   `git diff` showed only the intended change. (A first attempt by line number missed the line
   after the comment reflow and deleted nothing; I caught it from `git diff` and reran by pattern.)

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_192`, `PGTZ=UTC`, `SECURITY_STANDARDS_DIR` at the
  test fixture): exit 0. Lint passed, pyright reported 0 errors, and pytest gave 15274 passed and
  1 skipped.
- Security scan from the repo root: 0 BLOCK. The two WARN findings and one INFO finding are the
  ones #695's report recorded, and none comes from this diff, which adds no file and no UUID:
  - WARN `bws.secret-files-gitignored`: `*-migration/`.
  - WARN `bws.manifest-matches-usage`: undeclared `aaff45ca-…`.
  - INFO `bws.least-privilege-scope`: judgment.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_work_carrier_rows.py`
- `docs/superpowers/reports/lanes-inc-4-work-carrier-ping-url-due-report.md`

## Self-review and concerns

The diff mirrors #695. Per the handoff, I left the compose and `test_deploy_lanes.py`'s
`placeholders` as they are. At the base, `lanes/work-carrier/compose.yaml` holds
`SDS_DEADMAN_PING_URL_UUID: ""` (`:33`), and its comment (`:18-23`) says a later change writes the
id of the lane's ping-URL secret; that is deploy.md item 6.

Follow-ups: deploy.md work-carrier cut-over item 6 (the compose's keeper id and the test's
placeholder). The test database `orchestrator_test_hq_w_192` still exists for HQ to drop at
teardown. The scratch copy is under `/tmp/w192-HQ31`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs):** The diff makes no logging change.
- **Code Brain rule 4 (never swallow an exception):** The changed test only asserts. It has no
  `try` or `except`.
- **Code Brain rule 5 (never log secrets):** Nothing prints a token, a ping URL, or a ping-URL
  UUID. The row names only the BWS secret id, which isn't the ping URL's UUID.
- **Infra Brain 591, 592, and 593:** The diff adds no BWS token or bootstrap token, and the scan
  found no BLOCK findings. The only value added is a date.
- **Infra Brain 2890:** The diff commits no ping URL.
- **Infra Brain 1032 (cred.exposure-rotate):** The row declares no `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** Not applicable, because the diff changes no
  domain.
- **Infra Brain lesson 450 (rotation landmines):** No secret value enters the registry, a plan, a
  test, or a log. The rows name ids only.
