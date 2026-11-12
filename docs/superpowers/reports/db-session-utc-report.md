Outcome: done

# Report: every orchestrator database session runs in UTC (W-104)

- **PR:** #672 (`hq/db-session-utc`), ready for review, not merged.
- **Code head:** `4b1ad2ed`. This report is committed after it.
- **Base:** `origin/main` at `11eb2563`. I confirmed each fact in the handoff there.

## What I built

- **The factory.** `make_engine(url, **kwargs)` in `src/orchestrator/db.py` builds every engine. It
  sets `options` to `-c TimeZone=UTC`, appended to any `options` the caller passes in
  `connect_args`. The module's `engine` uses it.
- **The test helper.** `make_test_engine(url=TEST_DATABASE_URL, **kwargs)` in
  `tests/_support/database.py` calls the factory and imports it inside the function.
  `orchestrator.db` reads `ORCHESTRATOR_DATABASE_URL` at import (`get_settings` is cached, and
  `database_url` is required). That module sets the URL, and ruff's import order would load
  `orchestrator.db` first if `conftest.py` imported it at the top.
- **A DST default beneath the pin.** In `tests/_support/database.py`,
  `set_default_zone_beneath_the_pin()` runs `ALTER DATABASE … SET timezone =
  'America/New_York'` on the process's test database. It runs from the session-scoped
  `_test_database` fixture, after the worker database is created. The libpq startup option
  outranks a database-level default, so sessions stay UTC. Without the pin they fall back to the
  DST zone on any server, CI's included.

## Engine sites

I counted seven sites at the base. Six are test sites, and all six use the factory.

| Site | Before | After |
|---|---|---|
| `src/orchestrator/db.py` 6 | `create_engine(url, pool_pre_ping=True)` | `make_engine(...)` |
| `tests/conftest.py` `_schema` | `create_engine(TEST_DATABASE_URL)` | `make_test_engine()` |
| `tests/conftest.py` `migrated_engine` | same | `make_test_engine()` |
| `tests/conftest.py` `db_session` | same | `make_test_engine()` |
| `tests/_support/database.py` 86 `_admin_engine` | `create_engine(url, isolation_level="AUTOCOMMIT")` | `make_test_engine(url, isolation_level="AUTOCOMMIT")` |
| `tests/persistence/test_migrations.py` 39 | `create_engine(TEST_DATABASE_URL)` | `make_test_engine()` |
| `tests/persistence/test_migrations.py` 857 | same | `make_test_engine()` |

- **The admin engine.** It reads no timestamps, so it could have stayed bare. I routed it through
  the factory anyway: every test engine then has the same session zone, at no cost, and no test
  file still imports `create_engine`.
- **`migrations/env.py` 30.** It still uses `engine_from_config`, as the handoff requires.
  Migrations hold no SQL that depends on the session zone: `git grep` finds no `date_trunc`,
  `::date`, `AT TIME ZONE`, `current_date`, `to_char`, or `localtime` in `migrations/` or `src/`.
- **Direct connections.** `src/` holds no `psycopg.connect` and no async engine, so nothing
  connects around the factory.

## Tests

All tests are in existing files.

`tests/test_foundation.py`:

- `test_the_production_engine_runs_every_session_in_utc`: checks that `orchestrator.db.engine`
  points at this process's test database. That's true because `tests/_support/database.py` sets
  `ORCHESTRATOR_DATABASE_URL` before anything imports `orchestrator.db`. It then checks that the
  database's default is `TimeZone=America/New_York` (from `pg_db_role_setting`), and that
  `SHOW TimeZone` is `UTC`.
- `test_the_engine_factory_keeps_a_callers_connect_options`: a caller's
  `-c statement_timeout=12345` is kept beside the UTC pin.
- `test_every_engine_in_src_is_built_by_the_utc_factory`: `src/` holds exactly one
  `create_engine(` or `engine_from_config(` call, and it's in `orchestrator/db.py`.
- `test_control_every_engine_builder_call_is_reported`: a control showing that a second call in
  `db.py`, or a call in another file, is reported.

`tests/services/test_slo_report.py`:

- `test_intake_to_first_work_is_true_elapsed_time_across_a_dst_change`: one revision registered
  at 2026-10-31 12:00 UTC (EDT), with its first claim two days later (EST). It sets no session
  zone of its own and goes through `slo_report` on the factory's engine. Expected latency:
  172,800 s.

## Red-then-green proofs

Every run used `TZ=UTC`.

1. **Mac test Postgres (`America/New_York`), factory option removed:** the zone test failed with
   `assert 'America/New_York' == 'UTC'`, and the caller-options test failed the same way. The
   latency test failed with `assert 169200.0 == 172800.0`, one hour short.
2. **`git archive` of `11eb2563` with only the latency test added, Mac Postgres:** the latency test
   failed with `assert 169200.0 == 172800.0`. It's green on the branch.
3. **CI's server.** A `postgres:16-alpine` container with the same environment as `quality.yml`'s
   `postgres` service (`POSTGRES_HOST_AUTH_METHOD=trust`, no `TZ`), on port 55432.
   `SHOW TimeZone` read `UTC`.
   - Pin removed, no database default (CI before this PR): the latency test **passed**. The zone
     test failed only on its new database-default check. Without the default, both would pass
     with the pin removed, which is the gap HQ named.
   - Pin removed, database default in place: both failed (`'America/New_York' == 'UTC'`, and
     `169200.0 == 172800.0`).
   - As built: both passed.

   I removed the container after the runs.
4. **Engine-count guard:** an extra `create_engine(` appended to `db.py` failed it
   (`['orchestrator/db.py:19', 'orchestrator/db.py:24']`, `2 == 1`).

So both tests fail in CI with the pin removed, because the database default beneath the pin is a
DST zone whatever the server's default.

## Checks

- **`make check`:** run on `orchestrator_test_hq_w_104` with `PGTZ` unset, against the Mac's
  `America/New_York` server, after the last code change. ruff, ruff format, and pyright are clean
  (0 errors). Pytest collected 14,628: 14,627 passed and 1 skipped.
- **`/code-review` (low) on `origin/main...HEAD`:** no findings.

## Wall-clock reads

I surveyed `src/` for `.date()`, `.hour`, `.time()`, `strftime`, `isoformat` used in text, and
template formatting of database-loaded datetimes, including values the API serves and a lane
reads back. Where the server zone isn't UTC, the result changes only as described in the
following lists. Where it's already UTC, nothing changes. I didn't read the deployed server's
zone.

### Day boundary moves (approved by HQ: Option 2)

- `src/execution_watch/decide.py` 242 and 310: `row.entered_at.date().isoformat()` in the alert
  text "since <date>". `entered_at` is loaded from the database at
  `services/reporting/execution_status.py` 215. It's served with its offset as a plain `datetime`
  (`api/schemas/reporting.py` 137) and parsed with that offset kept (`execution_watch/client.py`
  85). Wherever the server zone isn't UTC, **the two "since" dates become UTC dates.** For
  example, 20:00-05:00 on November 3 printed "November 3" and now prints "November 4". The date is
  message text only: `_row_key` (134 to 139) builds the alert key from the condition and ids, not
  from `entered_at`. I left `src/execution_watch/` unchanged.

### Offset notation only: same instant, different printed wall clock

- **`isoformat()` in text:** `services/reporting/dead_letter.py` 142 and 172,
  `services/reporting/evidence_pack.py` 464, 515, and 579,
  `services/reporting/pending_decisions.py` 363, `services/reconciliation/consistency.py` 222, and
  `templates/decomposition_proposal.html` 193.
- **Bare datetimes in templates:** `_intake_content.html` 24, `member_proposal.html` 19 and 75,
  `unit.html` 36, 66, 108, 120, 124, and 128, `staged_intake.html` 7 and 33,
  `decomposition_proposal.html` 12 and 15, `release_evidence_pack.html` 32, and
  `evidence_pack.html` 7, 9, and 12.
- **An API-served value a lane prints:** `bump_driver/cli.py` 477, the `since.isoformat()` of an
  event's `occurred_at`, parsed at 402.
- **Stored in a JSON payload.** Neither value is ever compared as text:
  - `services/lifecycle/claims.py` 233 writes `expires_at` into the `claim.renewed` event payload.
    Its one reader is `_renew_replay` at `claims.py` 803 to 807. That function matches a replay on
    `action`, `subject_id`, `actor_id`, role, `attempt`, `expected_version`, and
    `lease_token_hash` (790 to 798), never on `expires_at`. It then parses `expires_at` back to an
    instant with `datetime.fromisoformat` and returns it in the `LeaseGrant`. A stored `-05:00`
    string and a `+00:00` one parse to the same instant.
  - `services/reconciliation/reconciliation_detection.py` 458 writes `created_at` into
    `stored_state`. `stored_state` is written to the condition row (`reconciliation.py` 186) and
    to its event payload (352). It's read only for display, at `web.py` 649 and in the `unit.html`
    98 template. The condition dedupes on an idempotency key derived from `lineage_hash` over
    `key_facts` (`reconciliation.py` 141 to 157), never on `stored_state`. No other `src/` code
    reads it.
- **Other hashes over loaded values:** none include a datetime. I checked
  `kernel/context.py` 54, `services/execution/dispatch.py` 360,
  `services/release/observations.py` 119 (hashes the caller's `observed_at`, not a loaded value),
  `services/reconciliation/reconciliation.py` 101 and 106, and `services/membership/proposals.py`
  76 and 258. The last one's `proposal_preview` holds no datetime.

### Unchanged either way

- **Converted to a named zone or to UTC before the wall clock is read:**
  `orchestrator/factory_policy.py` 458 and 591, `estate_lander/cli.py` 297,
  `tool_installer/window.py` 62, `rotation_worker/adapters/bws_machine_token.py` 130, and
  `services/reporting/slo_report.py` 152 (#671's `.astimezone(UTC)`, left as it is, with #671's
  test).
- **Times the process made itself, not database values:** `rotation_worker/worker.py` 301 and
  379, `landing_ledger/cli.py` 372, and `rotation_proposer/cli.py` 456 and 549.

## Files changed

- `src/orchestrator/db.py`
- `tests/_support/database.py`
- `tests/conftest.py`
- `tests/persistence/test_migrations.py`
- `tests/services/test_slo_report.py`
- `tests/test_foundation.py`
- `docs/superpowers/reports/db-session-utc-report.md`

## Self-review

- **Option precedence.** The pin is a libpq startup option, so it outranks a database or role
  default and the server's `postgresql.conf`. Under a connection pooler it would reach Postgres
  only if the pooler forwards startup options, and the tree has none.
- **What a caller can't do.** A caller's `options` is kept, but a caller can't drop the pin. That's
  the intent.
- **A shell `PGTZ`.** If a shell exports `PGTZ`, libpq sends it as a startup parameter too. Every
  run in this report had it unset. The project's documented value is `UTC`, which agrees with the
  pin either way.
- **The database default persists.** `ALTER DATABASE` changes the test database for later
  connections. That's harmless: every engine goes through the factory. A stray bare connection to
  a test database would now see New York time, and that's the point of the default.

## Concerns

- I didn't read the deployed orchestrator's Postgres `TimeZone`. If it's already UTC, production
  sees no change. If not, the "since" dates and the printed offsets change as listed.
- **Item 4 stop point.** The first pass stopped there and asked HQ. HQ chose Option 2, and the PR
  ships as is.

## Deviations

- The test engine sites call a new helper, `make_test_engine`, not `make_engine` directly. The
  reason is the import-order constraint in "What I built". It still calls the factory.
- The DST default beneath the pin (`set_default_zone_beneath_the_pin`) was added at HQ's
  direction, after the CI check.

## Follow-ups

- `docs/operations/local-development.md` tells sessions to export `PGTZ=UTC`. The factory makes
  that unnecessary. The PR body lists it, and I didn't edit it.

## Standards followed

- **Code Brain rule 3: structured JSON logs to stdout, not files.** The diff adds no logging, so
  the rule isn't exercised.
- **Code Brain rule 4: never swallow an exception silently.** The factory and the helpers catch
  nothing. `set_default_zone_beneath_the_pin` uses `try`/`finally` only to dispose of the admin
  engine, and any error propagates and fails the session fixture. The tests assert directly.
- **Code Brain rule 5: never log secrets, tokens, or request bodies.** Nothing added prints or logs
  anything. The factory doesn't log or format the database URL. The engine-count guard reports
  file and line positions only.
