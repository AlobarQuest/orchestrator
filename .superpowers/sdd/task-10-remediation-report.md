# Task 10 Remediation Report

Remediated the Task 10 review findings on top of `6590251`.

## Delivered

- Converted HTTPX timeouts into stable `api_timeout` CLI errors.
- Converted connection, DNS, TLS, and other HTTPX transport failures into stable
  `api_unavailable` CLI errors.
- Sanitized both paths so exception text, API endpoint details, tokens, and environment
  values are never emitted.
- Converted malformed success JSON and scalar success values into stable
  `invalid_response` errors. Valid list responses are represented as `{"items": [...]}`.
- Kept non-JSON HTTP error responses on the sanitized `http_error` contract without
  echoing response bodies or request URLs.
- Renamed the ambiguous `approve-action` command to `record-approval`, preserving the
  lifecycle `approve` command. The replacement exposes and validates explicit
  `--subject-type authority|action`, `--idempotency-key`, `--expected-version`, and
  `--reason` options.
- Extended the architecture guard to reject imports from `orchestrator.services` in
  addition to persistence, SQLAlchemy, and kernel imports.
- Replaced acceptance reliance on a mocked `orchestrator.cli.request` with a genuine
  in-process HTTP path:
  - the CLI's real `request()` creates the HTTPX client;
  - an HTTPX transport forwards the request into the actual FastAPI app;
  - real M2M authentication headers traverse the API dependency;
  - lifecycle services and PostgreSQL execute normally;
  - independently invoked API and CLI results are compared for state, version, event ID,
    error code, current version, and recovery.

## TDD and Verification

- RED: focused tests produced five expected failures for uncaught transport/timeout
  exceptions, unsafe malformed/scalar success handling, and the missing
  `record-approval` command.
- Focused hardening GREEN: 40 tests passed.
- Real HTTP/auth/PostgreSQL parity: 1 passed.
- Complete CLI suite: 42 passed.
- Full PostgreSQL gate:
  `TEST_DATABASE_URL=postgresql+psycopg://devon@127.0.0.1:5432/orchestrator_test make check`
  — Ruff clean, formatting clean, Pyright clean, 445 tests passed.
- Portfolio code standards and `git diff --check` passed.

No API, service, persistence, migration, UI, or container behavior changed.
