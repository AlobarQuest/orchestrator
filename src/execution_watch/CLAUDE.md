# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The execution-watch lane (`src/execution_watch/`)

- `execution-watch` alerts on a stalled, held or failed unit, from `GET /api/v1/execution-status`,
  which is that route's one reason to exist. It skips a rotation unit, and each condition
  `owned_elsewhere` says a bump lane or `work_watcher` holds, a table
  `test_the_ownership_table_matches_the_bump_lane` holds to the bump lane's selection. The one
exception to the rotation skip is `verification-stalled`, which covers a unit of any class. A dispatch
  condition applies only to a `ready` unit, because a circuit breaker never closes. Its
  `known.json` silences history; a missing file refuses, and `--seed` writes it. The same read serves
  rows that aren't units (staged intakes, undecomposed packages, undecided proposals), so `unit_id`
  and the unit flags are nullable and a unit condition must gate on `subject_kind`: a sent-back
  proposal's state is a held unit's. `admission-waiting` is raised while the server says `waiting`
  and fires only on `overdue`, because `known & raised` would prune a seeded key raised only when
  overdue; `verification-stalled` does the same with `verification_overdue`. Detail: `docs/operations/local-development.md`.
