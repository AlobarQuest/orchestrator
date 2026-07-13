from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import get_session
from orchestrator.config import ProductionDrillMode
from orchestrator.services.production_drill_compatibility import (
    DRILL_REVISION,
    PRE_DRILL_REVISION,
)

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/health", tags=["health"])
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


def expected_database_head(mode: ProductionDrillMode) -> str:
    if mode is ProductionDrillMode.OFF:
        return PRE_DRILL_REVISION
    return DRILL_REVISION


@router.get("/ready", response_model=None)
def ready(request: Request, session: SessionDep) -> dict[str, str] | JSONResponse:
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
        mode = request.app.state.production_drill_mode
        if not isinstance(mode, ProductionDrillMode):
            raise TypeError("invalid production drill mode")
        heads = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH))).get_heads()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "configuration"},
        )
    if (
        len(database_heads) != 1
        or len(heads) != 1
        or heads[0] != DRILL_REVISION
        or database_heads[0] != expected_database_head(mode)
    ):
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "migration_drift"},
        )
    return {"status": "ok"}
