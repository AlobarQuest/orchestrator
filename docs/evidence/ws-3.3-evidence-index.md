# WS-3.3 evidence index

This index records local evidence for approved intent package
`ws-3.3-protocol-smoke-runtime-semantics` revision 1. The package hash is
locally recorded in
`docs/superpowers/plans/2026-07-06-ws33-protocol-smoke-runtime-semantics.md` as
`7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`.

Task 10 evidence was recorded by `codex` at `2026-07-06T16:13:06Z` from branch
`codex/ws33-design`. The pre-Task-10 implementation head was
`946d356 test: add WS-3.3 architecture guards`. This is an evidence index, not
a completion claim for deployment, PR CI, or merge.

## Verification summary

- Task 9 scope guards:
  `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test uv run pytest tests/architecture/test_scope_guards.py tests/architecture/test_ws33_scope_guards.py -q`
  passed with `11 passed in 0.72s`.
- Task 9 focused suite:
  `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test uv run pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py tests/services/test_protocol_fixture_intake.py tests/services/test_status_ledger.py tests/protocol/test_ws33_smoke.py -q`
  first failed inside the default sandbox because TCP access to PostgreSQL at
  `192.168.97.2:5432` was blocked with `Operation not permitted`. The same
  command was rerun with approved DB access and passed with `31 passed, 1
  existing Starlette/httpx warning in 7.99s`.
- Task 10 final review focused suite:
  `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test uv run pytest tests/services/test_lifecycle_guards.py tests/services/test_context_preflight.py tests/api/test_context_api.py tests/architecture/test_scope_guards.py tests/protocol/test_ws33_smoke.py -q`
  first failed inside the default sandbox because TCP access to PostgreSQL at
  `192.168.97.2:5432` was blocked with `Operation not permitted`. The same
  command was rerun with approved DB access and passed with `33 passed, 1
  existing Starlette/httpx warning in 11.70s`.
- Full `make check`:
  `PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check`
  passed with Ruff, Pyright, and `643 passed, 1 existing Starlette/httpx warning
  in 73.47s`.

## Prior local WS-3.3 task evidence

- Task 1 migration and persistence model:
  `.superpowers/sdd/task-1-report.md` records focused red evidence for missing
  `context_snapshots`/`ContextSnapshot`, then green evidence for
  `tests/persistence/test_migrations.py tests/persistence/test_constraints.py`
  with `21 passed`. A repo-level `make check` at that point failed on unrelated
  Ruff format drift outside Task 1.
- Task 2 context policy:
  `.superpowers/sdd/task-2-report.md` records policy correction red evidence
  and final focused green evidence for
  `tests/kernel/test_context_policy.py` with `11 passed`, plus Ruff and Pyright
  passing on touched files.
- Task 3 context preflight service:
  `.superpowers/sdd/task-3-report.md` records an initial
  `ModuleNotFoundError` red, then green evidence for
  `tests/kernel/test_context_policy.py tests/services/test_context_preflight.py`
  with `20 passed`, plus Ruff and Pyright passing on touched files.
- Task 4 context runtime binding:
  local commit history records `3aea8fb feat: bind WS-3.3 protocol context at
  runtime`, `9fc5872 fix: align WS-3.3 preflight approval and claim binding`,
  `5ef948a fix: revalidate WS-3.3 execution preflight replay`, and
  `56419e4 fix: close WS-3.3 context binding gaps`. Exact focused command
  output is not recorded in this index beyond the fresh Task 9 focused suite.
- Task 5 API/CLI context surface:
  `.superpowers/sdd/task-5-report.md` records API/CLI context verification:
  `tests/api/test_context_api.py tests/cli/test_context_cli.py tests/cli/test_cli_http_parity.py tests/cli/test_cli_contract.py`
  passed with `41 passed`, the broader API/CLI suite passed with `118 passed`,
  review regressions passed, and Ruff/Pyright passed on touched files.
- Task 6 protocol fixture intake:
  `.superpowers/sdd/task-6-report.md` records green evidence for
  `tests/services/test_protocol_fixture_intake.py tests/cli/test_package_intake_cli.py`
  with `29 passed`, an expanded package/decomposition/API/CLI suite with `77
  passed`, and Ruff/Pyright passing on touched files.
- Task 7 status ledger:
  local commit history records `46d7bce feat: add WS-3.3 status ledger
  projection`. Exact focused command output from that task is not separately
  recorded in this index beyond the fresh Task 9 focused suite.
- Task 8 protocol smoke suite:
  local commit history records `5778dc6 test: add WS-3.3 protocol smoke suite`.
  Exact focused command output from that task is not separately recorded in this
  index beyond the fresh Task 9 focused suite.
- Task 9 architecture guards:
  current Task 9 guard command passed with `11 passed`, after review fixes
  tightened the status-ledger route-method guard and automatic-merge scanner.
  The scanner now catches token-split command arguments such as `gh pr merge`
  and `git push origin main`.
- Task 10 final review fixes:
  local changes bind authority-expanding standing-context approvals to exact
  context fingerprints, default execution preflight comparison to the
  claim-time context snapshot when no explicit snapshot is supplied, and expose
  lease-expiry reclaim through public API/CLI protocol surfaces so the WS-3.3
  smoke suite no longer calls the reclaim service directly.

## Scope guard evidence

- `tests/architecture/test_ws33_scope_guards.py` asserts there is no
  `workflow_dispatch`, `factory-runner`, or `factory_runner` code in runtime
  source or workflows.
- Runtime source has no `factory_events` or `factory_events.*` import. The
  existing chain verification subprocess call remains a verifier path, not an
  imported publisher.
- Registered FastAPI `APIRoute` objects expose `/api/v1/status-ledger` only as
  `GET`; the guard unions methods across duplicate matching paths so hidden
  non-GET status-ledger routes cannot be masked by OpenAPI omission or route
  overwrite.
- Runtime source and workflows have no guarded automatic merge strings such as
  `gh pr merge`, `/merges`, `merge_pull_request`, `auto_merge`, `automerge`, or
  `git push origin main`.
- Executable package intake still rejects closed packages through
  `load_package_intake_payload`.
- The closed-package protocol fixture path is explicitly named
  `protocol_fixture`, sets `protocol_fixture_only: true`, is present in
  `INTAKE_SOURCES`, and is wired through the package-intake protocol fixture
  source constant.
- `tests/architecture/test_scope_guards.py` keeps the production POST route
  inventory explicit, including the WS-3.3 preflight and reclaim routes.

## Baseline and standards evidence

- Prior WS-3.3 baseline checks are partially recorded in `.superpowers/sdd`
  task reports and commit history as listed above.
- Foundation/project-standards findings are not recorded in this Task 9 evidence
  index beyond the locally available reports. No separate foundation or
  project-standards evidence artifact was found during Task 9.
- Full branch `make check` passed during Task 10 as recorded above.

## Explicitly absent evidence

- No production deployment evidence exists.
- No factory-runner dispatch evidence exists.
- No external `factory-event/v1` publication evidence exists.
- No automatic merge evidence exists.
- No tracker canonicalization evidence exists.
- No WS-3.3 UI evidence exists.
- No independent verifier evidence exists.
- No full PR CI evidence exists for Task 9 until a PR/check run is actually
  executed.
