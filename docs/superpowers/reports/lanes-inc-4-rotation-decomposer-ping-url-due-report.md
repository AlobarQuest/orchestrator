Outcome: done

# Report: the rotation decomposer's ping-URL row is due

PR #695, branch `hq/lanes-inc-4-rotation-decomposer-ping-url-due`, base `origin/main` at
`ef51a15c`. Code commit `8d1d1a38`; this report is committed after it. The PR is open and ready,
not merged.

## What I built

1. In `.cred-consumers.toml`, the `rotation-decomposer-ping-url` row (`bws_uuid`
   `936bbbf2-…`) takes `rotate_requested = "2026-11-13"` at `:1154`, after `rotated_by_sds = true`.
   That is the order the `change-proposer-ping-url` row uses (`:1103-1104`). The row has no
   `created` and no `last_rotated`.
2. The rotation decomposer's block comment (`:1114-1119`) states the rule as it stands, in the
   change proposer's words: both rows are due by `rotate_requested`, and the ping URL's row is
   dated only after the token's `store` unit completed. It carries no correction history.
3. In `tests/rotation_worker/test_rotation_decomposer_rows.py`, `test_both_rows_are_due` (`:125`)
   replaces `test_only_the_token_row_is_due`. It asserts the token row's date as read from the
   file at the base (`:1126`, `2026-11-13`) and the ping-URL row's date. I updated the module
   docstring to match. A grep of `tests/` for `rotation-decomposer` or `rotation_decomposer`
   together with `rotate_requested` found no other assertion that the ping-URL row isn't due.
4. Issue plan:
   - A dated row makes infraops emit `cred.rotation-requested` (item 5).
   - For a due row with no package, the proposer calls `_generate`
     (`src/rotation_proposer/cli.py:439`), which passes
     `first_issue=_first_issue(last_rotated, due.credential_id)` (`:457`).
   - `_first_issue` (`:431-436`) returns `True` when infraops' `last_rotated` for the id is null.
     Both runs in item 5 serve `last_rotated` null for this row.
   - `test_a_first_issue_derives_the_issue_plan` (`test_rotation_decomposer_rows.py:152`) is
     parametrized over `TOKEN` and `PING_URL` and asserts `plan_for(row, first_issue=True)` yields
     an issue plan.
5. Due-ness check:
   - Setup: I ran infraops' `cred-findings` from a scratch `rsync` copy of
     `~/Projects/infraops-mcp-server` at `c3359bf` (`dist/cli/security-drift-cli.js`), with
     `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at scratch directories. Each
     held a one-file list and an empty (`{}`, mode `0600`) rotation state. I ran at
     `--now 2026-11-13T22:00:00Z`. I didn't touch the real list or the checkout.
   - Treated (this branch's file): exit 0 and 19 findings, all `cred.rotation-requested`,
     including
     `{'check': 'cred.rotation-requested', 'id': 'rotation-decomposer-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-13', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 18 findings, with no finding for
     `rotation-decomposer-ping-url`.
   - Both runs serve `last_rotated` null for both rotation-decomposer rows. Neither raised a parse
     error or `cred.invalid-rotate-requested`, so the file parses under infraops' TOML subset.
   - The token row shows as due in both runs only because the scratch state is empty. The one
     difference between the runs is the ping-URL finding.
   - The source rule is `src/security-drift/cred-rotation.ts:279-290` in infraops.
6. Mutation check: I copied the file, deleted line 1154 (the new `rotate_requested`), and ran the
   row tests: 1 failed and 18 passed, and the failure was `test_both_rows_are_due`. I restored the
   file from the copy; the rerun gave 19 passed, and `git diff` showed only the intended change.

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_140`, `PGTZ=UTC`,
  `SECURITY_STANDARDS_DIR` at the test fixture): exit 0. Lint passed, pyright reported 0 errors,
  and pytest gave 14850 passed and 1 skipped.
- Security scan from the repo root: 0 BLOCK. The two WARN findings and one INFO finding are the
  ones #691's report recorded, and none comes from this diff, which adds no file and no UUID:
  - WARN `bws.secret-files-gitignored`: `*-migration/`.
  - WARN `bws.manifest-matches-usage`: undeclared `aaff45ca-…`.
  - INFO `bws.least-privilege-scope`: judgment.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_rotation_decomposer_rows.py`
- `docs/superpowers/reports/lanes-inc-4-rotation-decomposer-ping-url-due-report.md`

## Self-review and concerns

The diff mirrors #691 line for line. Per the handoff, I left the compose and
`test_deploy_lanes.py`'s `placeholders` as they are. At the base,
`lanes/rotation-decomposer/compose.yaml` holds `SDS_DEADMAN_PING_URL_UUID: ""` (`:28`), and its
comment (`:13`) says a later change writes the keeper's id; that is deploy.md step 6.

The test database `orchestrator_test_hq_w_140` still exists for HQ to drop at teardown. The scratch
copy is under `/tmp/w140-46aw`.

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
