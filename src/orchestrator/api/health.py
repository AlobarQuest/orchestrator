from typing import Annotated

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_session

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=None)
def ready(session: SessionDep) -> dict[str, str] | JSONResponse:
    try:
        session.execute(text("SELECT 1"))
        current = MigrationContext.configure(session.connection()).get_current_revision()
        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    except (SQLAlchemyError, OSError):
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "reason": "database"}
        )
    if len(heads) != 1 or current != heads[0]:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "migration_drift"},
        )
    return {"status": "ok"}
