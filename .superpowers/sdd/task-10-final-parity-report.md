# Task 10 final parity correction

The closure review found that the CLI wrapped valid top-level API arrays in an
`items` object. That changed the response contract for evidence and history
list operations.

The HTTP client now preserves both JSON objects and arrays while continuing to
reject scalar and null success responses. The genuine HTTP parity test compares
the actual history API array with CLI JSON produced through HTTPX, FastAPI
authentication and routes, lifecycle services, and PostgreSQL.

Verification:

- CLI suite: 42 passed.
- Full `make check`: Ruff, formatting, and Pyright clean; 445 tests passed.
- Portfolio code standards and `git diff --check`: passed.
