from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api.health import router as health_router
from orchestrator.api.routes import router as api_router
from orchestrator.errors import DomainError


def create_app() -> FastAPI:
    application = FastAPI(title="Orchestrator")

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, error: DomainError) -> JSONResponse:
        status = 404 if error.code.endswith("_not_found") else 409
        if error.code in {"role_forbidden", "human_actor_required"}:
            status = 403
        detail = {"code": error.code, "message": error.message}
        if error.recovery is not None:
            detail["recovery"] = error.recovery
        for name in ("current_state", "current_version"):
            if getattr(error, name) is not None:
                detail[name] = getattr(error, name)
        return JSONResponse(status_code=status, content={"error": detail})

    application.include_router(api_router)
    application.include_router(health_router)
    return application


app = create_app()
