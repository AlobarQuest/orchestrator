from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock


def test_the_clock_does_not_advance_inside_a_transaction(migrated_engine: Engine) -> None:
    clock = TransactionClock()
    with Session(migrated_engine) as session:
        first = clock.now(session)
        session.execute(text("SELECT pg_sleep(0.01)"))
        second = clock.now(session)
        session.rollback()

    assert first == second


def test_a_later_transaction_reads_a_later_time(migrated_engine: Engine) -> None:
    clock = TransactionClock()
    with Session(migrated_engine) as session:
        first = clock.now(session)
        session.rollback()
    with Session(migrated_engine) as session:
        later = clock.now(session)
        session.rollback()

    assert later > first
