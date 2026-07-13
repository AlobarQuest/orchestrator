# Task 3 Report: Context Preflight Service for WS-3.3

## Status

Completed on branch `codex/ws33-design`.
Commit: `feat: add WS-3.3 context preflight service`

## Files Changed

- `src/orchestrator/services/context.py`
- `tests/services/test_context_preflight.py`

## Scope

- Added `PreflightCommand`.
- Added `record_preflight(session, command, actor)`.
- Added `require_claim_context(...)` and `require_execution_context(...)`.
- Recorded context snapshots and local events in one transaction.
- Kept the slice service-only: no API, CLI, claim/start/evidence integration,
  dispatch, external publication, fixture intake, or status ledger.

## Red Evidence

Command:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/services/test_context_preflight.py -q
```

Observed result:

- Failed during collection with `ModuleNotFoundError: No module named 'orchestrator.services.context'`.

## Green Evidence

Command:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py -q
```

Observed result:

- `17 passed in 1.58s`

Additional checks:

```bash
PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/services/context.py tests/services/test_context_preflight.py
PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/services/context.py tests/services/test_context_preflight.py
```

Observed result:

- Ruff: `All checks passed!`
- Pyright: `0 errors, 0 warnings, 0 informations`

## Concerns / Deviations

- Required context tests create package revisions with required context at insert time because `work_package_revisions` is append-only.
- Allowed capabilities are derived from required context, `WorkUnit.required_capability`, and any allowed capabilities present in the revision authority snapshot.
- Execution preflight helper support records claim-bound snapshots when called with purpose `execution`, but no lifecycle integration was added in this task.

## Fix Pass

Findings addressed:

- Authority-expanding context approval now accepts the authority fingerprint produced by the existing `record_approval(subject_type="authority")` path.
- Execution preflight now requires active claim credentials: actor, attempt, lease token hash, unreleased status, and unexpired lease.

Red evidence:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_context_preflight.py -q
```

Observed result:

- `3 failed, 5 passed`
- Real authority approval returned `context_approval_mismatch`.
- `PreflightCommand` rejected `attempt` and `lease_token` keyword arguments.

Green evidence:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py -q
PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/services/context.py tests/services/test_context_preflight.py
PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/services/context.py tests/services/test_context_preflight.py
```

Observed result:

- Pytest: `19 passed in 4.89s`
- Ruff: `All checks passed!`
- Pyright: `0 errors, 0 warnings, 0 informations`

## Fix Pass 2

Finding addressed:

- Execution preflight idempotent replay now revalidates active claim credentials
  before returning an existing snapshot.

Red evidence:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_context_preflight.py::test_execution_preflight_replay_revalidates_active_claim_credentials -q
```

Observed result:

- Failed because replay with missing claim credentials returned the prior
  `ContextSnapshot` instead of `DomainError("active_claim_required")`.

Green evidence:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py -q
PATH="$PWD/.venv/bin:$PATH" ruff check src/orchestrator/services/context.py tests/services/test_context_preflight.py
PATH="$PWD/.venv/bin:$PATH" pyright src/orchestrator/services/context.py tests/services/test_context_preflight.py
```

Observed result:

- Pytest: `20 passed in 3.43s`
- Ruff: `All checks passed!`
- Pyright: `0 errors, 0 warnings, 0 informations`
