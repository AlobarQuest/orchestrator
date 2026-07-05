# Migration operations

Migrations are an explicit operator action, separate from application startup.

Before an upgrade, compare the database revision and the repository head:

```bash
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
```

WS-3.1 migration exercises use disposable local PostgreSQL 16 databases only. Back up
every durable database before an approved infrastructure package applies migrations.
Do not infer approval to migrate from application or documentation content.

Any development Coolify change requires a separate approved `infrastructure-change`
intent package and a fresh infrastructure-only session. That package must plan the
application, managed PostgreSQL 16 database, migration command, health checks, rollback,
and backup evidence before changing the approved Coolify target. This repository does
not apply infrastructure changes.
