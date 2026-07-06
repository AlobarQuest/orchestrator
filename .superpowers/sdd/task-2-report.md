# Task 2 Report: Pure Standing-Context Policy

## Scope

- Created `src/orchestrator/kernel/context.py`
- Created `tests/kernel/test_context_policy.py`

## TDD Evidence

### Red

1. Added `tests/kernel/test_context_policy.py` first.
2. Ran:

```bash
pytest tests/kernel/test_context_policy.py -q
```

3. Result:
   - failed during collection because the new policy module was not present yet
   - initial local output was `ModuleNotFoundError: No module named 'orchestrator'` under the bare command from the repo root
4. Re-ran with the repo source path configured for local package imports:

```bash
PYTHONPATH=src pytest tests/kernel/test_context_policy.py -q
```

5. Result:
   - failing tests against the new policy surface before implementation

### Green

1. Implemented `src/orchestrator/kernel/context.py` with:
   - `normalize_standing_context(...)`
   - `context_fingerprint(...)`
   - `classify_context_update(...)`
   - `ContextDecision`
2. Ran:

```bash
PYTHONPATH=src pytest tests/kernel/test_context_policy.py -q
```

3. Result:

```text
7 passed in 0.01s
```

## Notes

- The implementation is pure policy logic only; there are no database, service, API, CLI, or migration imports.
- Authority-profile expansion is fail-closed via an explicit local rank map, with unknown profile changes treated as expansion.
- Version comparison supports dotted integer versions like `1`, `1.0`, and `1.2.3`, and treats non-matching versions as stale unless the strings are identical.
