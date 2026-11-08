Outcome: done

# Report: the rotation runbook says a first-issue registry row carries no `created`

PR #640, ready for review, not merged. The docs commit is `cce7bd7f`. This report is committed after
it, so the branch head is the commit that adds this file.

## What I built

I added one paragraph to `docs/operations/rotation-executor.md`, section "The registry rows",
after the ping-URL row paragraph and before "What each unit does". The paragraph has five
sentences. It says the following:

- A row whose first unit is an issue (a lane token or a ping URL that the SDS creates) carries no
  `created` and no `last_rotated`. The first `record-rotation` sets its rotation anchor.
- infraops reads either field as a rotation date (`rotationAnchor`) and serves that date as the
  row's `last_rotated`. The proposer's `_first_issue` then reads the credential as already issued.
- `plan_for` then picks the rotation plan, or refuses a type that has none, so the issue never
  runs.
- With neither field, the first issue is due from `rotate_requested` alone.
- A "For more information" pointer leads to W-57's report,
  `docs/superpowers/reports/lanes-inc-2-registry-rows-report.md`.

The sample TOML block in the section has no `created` line. I left it unchanged. I didn't touch
any ADR.

## Source lines confirmed

infraops-mcp-server `origin/main` at `c3359bf1486cfe63018cfb404b03246aaabd4f32`, read with
`gh api` and not checked out:

- `src/security-drift/cred-rotation.ts` lines 209–211: `rotationAnchor` returns
  `state.lastRotated[spec.id] ?? spec.last_rotated ?? spec.created`. The handoff gave line 210,
  and line 210 holds that expression.
- `src/cli/security-drift-cli.ts` lines 191–194: the CLI serves `last_rotated` as
  `rotationAnchor(spec, state) ?? null` for every `rotated_by_sds` row. This is the link that
  carries `created` to the proposer. The handoff didn't name it, so I checked it too.
- `cred-rotation.ts` lines 279–281: the `cred.rotation-requested` check compares against
  `state.lastRotated ?? spec.last_rotated` and ignores `created`. Line 95 gives `bws-lane-token`
  `maxAgeDays: 365`, which applies only when an anchor exists. Lines 97–98 give
  `healthchecks-ping-url` an infinite maximum age. A row with neither field therefore has no
  anchor, and `rotate_requested` alone makes it due.

This repository at `39887ddb`:

- `src/rotation_proposer/cli.py` lines 413–418: `_first_issue` returns
  `last_rotated[credential_id] is None`. A served `created` date makes it `False`.
- `src/rotation_proposer/cli.py` lines 550–570: `_record_retired` records a retired rotation only
  when it is later than the served `last_rotated`. A null value is earlier than any rotation, so
  the first `record-rotation` sets the anchor.
- `src/rotation_proposer/generate.py` lines 140–146: `plan_for` returns the issue plan only when
  `first_issue is True`. When it is `False`, it returns the rotation plan, or raises
  `GenerateError` for a type with no rotation plan.

**Difference from the handoff:** the handoff says the proposer "takes the rotation path instead of
the issue". That holds for a type with both plans. For a type with only an issue plan, `plan_for`
refuses instead. The paragraph states both cases. I treated this as a more exact wording of the
same rule, not a contradiction, so I didn't write a question file.

## Tests and checks

The Makefile has no `docs-check` target, so I ran `make check`. It used the worker database
`orchestrator_test_hq_w_58`, migrated to head (`0047_retire_tracker_bindings`), with `PGTZ=UTC`.

- ruff check and ruff format: All checks passed.
- pyright: 0 errors, 0 warnings, 0 informations.
- pytest: **14135 passed, 1 skipped**, 11 warnings, in 280.80 s.

## Files changed

- `docs/operations/rotation-executor.md`: one paragraph added.
- `docs/superpowers/reports/rotation-runbook-first-issue-created-report.md`: this report.

## Self-review

- The paragraph uses present tense and active voice, and holds no history of the change. That
  history stays in W-57's report.
- Function and field names match the source. The paragraph has no line numbers, so it doesn't go
  stale when either file changes.
- I didn't run `/code-review`. The diff is a single prose paragraph with no logic, so it falls
  under the trivial-change exemption.

## Concerns and follow-ups

- **Teardown:** the worker database `orchestrator_test_hq_w_58` still exists. HQ removes it with
  the worktree.
- **Follow-up for HQ:** a guard could refuse a `created` field on a row whose type issues. No such
  guard exists. I didn't build one because it's out of scope for a docs-only change.

## Standards followed

- **Code Brain rule 3** (structured JSON logs to stdout): this change has no code, so the rule
  doesn't apply.
- **Code Brain rule 4** (never swallow an exception silently): this change has no code, so the
  rule doesn't apply.
- **Code Brain rule 5** (never log secrets): the paragraph names functions, fields, and file paths
  only. It contains no token, no ping URL, and no UUID.
- **Infra Brain 591, 592, and 593** (BWS token handling): the diff has no BWS token and no example
  value in the token's shape. The only placeholders are in the unchanged sample block, and they
  are bracketed prose.
- **Infra Brain 2890** (never commit a ping URL with a real UUID): the diff contains no ping URL.
