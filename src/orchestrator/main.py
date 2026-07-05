from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api.dependencies import APIAuthenticationError, AuthConfig
from orchestrator.api.health import router as health_router
from orchestrator.api.routes import router as api_router
from orchestrator.errors import DomainError


def create_app(auth_config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(title="Orchestrator")
    application.state.auth_config = auth_config

    @application.exception_handler(APIAuthenticationError)
    async def authentication_error_handler(
        _request: Request, error: APIAuthenticationError
    ) -> JSONResponse:
        message = (
            "valid authentication credentials are required"
            if error.code == "authentication_required"
            else "authentication credentials were rejected"
        )
        return JSONResponse(
            status_code=401,
            content={"error": {"code": error.code, "message": message}},
            headers={"WWW-Authenticate": "Bearer"},
        )

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
