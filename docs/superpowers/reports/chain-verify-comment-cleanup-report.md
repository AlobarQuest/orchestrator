Outcome: done

# Report: drop stale `_verify_factory_chain` mentions

Branch `hq/chain-verify-comment-cleanup`, base `origin/main` `858528f3`. Worker W-177.

## What I built

Orchestrator PR #718 (`858528f3`) removed `_verify_factory_chain`, so no code runs
`python -m factory_events verify` from the carry any more. Two texts still named it. I fixed both,
with no code change:

1. `scripts/run-work-carrier.sh`, the "THE FACTORY-EVENTS CHAIN" comment (lines 290-295). It named
   `emit-intake-payload`, `python -m factory_events verify` and intent-packages' `verify-approval`
   as the children that inherit `work-carrier`'s environment. The comment now names the three
   children the carry runs at `858528f3`: `emit-intake-payload`, the
   `orchestrator.factory_chain_read` walk it starts, and intent-packages' `verify-approval`.
2. `docs/superpowers/plans/2026-11-14-fe-5-cutover.md`, R-OR (after line 422). I left the
   original sentence ("`_verify_factory_chain` (246-261) stays …") in place. A dated note follows
   it: "**Later, 2026-11-15:** orchestrator PR #718 (`858528f3`) removed `_verify_factory_chain`,
   because nothing called it." The bold dated lead matches the plan's other as-run notes in the
   step 0 and step 14 rows.

## Grep result for item 1

`git grep -n factory_events src/ scripts/ lanes/` at `858528f3` has no `python -m factory_events
verify` call. The only `-m factory_events` invocation is `import-file` in
`scripts/factory-events-session.sh:132`, which the carry doesn't run. The other hits are the
switch helpers in `scripts/sds-bws.sh` and their callers in `run-bump-proposer.sh` and
`run-work-carrier.sh`, the bundle and registry scripts, and `factory_chain_read.py`'s in-process
imports of `factory_events.store` and `factory_events.chain`.

The carry's chain path, read from the code:

- `src/work_carrier/prepare.py:119` shells out to `orchestrator emit-intake-payload`.
- That path runs `_verify_current_approval` in `src/orchestrator/package_sources.py`, which runs:
  - `_verify_with_intent_packages_cli` (line 170): `python -m intent_packages verify-approval`,
    with the inherited environment.
  - `_database_package_approval_events` (line 275): `python -m orchestrator.factory_chain_read`,
    with the inherited environment plus security-standards' `src` on `PYTHONPATH`. It reads the
    read password and the three `FACTORY_EVENTS_DB_*` settings from the environment.

## Tests and checks

`make check` against `orchestrator_test_hq_w_177`, with `TEST_DATABASE_URL`,
`ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` (the fixture) and `PGTZ=UTC` exported, as
`docs/operations/local-development.md` says. Exit 0: 10 workers, 15000 items collected,
14999 passed, 1 skipped. Lint and format reported "All checks passed!".

## Files changed

- `scripts/run-work-carrier.sh` (comment only)
- `docs/superpowers/plans/2026-11-14-fe-5-cutover.md` (one added note)
- `docs/superpowers/reports/chain-verify-comment-cleanup-report.md` (this report)

## Self-review

- The comment's claim, that each named child inherits the environment and reads the database
  chain under a set read password, holds for all three. `factory_chain_read` and `verify-approval`
  each get `dict(os.environ)` plus a `PYTHONPATH` addition.
- The plan's original sentence is unchanged. The note adds history only.
- I didn't run `/code-review`: the diff is a comment and a doc note, which CLAUDE.md lets a
  session skip.

## Concerns

None. `scripts/run-bump-driver.sh` lines 27-28 were out of scope and stay unchanged.

## Standards followed

- **Code Brain rule 5 (BLOCK), never log or write secrets:** neither edit nor this report holds a
  secret value. The comment names variables only (`FACTORY_EVENTS_READ_PASSWORD`,
  `FACTORY_EVENTS_DB_*`).
- **Code Brain rules 3 and 4:** don't apply, because I changed no code.
- **Infra Brain:** none applies, because I made no infrastructure change.
- **Repo doc style:** the plan note follows the plan's bold dated-lead as-run style, and the shell
  comment keeps the block's existing wrap and voice.
