import os
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

# WHAT THIS BUILD IS, ANSWERABLE FROM OUTSIDE IN ONE CALL.
#
# The image already carries this value as an OCI label and the tag carries a short form of it, but
# reading either needs a shell on the host -- so nothing off the machine could ask what production
# was serving, and the one application in this estate whose swap is performed by hand was the only
# one that could not be asked. That gap has a measured cost: a merged change sat undeployed for two
# days while its lane logged a zero every morning that was read as the rule finding nothing to do.
#
# READ ONCE, at import. A process cannot change which build it is, and re-reading per request would
# say otherwise to anyone who looked.
#
# `None`, never a guess and never absent. An image built before this existed, or a process started
# outside one, has no honest answer -- and a consumer must tell "this build does not know" from
# "this build is current". The key is always present so nothing has to distinguish an absent key
# from an unstated value, the same discipline the landing ledger's reason field uses.
REVISION = os.environ.get("ORCHESTRATOR_REVISION") or None


@router.get("/live")
def live() -> dict[str, str | None]:
    return {"status": "ok", "revision": REVISION}


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
