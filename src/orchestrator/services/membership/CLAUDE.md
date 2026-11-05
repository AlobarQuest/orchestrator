# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Membership services (`src/orchestrator/services/membership/`): member-table writers

- Only `services/membership/proposals.py` writes the member tables, reached only by a human
  confirming on `/review`, with one exception: `revert_hold.place` inserts a ceiling hold, only
  through `revert_landed.record_revert_landed`, in either landing lane's landing transaction (inert
  or estate) for a landed SDS revert, and never updates or deletes. Reads return frozen records,
  never ORM objects. `test_membership_writers.py` pins both writers, the hook's two callers, every
  write route and its role. The deploys service's wire value is `rollouts` (word guards). Detail:
  `docs/operations/membership.md`.
