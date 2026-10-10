import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from orchestrator.api.dependencies import APIAuthenticationError, AuthConfig
from orchestrator.api.health import router as health_router
from orchestrator.api.routes import (
    execution,
    intake,
    landing,
    lifecycle,
    reconciliation,
    release,
    reporting,
    verifier,
)
from orchestrator.config import Settings
from orchestrator.errors import DomainError
from orchestrator.identity.auth import TOKEN_HASH_PATTERN, M2MCredential
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.web import router as web_router

# One router per domain, included in this order. FastAPI's include_router copies routes rather
# than nesting them, so an aggregate router would hide every route from `router.routes`; each
# domain's router is included here directly. No two routes overlap on a method, so the order
# decides nothing today -- keep it so if a route is added.
API_ROUTERS = (
    intake.router,
    reporting.router,
    landing.router,
    execution.router,
    release.router,
    lifecycle.router,
    reconciliation.router,
    verifier.router,
)


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
        # Nothing about the request is wrong: this process cannot answer it right now, either
        # because of its own configuration or because something it must read is unreachable.
        # A 409 would tell the caller to change the request, which is the wrong instruction.
        if error.code in {
            "csrf_unavailable",
            "factory_policy_invalid",
            "factory_policy_version_unsupported",
            "named_check_observation_unavailable",
        }:
            status = 503
        # ADR-0027 split intake's role refusal out of `human_actor_required`, and a refusal that
        # left this set would have regressed 403 -> 409 -- telling a worker credential to change
        # its request rather than that it may not make one. `intake_change_record_required` is
        # deliberately NOT here: that actor MAY register, and what is wrong is the request.
        if error.code in {
            "role_forbidden",
            "human_actor_required",
            "csrf_rejected",
            "intake_registrar_invalid",
        }:
            status = 403
        detail = {"code": error.code, "message": error.message}
        if error.recovery is not None:
            detail["recovery"] = error.recovery
        for name in ("current_state", "current_version"):
            if getattr(error, name) is not None:
                detail[name] = getattr(error, name)
        return JSONResponse(status_code=status, content={"error": detail})

    for api_router in API_ROUTERS:
        application.include_router(api_router)
    application.include_router(health_router)
    application.include_router(web_router)
    return application


def load_auth_config(settings: Settings | None = None) -> AuthConfig | None:
    """Build the runtime authentication configuration, or refuse to boot.

    Every refusal is the same `RuntimeError`, so nothing about which value was wrong -- or
    what it held -- reaches a log. A fresh `Settings` is read rather than the cached one, so the
    environment at the moment of the call is the one judged. Reading `Settings` validates every
    setting, not only the auth ones, so a malformed unrelated value refuses here too -- as the
    same `RuntimeError` (pydantic's `ValidationError` is a `ValueError`) rather than escaping
    with the offending input in its message.
    """
    try:
        settings = settings if settings is not None else Settings.model_validate({})
    except ValueError as error:
        raise RuntimeError("invalid runtime authentication configuration") from error
    bundle_path = settings.registry_bundle
    if not bundle_path:
        return None
    try:
        registry = RegistryAdapter.from_path(Path(bundle_path))
        credentials = _m2m_credentials(registry, settings.m2m_credentials)
        trusted_proxy_ips = frozenset(_json_list(settings.trusted_proxy_ips))
        email_to_actor = _email_actor_mapping(registry, settings.email_to_actor)
        roles = {
            str(key): ActorRole(value)
            for key, value in _json_object(settings.m2m_roles, required=False).items()
        }
        if not set(roles) <= set(credentials) or any(
            role is ActorRole.HUMAN for role in roles.values()
        ):
            raise RuntimeError("invalid runtime authentication configuration")
        _require_rotation_executor(registry, credentials, settings.rotation_executor_agent_id)
        marker = _required_secret(settings.proxy_marker)
        csrf_secret = _required_secret(settings.csrf_secret).encode()
        return AuthConfig(
            registry=registry,
            m2m_credentials=credentials,
            trusted_proxy_ips=trusted_proxy_ips,
            proxy_marker_header=settings.proxy_marker_header,
            proxy_marker=marker,
            email_header=settings.email_header,
            email_to_actor=email_to_actor,
            m2m_roles=roles,
            credential_key_header=settings.credential_key_header,
            csrf_secret=csrf_secret,
        )
    except (TypeError, ValueError, KeyError) as error:
        raise RuntimeError("invalid runtime authentication configuration") from error


def _required(value: str | None) -> str:
    if not value:
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _required_secret(value: SecretStr | None) -> str:
    return _required(value.get_secret_value() if value is not None else None)


def _json_object(raw: str | None, *, required: bool = True) -> dict[str, object]:
    if raw is None and not required:
        return {}
    try:
        value = json.loads(raw or "")
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid runtime authentication configuration") from error
    if not isinstance(value, dict):
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(_required(raw))
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid runtime authentication configuration") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _m2m_credentials(registry: RegistryAdapter, document: str | None) -> dict[str, M2MCredential]:
    credentials: dict[str, M2MCredential] = {}
    for key, raw in _json_object(document).items():
        if (
            not key
            or not isinstance(raw, dict)
            or set(raw) != {"agent_id", "token_hash"}
            or not isinstance(raw.get("agent_id"), str)
            or not isinstance(raw.get("token_hash"), str)
            or TOKEN_HASH_PATTERN.fullmatch(raw["token_hash"]) is None
        ):
            raise RuntimeError("invalid runtime authentication configuration")
        actor = registry.resolve(raw["agent_id"])
        if actor.runtime == "human" or actor.authority_profile == "human-operator-v1":
            raise RuntimeError("invalid runtime authentication configuration")
        credentials[str(key)] = M2MCredential(
            agent_id=raw["agent_id"],
            token_hash=raw["token_hash"],
        )
    if not credentials:
        raise RuntimeError("invalid runtime authentication configuration")
    return credentials


# The authority profile security-standards gives the credential-rotation executor (ADR-0055).
ROTATION_EXECUTOR_PROFILE = "rotation-executor-v1"


def _require_rotation_executor(
    registry: RegistryAdapter, credentials: dict[str, M2MCredential], configured: str | None
) -> None:
    """Refuse to boot unless the executor setting and the credentials agree (ADR-0055 decision 1).

    A configured executor must resolve to a machine identity holding the executor's profile. And a
    credential whose identity holds that profile must be the configured executor: otherwise the
    executor would authenticate while claim confinement, keyed on the setting, did not know it,
    and it could claim any unit that is not a rotation unit.
    """
    if configured is not None:
        actor = registry.resolve(configured)
        if actor.authority_profile != ROTATION_EXECUTOR_PROFILE:
            raise RuntimeError("invalid runtime authentication configuration")
    for credential in credentials.values():
        profile = registry.resolve(credential.agent_id).authority_profile
        if profile == ROTATION_EXECUTOR_PROFILE and credential.agent_id != configured:
            raise RuntimeError("invalid runtime authentication configuration")


def _email_actor_mapping(registry: RegistryAdapter, raw: str | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for email, actor_id in _json_object(raw).items():
        if (
            not isinstance(email, str)
            or email != email.strip().lower()
            or not email
            or not isinstance(actor_id, str)
        ):
            raise RuntimeError("invalid runtime authentication configuration")
        actor = registry.resolve(actor_id)
        if actor.runtime != "human" or actor.authority_profile != "human-operator-v1":
            raise RuntimeError("invalid runtime authentication configuration")
        mapping[email] = actor_id
    return mapping


app = create_app(load_auth_config())
