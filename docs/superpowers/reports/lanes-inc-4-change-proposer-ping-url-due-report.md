Outcome: done

# Report: the change proposer's ping-URL row is due

PR: #691 (`hq/lanes-inc-4-change-proposer-ping-url-due`), open and ready, not merged. It's based
on `origin/main` at `c194950b`, the base the handoff names. The change commit is `1b1b59fd`, and
this report is a second commit on the same branch.

## What I built

These items follow the handoff's numbering.

1. In `.cred-consumers.toml:1104`, I added `rotate_requested = "2026-11-13"` to the
   `change-proposer-ping-url` row (`bws_uuid = "7651ccc2-…"`), after `rotated_by_sds = true`. This
   is the same order the `membership-check-ping-url` row uses (`:1054`). The row has no `created`
   and no `last_rotated`.
2. I rewrote the block comment at `.cred-consumers.toml:1066-1069` to the rule as it stands,
   using the membership check's wording from #684. Both rows are due by `rotate_requested`. The
   ping URL's row is dated only after the token's `store` unit completed. The comment carries no
   correction history.
3. In `tests/rotation_worker/test_change_proposer_rows.py`, `test_both_rows_are_due` (`:120`)
   replaces `test_only_the_token_row_is_due`. It asserts that the token row is `2026-11-13`, as
   read from the file at the base (`:1076`), and that the ping-URL row is `2026-11-13`. I updated
   the module docstring to match. I grepped `tests/` for any other assertion that this row isn't
   due and found none. The only change-proposer `not in` hit is
   `tests/scripts/test_run_change_proposer.py:418` (`STATEFUL_LANES`), which is unrelated.
4. Issue plan:
   - A dated row makes infraops emit `cred.rotation-requested`.
   - For a due row with no package, the proposer calls `_generate`
     (`src/rotation_proposer/cli.py:439`). `_generate` passes
     `first_issue=_first_issue(last_rotated, due.credential_id)` (`:457`).
   - `_first_issue` (`:431-436`) returns `True` when infraops' `last_rotated` for the id is
     null. This row has no `created` and no recorded rotation, so infraops serves null.
   - `test_a_first_issue_derives_the_issue_plan` (`test_change_proposer_rows.py:148`) is
     parametrized over `TOKEN` and `PING_URL`. It asserts that `plan_for(row, first_issue=True)`
     yields an issue plan for the ping-URL row.
5. Due-ness check:
   - Setup: I ran infraops' `cred-findings` from a scratch `rsync` copy of
     `~/Projects/infraops-mcp-server` at `c3359bf` (`dist/cli/security-drift-cli.js`). The run
     used `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` pointed at scratch directories,
     each with a one-file list and an empty `0600` rotation state, at
     `--now 2026-11-13T18:00:00Z`. I didn't touch the real list or the checkout.
   - Treated (this branch's file): exit 0 and 17 findings, all `cred.rotation-requested`. This
     includes
     `{'check': 'cred.rotation-requested', 'id': 'change-proposer-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-13', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 16 findings, with no finding for
     `change-proposer-ping-url`.
   - Both runs return `last_rotated` null for both change-proposer rows. Neither run raised a
     parse error or `cred.invalid-rotate-requested`, so the file parses under infraops' TOML
     subset.
   - The token row also shows as due in both runs because the state was empty. Production's
     state records that rotation. The only difference between the runs is the ping-URL finding.
   - The source rule is `src/security-drift/cred-rotation.ts:279-290`.
6. Mutation check: I copied the file, then deleted line 1104. That run gave 1 failed and 18
   passed, and the failure was `test_both_rows_are_due`. I restored the file from the copy, and
   the rerun gave 19 passed.

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_132`, `PGTZ=UTC`): exit 0, with
  14831 passed and 1 skipped. Pyright reported 0 errors.
- Security scan from the repo root found no BLOCK findings. It reported two WARN findings and one
  INFO finding, none from this diff, because the diff adds no file and no UUID:
  - WARN `bws.secret-files-gitignored`: `*-migration/`.
  - WARN `bws.manifest-matches-usage`: undeclared `aaff45ca-…`.
  - INFO `bws.least-privilege-scope`: judgment.
- `/code-review` (low) on `origin/main...HEAD` returned no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_change_proposer_rows.py`
- `docs/superpowers/reports/lanes-inc-4-change-proposer-ping-url-due-report.md`

## Self-review and concerns

The diff mirrors #684 line for line. Per the handoff, I left the compose's
`SDS_DEADMAN_PING_URL_UUID` (still `""`) and `test_deploy_lanes.py`'s `placeholders` as they are.
Those belong to deploy.md step 6. The test database `orchestrator_test_hq_w_132` still exists for
HQ to drop at teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs):** The diff makes no logging change.
- **Code Brain rule 4 (never swallow an exception):** The changed test only asserts. It has no
  `try` or `except`.
- **Code Brain rule 5 (never log secrets):** Nothing prints a token, a ping URL, or a
  ping-URL UUID. The row names only the BWS secret id, which isn't the ping URL's UUID.
- **Infra Brain 591, 592, and 593:** The diff adds no BWS token or bootstrap token, and the scan
  found no BLOCK findings. The only values added are dates.
- **Infra Brain 2890:** The diff commits no ping URL.
- **Infra Brain 1032 (cred.exposure-rotate):** The row declares no `[[credential.exposure]]`.
- **Infra Brain 212 (HTTPS for public FQDNs):** Not applicable, because the diff changes no
  domain.
- **Infra Brain lesson 450 (rotation landmines):** No secret value enters the registry, a plan, a
  test, or a log. The rows name ids only.
