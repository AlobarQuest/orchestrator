Outcome: done

# FE-3 build report: the orchestrator verifies approvals against the database chain

Handoff: sds-hq `state/handoffs/lanes-inc-4-fe-3-orchestrator-chain.md`. Branch
`hq/lanes-inc-4-fe-3-orchestrator-chain` from orchestrator `ef51a15c`. Worker W-141.

## What was built

With `FACTORY_EVENTS_READ_DSN` set, `package_sources` verifies the factory-events database chain
and reads its `package.approved` events in one read-only, repeatable-read snapshot. Any error
raises `PackageSourceError`, and the file isn't read. With neither DSN set, the file path is
unchanged. No DSN is set anywhere until FE-5, so nothing that runs today changes.

1. **`src/orchestrator/factory_chain_read.py`** is a child module that
   `package_sources` runs as `sys.executable -m orchestrator.factory_chain_read`, with
   security-standards' `src` on `PYTHONPATH`. It's the same interpreter and path setup that
   today's `factory_events verify` subprocess uses. It works as follows:
   - It opens one connection with `read_only = True` and `REPEATABLE_READ`, in one transaction.
     It streams FE-1's reader `SELECT` through a named cursor into
     `factory_events.store.walk_records`, collecting `package.approved` events as the rows pass.
   - It prints one JSON record on stdout: `verified` with the events (exit 0), or `broken` or
     `failure` with the `seq`, a 12-character head prefix and an error *type* (exit 2).
   - The DSN and a driver's error text are never printed.
2. **`src/orchestrator/package_sources.py`** gains a new function,
   `_verified_package_approval_events`, that routes the read:
   - **Read DSN set:** it calls `_database_package_approval_events`, which runs the child and
     refuses on anything except a verified record. A child that can't run (`OSError`,
     `TimeoutExpired`) and unparseable output each refuse with the cause chained (`from exc`).
     A `broken` or `failure` record refuses with a fixed message that names only the `seq` or an
     identifier-shaped error type. The child's stderr isn't relayed. `_verify_factory_chain` and
     `_iter_package_approval_events` aren't called.
   - **Only the append DSN set:** it refuses. FE-1's `verify` falls back to the append DSN, so
     the unchanged file path would verify the database but match events from the file. That's
     the split FE-2's `default_chain_checker` refuses, and this refuses it the same way.
   - **Neither set:** `_verify_factory_chain` and then `_iter_package_approval_events`, exactly
     as before.

   `_verified_approval_from_events` now takes the events list. The six-field matcher
   (`_approval_event_to_verified`) is untouched and serves both chains.
3. **`tests/architecture/test_intent_edges.py`** changes as follows:
   - `SUBPROCESS_COMMANDS` gains exactly `("<expr>", "-m", "orchestrator.factory_chain_read")`,
     and the count goes from 3 to 4.
   - `ENVIRONMENT` gains exactly `FACTORY_EVENTS_READ_DSN` and `FACTORY_EVENTS_APPEND_DSN`.
     Both are spelled as literals in `package_sources` so the scan reads them.
   - `READERS` and `EVENTS_FILE_PARTS` are unchanged. The database read adds no file reader, and
     the file's location doesn't move. FE-5 removes it.
   - New rows hold the child module's whole surface:
     - no subprocess and no file reader;
     - one environment variable (the read DSN);
     - exactly two lazy imports (`psycopg`, `factory_events.store`);
     - an exact standard-library import list;
     - one `cur.execute(WALK)`, with `WALK` pinned to FE-1's reader `SELECT`;
     - `read_only = True` and `REPEATABLE_READ` as its only attribute assignments.

     A control shows each row reporting a widening.
4. **The bump proposer.** `standing._run` passes no `env=`, so `approve --by-policy`, `revise`
   and `transition` already inherit `FACTORY_EVENTS_APPEND_DSN` when it's set. All three need
   it, because amendment 24 item 4 puts a holder's lifecycle events in the chain. No code change
   was needed:
   - Two tests in `tests/bump_proposer/test_standing.py` pin the behavior. They run a stand-in
     interpreter through the real `lifecycle` subprocess: the DSN arrives when set, and it's
     absent when unset.
   - `scripts/run-bump-proposer.sh` gains a comment saying it passes the variable through and
     fetches no DSN.
   - The launcher doesn't fetch a DSN, because the lane account's Keychain item and the secret's
     UUID don't exist (FE-4). Nothing was invented, so no stop point was reached.
5. **`docs/operations/package-intake.md`** no longer lists `~/.factory/events.jsonl` as the only
   chain. It says which chain each DSN state reads, and that a hand run refuses once the chain is
   the database (amendment 25 item 1). `driving-a-unit.md` #132 names the chain without naming
   the file, so it's still true and is unchanged.
6. **Tests:**
   - `pyproject.toml` gains an `integration` marker and `addopts = "-m 'not integration'"`.
   - `scripts/check-integration.sh` mirrors FE-2's: `FACTORY_TEST_DSN` if set, else a throwaway
     `postgres:16-alpine` on a random `127.0.0.1` port with a random password, removed on exit.
     Unlike FE-2's, it runs FE-1's `factory_events` under this repo's interpreter, because
     that's how `package_sources` runs it.
   - The new files are added to `layers.toml` (`[intent]`).

## Where the one-snapshot read lives, and why

It's in this repository, in `orchestrator.factory_chain_read`, and it uses FE-1's
`store.walk_records`, the same entry point FE-2 used. Neither upstream repository needed a change:

- FE-2's `intent_packages.chain_db` can't serve the orchestrator. It matches hash and revision
  only and answers with an exit code. The orchestrator matches event id, package id, hash,
  revision, approver and commit, and it needs the event's `timestamp` (amendment 24 item 8).
- `factory_events verify` followed by a second read would be two snapshots.

The match stays in the parent's existing `_approval_event_to_verified`, applied to events from
the one snapshot, so each repository keeps one matcher. It's a subprocess rather than an
in-process import so that security-standards stays out of the orchestrator's import graph, as it
is today.

## The unset-read-DSN test's placement

Only the "read DSN set, any error raises" tests ship here: four parametrized unit-test groups and
two integration tests (unreachable database, refused role). A test that an *unset* read DSN raises
would change today's behavior, which amendment 7 forbids before FE-5. It lands with FE-5's removal
of the file path (amendment 24 item 10). Nothing in the ADR text says FE-3 must refuse when the
read DSN is unset, so this isn't a stop point. The append-only refusal is a different case: a
chain DSN *is* set there, and it doesn't change today's behavior, because no process has either
DSN.

## Tests and checks run

- **`make check`** in the worktree, committed tree, with
  `TEST_DATABASE_URL=…/orchestrator_test_hq_w_141` and
  `SECURITY_STANDARDS_DIR=~/Projects/security-standards`: rc 0. Ruff check, ruff format and
  pyright were clean, and pytest reported **14,883 passed, 1 skipped**. The 7 integration tests
  are deselected, as intended.
- **Database tests**: `SECURITY_STANDARDS_DIR=<FE-1 checkout> scripts/check-integration.sh`
  reported **7 passed** (14,884 deselected) against a throwaway `postgres:16-alpine` container,
  run twice, the second time on the committed tree. No container was left behind.
  - The FE-1 checkout was a `git archive` of security-standards `origin/main` (`e89f69b`) into
    `/tmp`, because the local checkout (`1baec77`) doesn't contain FE-1.
  - The seven tests: match; no match; broken chain refuses with the match present; unreachable
    database refuses without the password anywhere in the message, stdout or stderr; refused
    role, likewise; read DSN set with the file holding the match still refuses; neither DSN set
    verifies from the file as before, and a corrupted file refuses.
- **Unit tests in the default run**: `tests/cli/test_package_chain_database.py` (22 tests)
  covers each refusal path:
  - the child can't run (`OSError`, timeout), with the cause chained;
  - empty or non-JSON output, with the cause chained;
  - broken, failure, an unnamed error type, and exit code and outcome disagreeing;
  - the events field isn't a list, or the record isn't a mapping;
  - the security-standards checkout is missing;
  - the append DSN alone refuses;
  - an empty read DSN counts as unset.

  It also covers the child's own behavior:
  - it walks in one read-only repeatable-read snapshot;
  - a broken walk returns no events;
  - a driver error is reported by type only, and the DSN never reaches stdout;
  - a missing read DSN is reported as unavailable.

  One test runs the **real** child and the real psycopg driver against a closed port. It asserts
  an `OperationalError` refusal, nothing on the parent's stdout, and no password in stderr.
  The bump-proposer tests add 2 more.
- **Mutation controls** (unit and edges suites): every mutant was caught.
  - Ignoring walk errors: 1 test failed.
  - Dropping `read_only`: 3 tests failed.
  - Falling back to the file under the read DSN: 14 tests failed.
- **`/code-review`** (medium) returned 8 findings. None needed a code change:
  - "`event` is `jsonb`" is wrong for the chain table: FE-1's `chain_schema.sql` line 22 has
    `event text`. The review read the `ship.py` projection's schema, and the integration tests
    pass against the real schema.
  - "`pg_isready` can pass during the image's initialization" doesn't apply here: the
    initialization server listens on a unix socket only, and the check uses `-h 127.0.0.1`.
  - The rest are listed under concerns.

## Files changed

- `src/orchestrator/factory_chain_read.py` (new)
- `src/orchestrator/package_sources.py`
- `tests/architecture/test_intent_edges.py`
- `tests/cli/test_package_chain_database.py` (new)
- `tests/cli/test_package_chain_database_integration.py` (new)
- `tests/cli/test_package_intake_cli.py`: one helper passes the events list
- `tests/bump_proposer/test_standing.py`
- `scripts/check-integration.sh` (new)
- `scripts/run-bump-proposer.sh`: comment only
- `docs/operations/package-intake.md`
- `pyproject.toml`: marker and `addopts`
- `layers.toml`

## Self-review

- **Stdout contract.** The carrier runs `json.loads(completed.stdout)` on `emit-intake-payload`,
  so the child's record is captured and never relayed. A test asserts the parent's stdout stays
  empty. That's why this module doesn't log to the CLI's stdout as FE-2's child does.
- **No file fallback.** No code path under the read DSN calls `_verify_factory_chain` or
  `_iter_package_approval_events`. Tests forbid both, and one mutant proves it.
- **The DSN** travels only through the child's environment. It isn't in argv, a message, a log
  record or a chained cause. A chained cause is an `OSError`, a `TimeoutExpired` (whose text is
  the argv) or a JSON parse error, never a driver error.

## Concerns

1. **Logging versus the stdout contract.** Amendment 24 item 13 has verifiers log JSON to stdout.
   On this path the CLI's stdout is the payload the carrier parses (Code Brain rule 3: "don't
   change what any lane's stdout contract parses"). The child's one stdout record is its
   structured log record, and the parent consumes it. The refusal's fixed message reaches stderr
   and the carrier's `package_not_intakeable` detail. No JSON log line from this read reaches a
   log sink of its own. If HQ wants one, it needs a decision on where it goes.
2. **The 30-second timeout** is shared with today's file verify, and FE-2 uses the same value.
   The walk's cost was FE-1's to measure (amendment 24, "Open before building"). A chain that
   outgrows it refuses every intake, closed.
3. **The whole set of `package.approved` events** crosses the pipe on each read: 103 on the Mac
   on 2026-11-13. That's small, but it grows with the chain. Filtering by package id in the child
   would add a second place that matches.
4. **The append-only refusal** is a choice beyond the handoff's literal wording ("when it isn't
   set, the file path behaves exactly as today"). With only the append DSN set, today's code
   would verify the database through `factory_events verify` but match against the file. It
   would already refuse through intent-packages' `verify-approval`, which FE-2 made refuse in
   that state. This makes the refusal explicit and keeps the two repositories consistent. No
   process holds either DSN today.
5. **The bump-proposer launcher** fetches no DSN. That waits for FE-4's Keychain item and the
   secret's UUID.
6. **Out of scope:** the Infra Brain and Code Brain rules were taken from the handoff, and no
   concern fell outside them.

## Standards followed

- **Code Brain 3 (logging).** The child writes one structured JSON record to stdout. No file
  logging was added, and no lane's stdout contract changed: `emit-intake-payload`'s stdout is
  still only the payload, which a test asserts.
- **Code Brain 4 (error handling).** Every database-path failure becomes `PackageSourceError`.
  Where the parent holds an exception (`OSError`, `TimeoutExpired`, or an `IndexError` or
  `JSONDecodeError` from parsing), it's chained with `from exc`. The child's one broad `except`
  is commented and reports the error's type, so it isn't silently swallowed.
- **Code Brain 5 (security).** No DSN appears in argv, a log record, an exception message or a
  chained cause. The child reports only error types, and the parent doesn't relay the child's
  stderr. Unit and integration tests assert that the password never appears in messages,
  stdout or stderr.
- **Infra Brain Database 6.** No schema is created outside test fixtures. The integration
  fixture runs FE-1's `db_setup` on a throwaway database in a throwaway container.
- **Infra Brain Security 591, 592, 593.** No BWS token or inline `BWS_ACCESS_TOKEN` was added.
  DSNs come from the environment only. The tests use a random throwaway password for a
  disposable cluster.
- **Infra Brain Security 1032.** No DSN was printed: the integration DSN wrapper's `repr` is
  redacted, and the script never echoes the DSN. No credential was exposed, and nothing needs
  rotating.
- **Orchestrator `CLAUDE.md`:**
  - A per-worktree test database was used (`orchestrator_test_hq_w_141`).
  - The new files are placed in `layers.toml`.
  - The architecture guard was extended exactly, not loosened.
  - No HTTP client was imported.
  - The interpreter is `.python-version`'s (3.14).
- **`STANDARDS.md`:** no suppression comments were added, and there are no comments that only
  restate the code.
