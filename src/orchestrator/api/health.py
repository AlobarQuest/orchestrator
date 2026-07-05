from pathlib import Path
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
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=None)
def ready(session: SessionDep) -> dict[str, str] | JSONResponse:
    try:
        session.execute(text("SELECT 1"))
        database_heads = MigrationContext.configure(session.connection()).get_current_heads()
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "reason": "database"}
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "configuration"},
        )
    try:
        heads = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH))).get_heads()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "configuration"},
        )
    if len(database_heads) != 1 or len(heads) != 1 or database_heads[0] != heads[0]:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "migration_drift"},
        )
    return {"status": "ok"}
