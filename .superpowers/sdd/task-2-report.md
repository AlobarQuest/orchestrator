Status: green

Commit: `feat: add WS-3.3 context policy`

Files changed:
- `src/orchestrator/kernel/context.py`
- `tests/kernel/test_context_policy.py`
- `.superpowers/sdd/task-2-report.md`

Red evidence:
- Initial focused run in this worktree did not reproduce the expected red import failure. `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` returned `7 passed in 0.01s`, which indicates the standing-context implementation already existed before this task work began.
- Required verification was still red on the starting implementation:
  - `PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` failed with `F401` for an unused `typing.Any` import and `E501` for an overlong generator expression line in `src/orchestrator/kernel/context.py`.
  - `PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` failed with 15 errors, primarily from set construction over `object`-typed capability values in `src/orchestrator/kernel/context.py` and overly broad `**overrides` typing in `tests/kernel/test_context_policy.py`.

Green evidence:
- `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` -> `7 passed in 0.01s`
- `PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `All checks passed!`
- `PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `0 errors, 0 warnings, 0 informations`

Concerns/Deviations:
- The task brief said `src/orchestrator/kernel/context.py` did not exist and expected a red import failure first. In the actual shared worktree, both the module and the tests were already present and the focused pytest run started green. I preserved that existing implementation and limited changes to the lint/typecheck failures plus this report.
- No database, service, API, CLI, migration, or runtime orchestration behavior was added. The work remained pure standing-context policy only.
