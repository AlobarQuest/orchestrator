Outcome: done

# Report: make execution-watch's ping-URL row due

PR: [AlobarQuest/orchestrator#641](https://github.com/AlobarQuest/orchestrator/pull/641), branch
`hq/lanes-inc-2-ping-url-due`, base `origin/main` at `725e4424`. Code commit `43b11c23`; this
report is the commit after it (the PR head). The PR is ready for review and isn't merged.

## What I built

The `execution-watch-ping-url` row is due for its first issue, by ADR-0055 amendment 19 item 8
step 5. HQ's handoff states the precondition: the token's `store` unit completed, and no rotation
waits on a person's mint.

## Items 1-6

1. **Registry row.** `.cred-consumers.toml:721` adds `rotate_requested = "2026-11-09"` after
   `rotated_by_sds = true`, the token row's order. Neither row gains `created` or `last_rotated`.
2. **Comments.** The block at `.cred-consumers.toml:681-687` states the rule as it stands: both
   rows are due for their first issue by `rotate_requested`, and the ping URL's row was dated only
   after the token's `store` unit completed, so the two issue units never waited on a mint at
   once. The comment over the ping-URL row (`:714`) keeps only "No `created`, for the reason the
   block above gives." The text holds no correction history.
3. **Test.** `tests/rotation_worker/test_execution_watch_rows.py:71-80`:
   `test_each_row_is_due_and_has_no_age_anchor`, parametrized over both rows, replaces
   `test_only_the_token_row_is_due_and_neither_has_an_age_anchor` and keeps its docstring. The
   module docstring now says both rows are due. A grep of `tests/`, `src/`, and
   `docs/operations/` for `execution-watch-ping-url` and "not yet due" or "isn't yet due" found no
   other assertion that the ping-URL row isn't due.
4. **Proposer first issue.** `test_a_first_issue_derives_the_issue_plan[execution-watch-ping-url]`
   (`test_execution_watch_rows.py:83-88`) passes. `src/rotation_proposer/cli.py:413-418`
   `_first_issue` returns `True` when infraops serves `last_rotated` as null for the id. Both
   `_revision_body` (`:400`) and `_generate` (`:439`) pass that value to the plan. The row has no
   `created`, and infraops serves `last_rotated: null` for it (item 5), so the proposer takes it
   as a first issue.
5. **Due-ness, measured.** I ran infraops' `security-drift-cli cred-findings` from a scratch copy
   (`/tmp/w59-credfind.CpVl`). The copy holds `dist`, `package.json`, and a symlink to
   `node_modules`, built from `c3359bf`. I didn't edit or build the checkout.
   `INFRADRIFT_CONFIG_DIR` pointed at a scratch list naming only this worktree's file, and
   `SECURITY_DRIFT_STATE_DIR` at a copy of the rotation state. I didn't use
   `~/.config/infra-drift/cred-consumers.list`. The run printed these `execution-watch` results:
   - `{'check': 'cred.rotation-requested', 'id': 'execution-watch-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-09', 'rotated_by_sds': True}`
   - `{'check': 'cred.rotation-requested', 'id': 'execution-watch-bws-token', 'class': 'bws-lane-token', 'rotate_requested': '2026-11-08', 'rotated_by_sds': True}`
   - `last_rotated`: `{'execution-watch-bws-token': None, 'execution-watch-ping-url': None}`

   For the control, I ran the same command over `origin/main`'s file. It listed only
   `execution-watch-bws-token`, so the ping-URL finding comes from this change.

   **The token row still lists, with `last_rotated` null.** The rotation state file I copied,
   `~/.config/infra-drift/cred-rotation-state.json`, was last modified October 31, 2026. It holds
   no recorded rotation for `execution-watch-bws-token`. The handoff says that rotation was
   recorded today, but infraops' local state doesn't show it. I reported this and didn't change
   it.
6. **Mutation check.** I backed up `.cred-consumers.toml` to `/tmp/w59-registry.bak` and removed
   the new line. `test_each_row_is_due_and_has_no_age_anchor[execution-watch-ping-url]` failed
   (1 failed, 6 passed). After I restored from the copy, all 7 passed and `git diff` matched the
   intended change.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_59` exited 0: ruff and ruff format passed, pyright
  reported 0 errors, and pytest reported **14136 passed, 1 skipped**.
- `tests/rotation_worker/test_execution_watch_rows.py`: 7 passed, also after the final docstring
  reflow.
- Security scan from the repo root: **0 BLOCK**, 2 WARN, and 1 INFO. The WARNs are
  `glob not covered: *-migration/` and `undeclared UUID in code: aaff45ca-…`. Neither comes from
  this diff, which doesn't contain that UUID.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_execution_watch_rows.py`
- `docs/superpowers/reports/lanes-inc-2-ping-url-due-report.md` (this report)

## Self-review

The diff is two comment rewrites, one registry line, and one test rewritten into a parametrized
form. That form asserts the same three properties for each row and drops the "ping URL isn't due"
assertion. The cred-findings run and its `origin/main` control measure due-ness with the real
infraops code, not only by reading it.

## Concerns and follow-ups

- infraops' rotation state has no entry for `execution-watch-bws-token`, and the state file
  predates the token's rotation. If HQ expected `record-rotation` to have run, it hasn't reached
  `~/.config/infra-drift/cred-rotation-state.json` on this machine. While that entry is missing,
  infraops keeps reporting `cred.rotation-requested` for the token. Check where the proposer
  records it (`_record_retired`, `src/rotation_proposer/cli.py:550`).
- Teardown is still to do: remove the scratch directory `/tmp/w59-credfind.CpVl`, the backup
  `/tmp/w59-registry.bak`, the test database `orchestrator_test_hq_w_59`, and the worktree after
  merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** the change adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** the rewritten test has no `try`
  and catches nothing. A failure fails the assertion.
- **Code Brain rule 5 (never log secrets):** nothing added prints a token, a ping URL, or a ping
  URL's UUID. The `bws_uuid` values in the rows and this report are BWS secret ids, which differ
  from a ping URL's UUID.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token to any tracked file or to history, and
  the test adds no test values. The security scan reports 0 BLOCK.
- **Infra Brain 2890:** the diff and report contain no `https://healthcheck.alobar.net/ping/` URL.
- **Infra Brain 1032 (cred.exposure-rotate):** neither row declares `[[credential.exposure]]`, so
  the rule doesn't apply.
- **Infra Brain lesson 450 (rotation landmines):** the registry, test, and report name ids and a
  date only. No secret value enters them.
