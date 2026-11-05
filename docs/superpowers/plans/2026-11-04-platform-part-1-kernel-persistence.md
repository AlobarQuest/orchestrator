# Platform layer Part 1: kernel and persistence

Spec: `docs/superpowers/specs/2026-11-04-platform-layer-design.md`, "Part 1". Branch
`platform-layer-pass` in `.worktrees/platform`, its own `.venv` (Python 3.14.3) and database
`orchestrator_test_platform`. Tests need `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`,
`SECURITY_STANDARDS_DIR` (`$PWD/tests/fixtures/security-standards`) and `PGTZ=UTC`.

Tasks run one at a time in this worktree, so two pytest suites never share the database. Each task
is test-first where it adds a test, shows the new test failing on a deliberately broken input
before it passes, ends with `make check` green, and is committed on its own. Each gets an
implementer and a reviewer (Sonnet); the final whole-branch review runs on Opus. No deploy: the
database schema doesn't change.

New test files go in `layers.toml`'s `platform` layer (`test_layer_manifest.py`).

## Task 1: the `IN (...)` CHECK shape

Files: `src/orchestrator/persistence/models.py`, `tests/persistence/test_check_constraint_text.py`
(new), `tests/fixtures/model_in_checks.json` (new), `tests/architecture/test_in_check_shape.py`
(new).

1. Generate the fixture from today's model: `{constraint name: str(constraint.sqltext)}` for every
   `CheckConstraint` in `Base.metadata` whose text contains ` IN (`. Hand-correct one entry,
   `ck_work_package_revisions_verification_mode`, to drop the trailing comma (migration 0003's
   text). A test asserts the model renders exactly the fixture. It fails before step 2.
2. Rewrite the 20 `IN {X!r}` constraints to join quoted members explicitly, with one small helper
   in `models.py` (for example `_in_list(VOCAB)` returning `"('a', 'b')"`), called inside each
   `CheckConstraint(...)` so the vocabulary name stays inside the call
   (`test_cross_boundary_vocabulary` keys on that). The fixture test passes.
3. A database test: for each model `IN` CHECK, create a temporary table `LIKE` its real table
   (no constraints), add the model's CHECK to it, and assert `pg_get_constraintdef` equals the
   migrated constraint's `pg_get_constraintdef` on the real table. This holds model and migration
   equal in Postgres's own normal form. It would have failed on the drifted constraint.
4. An AST guard: in `models.py` and in any `migrations/versions/` file whose numeric prefix is above
   `0047`, refuse an f-string with a `!r` conversion, a `repr(` call or a `%r` whose literal text
   contains ` IN `. Show it fails on a seeded `f"x IN {V!r}"` in a temporary copy (a control test
   in the file, not an edit to the model).

## Task 2: metadata guards (run ids, nullable UNIQUE, revision ids)

Files: `tests/persistence/test_schema_rules.py` (new).

1. Every column in `Base.metadata` whose name ends `run_id` is `BigInteger` or `String`, never
   `Integer` or `SmallInteger` (spec call 9). A control builds a throwaway `Table` with an
   `Integer` `run_id` and asserts the predicate refuses it.
2. For every unique constraint and unique index in `Base.metadata`, resolve its columns, including
   the columns an expression index names, and require `postgresql_nulls_not_distinct=True` where
   any is nullable, or an entry in a `NULLABLE_UNIQUE_REASONS` dict (empty today). A control
   builds a nullable unique constraint without the flag.
3. Every revision id under `migrations/versions/` is at most 32 characters, read through Alembic's
   `ScriptDirectory`. A control feeds a 33-character id to the predicate.

## Task 3: the `updated_at` trigger and `TransactionClock`

Files: `tests/persistence/test_constraints.py` (extend), `tests/test_clock.py` (new, or next to
the existing clock tests if any).

1. Back-date a `work_units` row's `updated_at` through an `UPDATE` that sets it to a fixed past
   value, commit, re-read through a different session, and assert the stored value is not the
   past value (the trigger rewrote it).
2. `TransactionClock.now()` returns the same value twice inside one transaction across a
   `pg_sleep(0.01)`, and a later transaction reads a later value.

## Task 4: transaction ownership, time-boxed

Files: `tests/architecture/test_transaction_ownership.py` (new) only if a predicate discriminates.

Spec call 7. Using the call graph `test_unreachable_guards.py` builds, try a predicate of the form
"a function whose body calls `session.commit()` is never called by another function whose body
calls `session.commit()`, or by a function reached from such a function before its commit". It
must fail on a seeded nested commit (a control) and pass on today's code with no allowlist entry
that reads "in fact it is fine". One implementer attempt. If it can't, write no test, and record
in this plan and in Task 5's CLAUDE.md text that the rule stays document-only and why (the
measured counter-examples).

## Task 5: records

Files: `src/orchestrator/kernel/states.py`, `kernel/leases.py`, `kernel/secret_metadata.py`,
`config.py` (comments and docstrings only), `docs/operations/local-development.md`, `CLAUDE.md`,
`docs/history/claude-md-invariants-archive.md`.

1. Docstrings: OBSERVER is named in three places (`observations.py::_authorize_actor`,
   `api/routes/reporting.py`, `api/dependencies.py`); the hung-worker hole in `leases.py` is
   closed (name what closed it, read from git); three ingresses in `secret_metadata.py`; four JSON
   settings in `config.py`.
2. `local-development.md`: the test-database entry (one schema migrated per process, emptied by
   `TRUNCATE` per test, `-n auto` workers keyed per worker, 47 migrations), the Python floor
   (`>=3.14`, held equal), and the lane count (20 launch agents).
3. CLAUDE.md: rewrite as they stand, each naming its pinning test, the rules for `IN` CHECKs
   (the fixture and the database comparison), run ids (never a 32-bit integer), nullable UNIQUE,
   revision ids, the trigger, `TransactionClock`, transaction ownership (Task 4's outcome) and the
   shared test database. Old text goes to the archive under the next free numbers. The section
   stays below `<!-- code-standards:end -->`.

## Closing

1. Final whole-branch review on Opus against the spec's Part 1; one fix wave; scoped re-review.
2. Mutation pass over Part 1's rules: break each rule in the code it governs (a `!r` back in, a
   trailing comma, an `Integer` run id, a nullable unique without the flag, a 33-character id, the
   trigger dropped in a scratch migration, `TransactionClock` on `clock_timestamp()`), clearing
   `__pycache__` around each mutate and restore, and record which tests fail. Kill or fix every
   survivor.
3. Push, open the pull request, watch CI with `--watch --interval 30`, merge when green, confirm
   `MERGED` and read `main`.
