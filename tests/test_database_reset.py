"""The per-test reset is what keeps a shared migrated schema honest; these prove it clears.

The first pair discriminates by ORDER: the first test commits a row and cleans nothing up, the
second fails if it survives. `xdist_group` keeps both on one worker, in file order, under
`--dist loadgroup`; without it the second could land on a different worker and pass for the
wrong reason. The direct tests call the reset themselves, so they hold under any distribution.
"""

import uuid

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event
from tests._support import database
from tests._support.database import assert_unseeded, data_tables, reset_data

SENTINEL_KEY = "database-reset-control:survivor"


def _event(idempotency_key: str) -> Event:
    return Event(
        actor_id="system",
        action="database.reset.control",
        subject_type="control",
        subject_id=uuid.uuid4(),
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )


@pytest.mark.xdist_group("database_reset_control")
def test_reset_control_leaves_a_committed_row_behind(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_event(SENTINEL_KEY))
        session.commit()


@pytest.mark.xdist_group("database_reset_control")
def test_reset_control_the_previous_tests_row_is_gone(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_reset_empties_every_data_table(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_event("database-reset-control:direct"))
        session.commit()

    with migrated_engine.connect() as connection:
        tables = data_tables(connection)
    reset_data(migrated_engine, tables)

    with migrated_engine.connect() as connection:
        assert_unseeded(connection, tables)


def test_the_unseeded_guard_fires_on_a_row(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_event("database-reset-control:seeded"))
        session.commit()

    with migrated_engine.connect() as connection:
        with pytest.raises(AssertionError, match="events"):
            assert_unseeded(connection, data_tables(connection))


def test_a_leaked_open_transaction_fails_the_reset_rather_than_hanging(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(database, "RESET_LOCK_TIMEOUT", "200ms")
    with migrated_engine.connect() as connection:
        tables = data_tables(connection)

    leaked = migrated_engine.connect()
    try:
        leaked.begin()
        leaked.execute(select(func.count()).select_from(Event))  # holds ACCESS SHARE on events
        with pytest.raises(OperationalError, match="lock timeout"):
            reset_data(migrated_engine, tables)
    finally:
        leaked.close()
