# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The bump-driver lane (`src/bump_driver/`)

- The `bump-driver` lane acts as SYSTEM only on a standing `dependency-update` revision in a
  repository granted `dependency_updates`, and on a unit whose own envelope says so; it reads
  `resolved` records as well as `approved`, since `work_watcher` retires a record before its unit
  lands. A dispatch record that started no run holds its idempotency key and moves no version, so
  the lane's key gains a suffix per such record, or it would replay the skip for ever. Its mirrors
  run only git and `fresh` proves them clean at `origin/main`, because `factory`'s conformance claim
  runs `code-standards` in the mirror; `factory`'s environment is an allowlist holding no credential
  but the Brain keys, so the lane passes `--intake-file` and `--assume-checkout-current`. It never
  requeues a unit and never re-dispatches a run nobody claimed. Detail: `docs/operations/landing-lanes.md`, "The bump
  driver".
