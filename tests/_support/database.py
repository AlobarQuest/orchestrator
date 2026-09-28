"""The test database: which one this process uses, how it is built, and how it is reset.

The schema is migrated ONCE per test process and every test that asks for it gets a clean one
by TRUNCATE afterwards. Rebuilding per test (``DROP SCHEMA`` + ``alembic upgrade head`` over
every migration) was ~75% of the suite's runtime. A test that exercises the migrations
themselves opts back into a full rebuild with ``@pytest.mark.schema_rebuild``.

Under pytest-xdist each worker gets its own database, named after ``TEST_DATABASE_URL``'s with
the worker id appended (``orchestrator_test`` -> ``orchestrator_test_gw0``), created at worker
start and dropped at worker end. The base database is never touched by a worker, so two
sessions on different base databases never meet.
"""

from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

BASE_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/orchestrator_test",
)
WORKER = os.environ.get("PYTEST_XDIST_WORKER")


def _worker_url(base: str, worker: str | None) -> str:
    if worker is None:
        return base
    url = make_url(base)
    return url.set(database=f"{url.database}_{worker}").render_as_string(hide_password=False)


TEST_DATABASE_URL = _worker_url(BASE_TEST_DATABASE_URL, WORKER)

# `migrations/env.py` migrates whatever ORCHESTRATOR_DATABASE_URL names, whatever alembic's own
# URL says. So this is an override, not a default: under xdist every worker would otherwise
# migrate the shared base database, and serially a shell that exports a dogfooding database
# would have its schema upgraded by a test run.
os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ORCHESTRATOR_DATABASE_URL"] = TEST_DATABASE_URL

# A reset blocked by a lock another connection still holds is a test that leaked an open
# transaction. Fail it, naming the cause, rather than hang the run.
RESET_LOCK_TIMEOUT = "5s"


def alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def create_worker_database() -> None:
    """Create this worker's database afresh. A no-op outside xdist: the base must exist."""
    if WORKER is None:
        return
    name = make_url(TEST_DATABASE_URL).database
    assert name and name != make_url(BASE_TEST_DATABASE_URL).database
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def drop_worker_database() -> None:
    if WORKER is None:
        return
    name = make_url(TEST_DATABASE_URL).database
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _admin_engine() -> Engine:
    url = make_url(TEST_DATABASE_URL).set(database="postgres")
    return create_engine(url, isolation_level="AUTOCOMMIT")


def rebuild_schema(engine: Engine) -> tuple[str, ...]:
    """Drop and re-migrate the schema; return the tables a reset must empty."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        tables = data_tables(connection)
        assert_unseeded(connection, tables)
    return tables


def assert_unseeded(connection: Connection, tables: tuple[str, ...]) -> None:
    # A reset empties every table. A migration that seeds rows would have them destroyed after
    # the first test, and every later test would run against a database no deployment has.
    seeded = [table for table in tables if _has_rows(connection, table)]
    assert not seeded, f"migrations seed rows the per-test reset would destroy: {seeded}"


def data_tables(connection: Connection) -> tuple[str, ...]:
    return tuple(
        connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version' ORDER BY tablename"
            )
        )
    )


def _has_rows(connection: Connection, table: str) -> bool:
    return connection.scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}")')) is True


def reset_data(engine: Engine, tables: tuple[str, ...]) -> None:
    """Empty every data table, failing (not hanging) if a leaked transaction holds a lock.

    TRUNCATE rather than DELETE: it needs no superuser (DELETE would have to switch off the
    append-only triggers with ``session_replication_role``), and its ACCESS EXCLUSIVE lock is
    what turns a leaked idle-in-transaction session into a loud ``lock_timeout`` rather than a
    silent pass. No table has an identity column, so RESTART IDENTITY is belt and braces.
    """
    if not tables:
        return
    names = ", ".join(f'"{table}"' for table in tables)
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT}'"))
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
