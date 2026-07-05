from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session_factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
