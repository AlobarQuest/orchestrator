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

<!-- code-standards:end -->

## Known Non-obvious Invariants

- **Everything in this section must stay BELOW `<!-- code-standards:end -->`.**
  `code_standards.stanza.inject_stanza` replaces the whole `start`…`end` block in
  place and preserves only what surrounds it, and the canonical stanza template
  contains no invariants section. Until 2026-07-09 these bullets lived *inside*
  the block, one `code-standards init`/`sync` away from silent deletion.
  Verify with: re-rendering the block over this file must be a no-op.
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
- Production `/api` is M2M-only at the proxy: the Traefik dynamic config
  (`/data/coolify/proxy/dynamic/orchestrator.yaml` on the VPS) strips
  `X-authentik-*` headers from `/api` routes, so human-actor API routes are
  unreachable from a browser session unless a dedicated router applies the
  `/review` middleware chain (strip → Authentik forward-auth → proxy marker) to
  those paths — see the `orchestrator-promotion-human` router (WS-6.3). Quirk:
  the first same-origin POST behind forward-auth can return the app's 401
  (fetch follows the auth 302 and degrades to GET); the immediate retry works.
- Production observation ingestion requires an `ActorRole.SYSTEM` actor. The
  standing M2M credential is worker-role, so closeout-style observations use a
  temporary credential: merge into `ORCHESTRATOR_M2M_CREDENTIALS` + map it in
  `ORCHESTRATOR_M2M_ROLES`, restart, use, then revert. Verify EVERY env write
  landed before restarting — a roles entry without its matching credential
  fails startup validation closed and takes production down.
- Coolify's env PATCH endpoint intermittently 500s on this app; the reliable
  fallback is delete-by-env-uuid + recreate. All `/envs` API responses include
  `real_value` for every variable (DB URLs with passwords) — parse them
  in-process and print only whitelisted fields, never through ad-hoc shell
  pipelines.
- The work-unit authority envelope is a **cross-repo contract** with
  `AlobarQuest/factory-runner`, not a local data shape. It is pinned by a
  byte-identical `tests/fixtures/runner_authority_envelope.json` in both repos
  and the same `CONTRACT_SHA256` in `tests/contract/test_runner_envelope_contract.py`
  here and `tests/test_orchestrator_envelope_contract.py` there. Changing the
  envelope means changing both repos together; a one-sided edit fails the repo
  that was not updated. Before WS-6.4.0 no test crossed this boundary, and the
  two sides had silently diverged into mutually unsatisfiable fixtures.
- `AuthorityEnvelope.normalized()` defines what a human's authority approval
  actually attests. Fields outside `KNOWN_FIELDS` contribute only their *names*
  to the fingerprint, never their values — so a field carrying real authority
  (where code ships, which change class, what conformance was claimed) MUST be a
  known field. **Adding to `KNOWN_FIELDS` rewrites every authority fingerprint**,
  so its cost is proportional to the live ledger: free on 2026-07-09 (2 completed
  units, empty ledger), expensive later. No data migration is needed for existing
  units — every `authority_fingerprint()` call site is at write/activation time,
  and readiness compares stored columns rather than recomputing.
- factory-runner refuses to act unless `target_repo == current_repo`: it may only
  mutate the repository it checked out (`factory_runner/authority.py`). Dispatch
  must therefore resolve the target repository **per work unit**, from
  `authority.constraints.target_repository`, never from a process-global setting.
  A global target does not fail closed — it silently misroutes every fan-out unit
  to whichever repo was configured at process start.
- `src/orchestrator/kernel/` may not contain the string literal `dispatch`,
  `merge`, or other WS-4.2/mutation terms — `tests/architecture/test_ws32_scope_guards.py`
  scans runtime string literals, **including docstrings**, not just code.
