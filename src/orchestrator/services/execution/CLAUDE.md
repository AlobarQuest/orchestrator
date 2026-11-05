# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Execution services (`src/orchestrator/services/execution/`): dispatch

- Dispatch takes the target from each unit's envelope (`test_dispatch_routes_to_the_units_own_target_repository`). Archive #16.
- The orchestrator assigns the dispatch ordinal; a retry reuses the `idempotency_key` (`test_dispatch.py`).
- `_blocked_reason` normalizes the envelope once (`test_dispatch_uses_one_normalized_authority_snapshot`). Archive #108.
