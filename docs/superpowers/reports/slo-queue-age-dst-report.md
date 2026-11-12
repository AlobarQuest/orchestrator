Outcome: done

# Report: the SLO report's queue age across a DST change

- PR: AlobarQuest/orchestrator#671 (ready, not merged), branch `hq/slo-queue-age-dst`.
- Base: `origin/main` at `24c43faf` (confirmed at dispatch).
- Code head: `fbeb8819`. The commit that adds this report follows it on the same branch.

## What I built

`_queue_age` in `src/orchestrator/services/reporting/slo_report.py` converts `now` and the
ready-entry time to UTC before subtracting, so the age is the true elapsed time whatever the
session's time zone. A regression test in `tests/services/test_slo_report.py` pins the case.

## Hypothesis check

HQ's reading holds. The hour comes from the Postgres server's `TimeZone`, not the client process's
`TZ`.

- `SHOW timezone` on `orchestrator_test_hq_w_103`: `America/New_York`.
- A probe on that database, with no session setting, read `transaction_timestamp()` and
  `'2026-07-05 00:00:00+00'::timestamptz`:

  ```
  now:     datetime(2026, 11, 12, 3, 47, 4, 825208, tzinfo=ZoneInfo(key='America/New_York'))
  entered: datetime(2026, 7, 4, 20, 0, tzinfo=ZoneInfo(key='America/New_York'))
  same tzinfo object: True
  naive subtraction: 11260024.825208
  utc subtraction:   11263624.825208
  diff: -3600.0
  ```

- The unmodified `test_queue_age_median_of_ready_units` failed with `assert 3600.0 < 5`
  (`11260024.22 - 11263624.22`): the report's value is the wall-clock subtraction, and the test's
  `expected` (UTC `ready_at`) is the true one. The test is right; the product code was wrong.

## Red-then-green proof under `TZ=UTC`

Red: `git archive 24c43faf` into `/tmp/w103/base1`, the new test file copied in, run with
`TZ=UTC` and `PYTHONPATH` pointing at the archive's `src` (confirmed: `slo_report` imported from
`/tmp/w103/base1/src/...`):

```
E       AssertionError: assert 1810800.0 == 1814400.0
E        +  where 1810800.0 = MetricValue(status='computed', value=1810800.0, ...).value
2 failed, 32 deselected, 1 warning in 0.51s
```

The second failure in that run is the existing `test_queue_age_median_of_ready_units`
(`assert 3600.0 < 5`). It fails under `TZ=UTC` too, because the zone is the server's.

Green: the worktree with the fix, `TZ=UTC`:

```
tests/services/test_slo_report.py: 34 passed, 1 warning in 3.85s
```

## Tests and checks

- `make check` (with `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL` at
  `orchestrator_test_hq_w_103`, `SECURITY_STANDARDS_DIR` set): exit 0. ruff "All checks passed",
  pyright 0 errors, pytest 14623 items collected, 14622 passed, 1 skipped.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `src/orchestrator/services/reporting/slo_report.py`: import `UTC`; UTC subtraction in
  `_queue_age`, with a two-line comment giving the reason.
- `tests/services/test_slo_report.py`: `test_queue_age_is_true_elapsed_time_across_a_dst_change`.
- `docs/superpowers/reports/slo-queue-age-dst-report.md`: this report.

## Self-review

- The test uses `SET LOCAL TIME ZONE` after a flush and never commits, so the setting dies with
  the test's transaction and can't leak to another test through the pool.
- `now` is read from the database in that zone (as `TransactionClock` does), not built in
  Python: a Python-built UTC `now` has a different `tzinfo` from `entered`, and Python would then
  subtract correctly and hide the bug.
- The test asserts `now.utcoffset() == -5h`, so it can't pass vacuously if the zone setting were
  ever ignored.
- Both ends are fixed (2026-10-20 12:00 UTC, EDT; 2026-11-10 12:00 UTC, EST), so the test is
  independent of today's date. Expected: exactly 21 days, 1814400 seconds.
- The existing `test_queue_age_median_of_ready_units` is unchanged and passes after the fix.

## Deviations

None. The test calls the private `_queue_age` directly, as the handoff allowed, to pass a fixed
`now`.

## Follow-ups (not fixed in this PR)

Same shape, in `src/orchestrator`: a subtraction, or timedelta arithmetic, on a datetime the
database returned in the session zone. Python does both by wall clock when the `tzinfo` is a DST
`ZoneInfo`. Each is latent where the server zone is UTC.

1. `src/orchestrator/services/reporting/slo_report.py:121`: `first_claim - revision.registered_at`
   (two database values). `intake_to_first_work` is an hour off when a claim follows its
   registration across a DST change.
2. `src/orchestrator/services/reporting/slo_report.py:81`: `until - DEFAULT_WINDOW`, with `until`
   from `TransactionClock` when no filter is given: the default window start can shift an hour.
3. `src/orchestrator/services/lifecycle/execution_stall.py:103`:
   `now - claim.lease_expires_at` (`TransactionClock` and a database value), and line 79
   `cutoff = now - timedelta(seconds=grace_seconds)`.
4. `src/orchestrator/services/landing/revert_watch.py:365`:
   `production.now - max(settled, *received_at...)`. `production.now` is `func.now()` and
   `received_at` a database value (`revert_act.py:1187`, `1160`); when `max` picks a `received_at`
   both share the session zone. Line 409, `production.now - timedelta(hours=...)`, has the same
   arithmetic issue.
5. `src/orchestrator/services/landing/branch_update_serialization.py:185`:
   `now - EDIT_EVENT_BOUND`, with `now` from `TransactionClock` (line 346), then sent to SQL.

Comparisons (`<`, `max`) between two values that share one `ZoneInfo` are also by wall clock, so
an ordering inside the repeated fall-back hour can invert. I didn't enumerate those.

Out of the orchestrator package, `execution_watch`, `landing_ledger` and `bump_proposer` subtract
datetimes parsed from API JSON, not read from the database, so they are outside this shape.

A root-cause option, if HQ wants one: set the session zone to UTC at the engine (`db.py`). That
is the edit this handoff reserved for a question, so I didn't make it.

## Concerns

- The developer Postgres server's default `TimeZone` is `America/New_York`, so every Mac worker's
  test database runs in that zone. The production server's zone wasn't read (no production act).

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout, not files): no logging change; the diff adds
  no log call.
- Code Brain rule 4 (never swallow an exception silently): the diff adds no `try` or `except`;
  every failure in the new test propagates as an assertion or error.
- Code Brain rule 5 (never log secrets, tokens, or request bodies): nothing added prints or logs
  anything.
