<!-- code-standards:start -->
# Code Quality (code-standards layer)

Standards reference: `~/Developer/code-standards/STANDARDS.md`

## Before writing a cross-cutting pattern — query Code Brain

Before implementing a recurring cross-cutting concern (logging, error handling,
auth, notifications, API conventions, secrets, …), query **Code Brain** — the
machine source of record for our paved roads — and follow its rules:

- `get_road("<slug>")` → the decided approach + rules + exemplars, or
- `get_rules(severity="BLOCK")` → the must-follow rules.

Do **not** infer the standard from existing code; it may predate the standard.
When you decide a new cross-cutting pattern, write it back (`add_road` / `add_rule`).

## Before declaring a non-trivial change done

1. Run `make check` — full-repo lint, type-check, and tests must be green.
2. Run `/code-review` — review the diff for correctness bugs and simplification opportunities.

Both gates apply to any change that touches logic, interfaces, or configuration.
Trivial fixes (typos, comment edits) may skip `/code-review` at your discretion.

## Enforcement

A diff-scoped Stop hook enforces this automatically: it runs the linters over your
changed files when the session ends and blocks completion if new violations are
introduced. Existing baseline violations are tracked and do not block.

## Canonical example module

The authoritative pattern for this repo's style is:

the cleanest, most idiomatic existing module in this repo

When writing new code, mirror the structure, naming conventions, and documentation
style of that module.

## Known Non-obvious Invariants

- On this machine, repo-local agent instructions live in `CLAUDE.md`. Treat
  `AGENTS.md` references from generic agent tooling as equivalent to checking
  `CLAUDE.md` unless a repo explicitly provides both files.
- Generic authority approvals satisfy work-unit readiness only. Authority-expanding
  standing-context updates require a named human approval bound to the exact
  standing-context fingerprint.
- Protocol smoke tests may manipulate time or lease expiry as deterministic fixture
  setup. Runtime recovery behavior itself must go through public API/CLI surfaces,
  not private service shortcuts.
- The default `make check` gate must resolve Python tools from the repo-local
  `.venv/bin` before global PATH. A global `pytest` can collect with the wrong
  interpreter and fail imports even when the uv-scoped suite is green.
- Local dogfooding must use a runtime database separate from `orchestrator_test`.
  The test fixtures intentionally drop and recreate the test database, so storing
  live orchestrator lifecycle state there will erase approved intake, decomposition,
  claims, and evidence during `make check`.
- Generated post-deploy acceptance criteria are verifier-owned. Public
  adjudication must reject generated post-deploy AC IDs, so post-deploy completion
  always flows through the WS-5.1 verifier evaluators and lifecycle guards.
- Production Coolify images must be amd64 or multi-arch. Local Apple Silicon
  Docker builds produce arm64 images by default; use `docker buildx build
  --platform linux/amd64 --push` or a multi-arch build for `sds.alobar.net`, and
  verify the running container image/digest after Coolify reports deployment
  finished.

<!-- code-standards:end -->
