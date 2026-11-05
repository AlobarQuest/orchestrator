# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Unit lifecycle services (`src/orchestrator/services/lifecycle/`): claims, leases, versions, transitions and rotation claims

- `work_units.version` moves only on a state transition, in three places (`test_execution_writers.py`). Archive #30.
- `release_claim` alone ends a claim, from its listed callers (`test_execution_writers.py`); keep
  replay-before-release and unit-then-claim lock order. Archive #34.
- Any "may this unit run again" rule belongs in `claims._readiness_eligibility_error`, shared by reclaim,
  release and requeue (`test_reclaim.py`, `test_requeue.py`). Archive #45.
- A lease may only lengthen, and a lapsed lease ends its attempt (`test_lease_policy.py`,
  `test_drill_lapse_before_submit.py`). Archive #89.
- A lease has three writers, each sized by `claim_lease` (`test_execution_writers.py`). A per-claim rule covers
  all three, except that the rotation change window gates starts only (ADR-0055 amendment 2). Archive #90.
- `release_claim` runs only on failure, cancel, reclaim, release and expiry recovery, so a completed
  unit keeps an unreleased lapsed claim. Claim predicates must gate on unit state
  (`claims.CLAIM_HOLDING_STATES`) and the newest attempt, never on `released_at IS NULL`. History:
  `docs/history/claude-md-invariants-archive.md` #96.
- A unit's completion satisfies, in `_perform_transition`'s transaction, the pending `work_unit`
  dependencies that require it `completed` (exact match); no other condition or kind. A human-act
  dependency is resolved only at `/review/dependencies/{id}/resolution`, whose detail is
  `HumanActDetail` plus the secret scan. Detail: `docs/operations/driving-a-unit.md`.
