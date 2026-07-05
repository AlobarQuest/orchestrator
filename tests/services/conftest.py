from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def migrated_session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as session:
        yield session
        session.rollback()
