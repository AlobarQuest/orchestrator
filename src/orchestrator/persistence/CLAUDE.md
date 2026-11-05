# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Persistence models (`src/orchestrator/persistence/`): CHECK constraints, column types and unique constraints

- Never build a SQL `IN (...)` CHECK with `f"{VOCAB!r}"`: a one-element tuple renders a trailing
  comma. Build it with `models._in_members`; the model's rendered text is pinned by
  `tests/fixtures/model_in_checks.json` and compared with the migrated database
  (`test_postgres_stores_the_model_check_as_it_stores_the_migrated_one`), and
  `test_no_in_list_is_built_with_repr` refuses the shape.
  History: `docs/history/claude-md-invariants-archive.md` #54.
- A column holding a GitHub Actions run id is never a 32-bit integer: use `BigInteger` or `String`.
  Ids exceed 2^31, and `Integer` fails only on Postgres (`integer out of range`), never on SQLite
  (`test_run_id_columns_are_not_narrow_integers`).
  History: `docs/history/claude-md-invariants-archive.md` #157.
- A UNIQUE constraint or unique index over a nullable column is disabled for NULL rows: set
  `postgresql_nulls_not_distinct=True` or record a reason in `NULLABLE_UNIQUE_REASONS`
  (`test_nullable_unique_constraints_say_how_they_treat_null`), and test by writing around the
  service.
  History: `docs/history/claude-md-invariants-archive.md` #229.
