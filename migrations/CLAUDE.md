# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Alembic migrations (`migrations/`)

- Alembic revision ids must be at most 32 characters (`alembic_version.version_num` is
  `varchar(32)`;
  `test_revision_ids_fit_alembic_version_num`). History: `docs/history/claude-md-invariants-archive.md` #57.
