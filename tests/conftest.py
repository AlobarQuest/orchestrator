from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from tests._support.database import (
    TEST_DATABASE_URL,
    create_worker_database,
    drop_worker_database,
    rebuild_schema,
    reset_data,
)

__all__ = ["TEST_DATABASE_URL"]


@dataclass
class _Schema:
    """This process's migrated schema: the tables a reset empties, and whether it needs rebuilding.

    `dirty` is set after any `schema_rebuild` test, which may leave the schema downgraded,
    dropped, or at a different head; the next ordinary test rebuilds before it runs.
    """

    tables: tuple[str, ...]
    dirty: bool = False


@pytest.fixture(scope="session")
def _test_database() -> Iterator[None]:
    create_worker_database()
    yield
    drop_worker_database()


@pytest.fixture(scope="session")
def _schema(_test_database: None) -> _Schema:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        return _Schema(tables=rebuild_schema(engine))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _schema_rebuild_bookkeeping(request: pytest.FixtureRequest) -> Iterator[None]:
    # Applies to marked tests that request no database fixture too: some drop the schema
    # themselves, and the flag must be set however the test ends.
    if request.node.get_closest_marker("schema_rebuild") is None:
        yield
        return
    schema: _Schema = request.getfixturevalue("_schema")
    try:
        yield
    finally:
        schema.dirty = True


@pytest.fixture
def migrated_engine(request: pytest.FixtureRequest, _schema: _Schema) -> Iterator[Engine]:
    """An engine on a migrated, empty schema.

    Nothing wraps the test in an outer transaction: services commit, and a persistence
    assertion re-reads through a second session, so each test gets a real database and the
    data is removed afterwards. The reset runs after every fixture that depends on this one
    (`migrated_session`, `db_client`, ...) has closed its session.
    """
    rebuild = request.node.get_closest_marker("schema_rebuild") is not None
    engine = create_engine(TEST_DATABASE_URL)
    try:
        if rebuild or _schema.dirty:
            _schema.tables = rebuild_schema(engine)
            _schema.dirty = False
        yield engine
    finally:
        # Dispose first: it closes the pool's idle connections, so any connection still open
        # afterwards belongs to a session the test leaked, and the reset's lock_timeout names it.
        engine.dispose()
        try:
            if not rebuild:
                reset_data(engine, _schema.tables)
        finally:
            engine.dispose()


@pytest.fixture
def migrated_session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def db_session(_test_database: None) -> Iterator[Session]:
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()
