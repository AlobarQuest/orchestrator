import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/orchestrator_test",
)
os.environ.setdefault("ORCHESTRATOR_DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()
