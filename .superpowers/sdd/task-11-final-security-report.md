# Task 11 Final Security and Effect Report

Completed the final Task 11 security and canonical-effect verification on top of `a422ed5`.

## Confirmed Security Behavior

- An attacker-preseeded review-session cookie is rejected and rotated on authenticated
  GET; a subsequent POST succeeds only with the newly issued signed cookie and its bound
  token.
- Review cookies remain signed, `Secure`, `HttpOnly`, and `SameSite=strict`; tokens remain
  bound to actor, unit, action, session, idempotency key, and expiry.
- Empty and weak CSRF secrets are rejected below 32 bytes. Missing signing configuration
  now fails stably with HTTP 503 `csrf_unavailable`.
- Malformed base64/invalid UTF-8 CSRF input returns HTTP 403 `csrf_rejected`, not a 500.
- Inactive human identities are denied on both GET and POST; worker POSTs are forbidden.
- Stale-version forms return HTTP 409 `version_conflict` with the current version.

## Confirmed Canonical Effects

- Action-approval exact replay converges to one Approval and one canonical Event. The
  Event now uses the submitted idempotency key rather than a derived suffix.
- Cancellation transitions to `cancelled` and persists its supplied reason.
- Retry authorization increases the bounded attempt budget and returns failed work to
  `ready`.
- Review and cancellation reasons are rendered in both unit detail and the read-only
  Evidence Pack.
- Evidence and adjudication projections explicitly prove row-local current/superseded
  labels.

## TDD and Verification

- Expanded RED: 16 web tests ran; 13 passed and three exposed the remaining gaps:
  CSRF configuration mapped to 409, approval Event identity did not use the command key,
  and persisted transition reasons were not rendered.
- Focused web plus related lifecycle/service verification: 57 passed.
- Full PostgreSQL gate:
  `TEST_DATABASE_URL=postgresql+psycopg://devon@127.0.0.1:5432/orchestrator_test make check`
  — Ruff clean, formatting clean, Pyright clean, 461 tests passed.
- Portfolio code standards and `git diff --check` passed.

No browser was launched. No migration, CLI, API route, or container scope was added.
