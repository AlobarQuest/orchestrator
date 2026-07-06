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

## Fix Pass

Findings addressed:
- Enforced the `required` lower bound for `capabilities`; current contexts that drop required capabilities are rejected.
- Enforced the `required` lower bound for `authority_profile`; current contexts below the required profile are rejected.
- Rejected malformed `capabilities` payloads instead of normalizing them into an accepted empty list.
- Renamed the same-scope standards-change reason to `standards_changed_within_floor` so it remains truthful for any standards change that stays above the required floor.

Red evidence:
- Added focused tests for dropping a required capability, using an authority profile below the required floor, and malformed `capabilities`, plus updated the standards-change reason expectation.
- `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` failed with 4 assertion failures:
  - `test_same_scope_newer_standard_version_is_accepted` still returned `standards_newer_or_equal`
  - `test_dropping_required_capability_is_rejected` was incorrectly accepted as `same_scope`
  - `test_lower_than_required_authority_profile_is_rejected` was incorrectly accepted as `same_scope`
  - `test_malformed_capabilities_is_rejected` was incorrectly accepted as `same_scope`

Green evidence:
- `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` -> `10 passed in 0.01s`
- `PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `All checks passed!`
- `PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `0 errors, 0 warnings, 0 informations`

Commit SHA:
- `4d9fece`

Concerns:
- The starting worktree already contained the WS-3.3 Task 2 implementation and tests, so this pass was a targeted policy correction on top of existing branch work rather than a first implementation.

## Fix Pass 2

Finding addressed:
- Rejected malformed capability list members instead of coercing them with `str(...)`.

Red evidence:
- Added `test_capabilities_with_non_string_members_is_rejected`.
- `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` failed because `["repository_read", 1, None]` was classified as `authority_expanding` instead of `missing_required`.

Green evidence:
- `PATH="$PWD/.venv/bin:$PATH" pytest tests/kernel/test_context_policy.py -q` -> `11 passed in 0.01s`
- `PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `All checks passed!`
- `PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/kernel/context.py tests/kernel/test_context_policy.py` -> `0 errors, 0 warnings, 0 informations`
