# Lane runtime layer pass: implementation plan

Spec: `docs/superpowers/specs/2026-11-04-runtime-layer-design.md`. Branch `runtime-layer-pass` in
`.worktrees/runtime`, its own `.venv` and database `orchestrator_test_runtime`. Tests need
`TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR` and `PGTZ=UTC`.

Tasks run one at a time in this worktree, so two pytest suites never share the database. Each task
is test-first, ends with its own tests and `make check` green, and is committed on its own. Each
gets an implementer and a reviewer (Sonnet); the final whole-branch review runs on Opus.

## Task 1: the alarm library (Part 1b mechanism, Part 1c)

Files: `scripts/sds-deadman.sh`, `tests/scripts/test_sds_deadman.py`, `tests/scripts/test_sds_bws.py`.

1. `sds_deadman_resolve` reads the key through `sds-bws.sh` (`sds_bws_value <uuid> "$broad"`),
   sourced by its own directory, and parses the check listing with the venv Python `sds-bws.sh`
   uses. No `python3` from `PATH` remains in the file.
2. `sds_outcome_end <rc> [--body-on-success] [--told <code>]`, flags in any order. `--told` refuses
   0 and 1 the way `sds_deadman_arm` refuses them for `--finding`. An exit equal to the told code
   logs one line naming it and pings success with the report as the body.
3. Tests: success ping on the told code, `/fail` on other codes, both flags together, a reserved
   told code ignored with a log line, and the key read through a stubbed `bws` with no `python3` on
   `PATH`. `test_sds_bws.py`'s no-self-parse rule covers `sds-deadman.sh` and catches `| VAR=… python3`.
4. A test holds each launcher's `--told` code to its `EXIT CODES` block, as the `--finding` test does.

## Task 2: bump-driver tells, then pings success (Part 1b)

Files: `scripts/run-bump-driver.sh`, `src/bump_driver/` tests.

1. A structural test: every `Outcome` whose result is in `FINDINGS` carries a key, and a failed
   sync returns 3.
2. The launcher calls `sds_outcome_end "$rc" --told 2`, and its `EXIT CODES` block says code 2 is
   delivered as a keyed alert and a failed delivery is 3.

## Task 3: tool_installer alerts (Part 1a)

Files: `src/tool_installer/`, `scripts/run-tool-installer.sh`, the guard tables the spec names.

Keys, unread rule, state path, token handling and exit mapping as in the spec's table. Tests cover
firing on a failing row, resolving on a clean row, an un-acted row left unread, no `sync` on an
early exit, a failed send giving 2 after the pass's own work, and a dry run sending nothing.

## Task 4: activation_sweep alerts (Part 1a)

Files: `src/activation_sweep/`, `scripts/run-activation-sweep.sh`, guard tables.

Two state files (`sweep-alerts.json`, `bind-alerts.json`). Keys by the member name `sds_scope`
prints. Tests cover each sweep condition, the bind per-member rule, an unavailable checkout left
unread under the same key it fired with, one subcommand never resolving the other's keys, and a
failed send giving 3.

## Task 5: revision_watcher alerts (Part 1a, Part 3 rows)

Files: `src/revision_watcher/`, `scripts/run-revision-watcher.sh`, guard tables.

Key per application from `Pass.findings`, unread from `Pass.unreadable`; an undeclared application
raises nothing. Correct the launcher header and the CLI docstring's "open decision".

## Task 6: pin_watcher alerts (Part 1a)

Files: `src/pin_watcher/`, `scripts/run-pin-watcher.sh`, guard tables. Same shape as Task 5, keyed
by repository.

## Task 7: the edges test (Part 4)

Files: `tests/architecture/test_runtime_edges.py`, `tests/architecture/edge_table.py`.

Rows as in the spec, each with a control that widens the surface and is caught. New helpers: a
line parser for shell command heads and hosts, and `network`/`processes` for single `scripts/`
files. Correct the `activate-checkout.sh` comment.

## Task 8: records (Part 3)

Every row of the spec's Part 3 table, ADR-0021 amendment 3, and the CLAUDE.md rules for this layer
rewritten as they stand, with the old text moved to `docs/history/claude-md-invariants-archive.md`.
The reader lists in both manifests are regenerated from a grep of each UUID, never by hand.

## After the tasks

1. Final whole-branch review (Opus), one fix wave, a scoped re-review.
2. Before merging: no rotation unit ready or claimed, no dispatched run live, and no rotation of
   `bws-sds-operator-token` or the observer bearer in flight. Merge.
3. No server code changes, so no deploy. Reinstall the activation-sweep, estate-landing and
   rotation-decomposer plists from `main`.
4. Live proof, from each lane's next scheduled run log: the spec's Live proof table.
5. Docs: the Layer Map artifact, the year plan sentence, memory.
