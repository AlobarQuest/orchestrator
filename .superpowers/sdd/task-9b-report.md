# Task 9B Report

Completed the lifecycle API contract and mutation concurrency remediation on top of `8845aa3`.

## Delivered

- Made every API mutation request inherit `CommandBase`, requiring a bounded
  `idempotency_key` and non-negative `expected_version`.
- Added optimistic-version enforcement to claims and evidence writes, including stable
  `version_conflict` state/version context.
- Added registration preconditions (`expected_version=0`) and event-backed exact replay
  identity for revisions and approved units. Exact retries return the original resource;
  reuse with different request content returns stable `idempotency_conflict`.
- Added idempotent lease renewal. Exact replay returns the originally recorded expiry and
  never extends the lease twice; changed request identity conflicts stably.
- Added explicit configured M2M role assignment so authenticated system and verifier
  identities can exercise their lifecycle authority through the same API boundary.
- Preserved thin routes and existing service call contracts. No route queries ORM models.
- Added a PostgreSQL end-to-end API contract test covering:
  revision/unit registration and exact replay; readiness; ready; claim; renew; start;
  block; request approval; approve; evidence append/list; submit; verify; review;
  complete; fail; retry; cancel; history; worker completion denial; illegal-edge denial;
  version-conflict context; and transaction persistence.
- Added focused claim/renew service regressions for version conflicts, exact renewal
  replay, and conflicting key reuse.

No schema migration, CLI, UI, or container changes were required.

## TDD and Verification

- Initial focused RED exposed missing claim version parameters and renewal idempotency
  support. The first PostgreSQL invocation also identified an environment-only fallback
  role mismatch; reruns used the repository's established local `devon` test URL.
- Focused API and service suite:
  `TEST_DATABASE_URL=postgresql+psycopg://devon@127.0.0.1:5432/orchestrator_test uv run pytest tests/api tests/services -q`
  — 92 passed.
- Focused replay/lifecycle regression suite — 22 passed.
- Full gate:
  `TEST_DATABASE_URL=postgresql+psycopg://devon@127.0.0.1:5432/orchestrator_test make check`
  — Ruff clean, formatting clean, Pyright clean, 395 tests passed.
- Portfolio code standards check and `git diff --check` passed.
- Diff review found no ORM access in routes, new suppression comments, migration changes,
  or unrelated surface expansion.
