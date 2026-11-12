from typing import Any

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.config import get_settings

# psycopg returns a timestamptz in the session's TimeZone. In a zone that observes DST, two
# loaded values share one ZoneInfo and Python subtracts and compares those by wall clock, so
# arithmetic across a DST change is an hour off. A UTC session returns fixed-offset values.
_UTC_SESSION = "-c TimeZone=UTC"


def make_engine(url: str | URL, **kwargs: Any) -> Engine:
    """An engine whose every session runs in UTC; the only place an engine is built."""
    connect_args = dict(kwargs.pop("connect_args", {}))
    options = connect_args.get("options")
    connect_args["options"] = f"{options} {_UTC_SESSION}" if options else _UTC_SESSION
    return create_engine(url, connect_args=connect_args, **kwargs)


engine = make_engine(get_settings().database_url, pool_pre_ping=True)
session_factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
