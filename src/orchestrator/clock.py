from datetime import datetime
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session


class Clock(Protocol):
    def now(self, session: Session) -> datetime: ...


class TransactionClock:
    def now(self, session: Session) -> datetime:
        return session.execute(select(text("transaction_timestamp()"))).scalar_one()
