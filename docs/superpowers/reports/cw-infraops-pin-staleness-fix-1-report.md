Outcome: done

# Report: cw-infraops-pin-staleness, fix round 1 (PR #796)

Worker W-280. I pushed the round to PR #796's branch, `hq/cw-infraops-pin-staleness`, on top of
`65961049`. The full section the handoff asked for is `## Fix round 1` in
`docs/superpowers/reports/cw-infraops-pin-staleness-report.md`. This file is the worker report and
summarizes that section.

## What I built

- **Finding 1:** `deploy.md`'s revision-watcher paragraph is back to its `a2c27ce7` text, and the
  pin-watcher paragraph names `infraops-pin-alerts.json`. The pin-bump paragraph says "a pass
  after the bump merges resolves the alert".
- **Finding 2:** an unread bundle makes the pass exit 3, in a dry run too. An acting pass still
  syncs the key as unread first, so an open key is held and never resolved. A stale pin keeps the
  callers' exit code. `scripts/run-pin-watcher.sh` and `sds_deadman_arm` are unchanged. With
  `--finding 2`, the exit trap `sds_deadman_finish` sends `/fail` for 3, so the stop point
  doesn't apply.
- **Dry run:** a dry run with an unreadable caller exits 3, so a dry run with an unread bundle
  matches it. It exits 3 and sends and writes nothing.
- **Finding 3:** Amendment 19 items 4 and 5 state the unread rule and no longer say an unread
  bundle changes no exit code. No other amendment was edited.
- **Finding 4:** `cli.py`'s docstring describes the order (credential checks, bundle check, member
  record), and its exit-code table names an unread bundle.

## Tests and checks

- `tests/pin_watcher`: 132 passed. New tests cover unread with an open key (4 failure kinds),
  unread with the key never opened (4), unread beside a caller finding, an unread dry run, and a
  stale pin keeping the callers' exit code.
- Mutation: three mutants in `cli.py`, all killed (10, 11, and 1 failures). The first is "unread
  leaves the exit unchanged".
- `make check` on `orchestrator_test_hq_w_280`: 14744 passed, 1 skipped. Ruff and pyright are
  clean.
- Risk verdict: `normal`. `/code-review` at medium found three nits. I addressed two, and the
  third needs no change; the main report gives the reasons.

## Files changed

`src/pin_watcher/cli.py`, `src/pin_watcher/bundle.py`, `tests/pin_watcher/test_bundle.py`,
`docs/operations/deploy.md`, `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`
(Amendment 19 only), `docs/superpowers/reports/cw-infraops-pin-staleness-report.md`, and this
file.

## Self-review

The change is one return value and one exit condition. The unread path still runs the sync before
returning, so the open-key hold is unchanged. The mutation runs pin both causes of an incomplete
pass.

## Concerns

- `git reset --hard` was denied in this worktree. I reached the PR head with
  `git merge --ff-only origin/hq/cw-infraops-pin-staleness`, which gives the same result from the
  PR's base.
- Merged isn't deployed: the scheduled pass runs the rule only after the lane image is released
  and redeployed.

## Standards followed

- **Code Brain rule 3:** stdout and stderr lines go through `typer.echo`. No logging library and
  no file were added.
- **Code Brain rule 4:** an unread bundle is logged and surfaces as exit 3. It's no longer
  swallowed at the pass level.
- **Code Brain rule 5:** no credential is in the change, and the token-absence test passes.
- **Code Brain rule 7:** the unread messages keep the URL and status, the error type, or the parse
  reason.
- **Road `notifications`:** keyed alert-hub through `KeyedAlerts`. Unread holds the key, and equal
  resolves it.
- **Infra Brain BLOCK 2, 591, 592, 593, 1032, 3:** no secrets, no exposed credential, and no image
  tag. Rules 1, 4, 5, 6, 211, and 212 don't apply.
