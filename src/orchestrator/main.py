import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api.dependencies import APIAuthenticationError, AuthConfig
from orchestrator.api.health import router as health_router
from orchestrator.api.routes import router as api_router
from orchestrator.errors import DomainError
from orchestrator.identity.auth import TOKEN_HASH_PATTERN, M2MCredential
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.web import router as web_router


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
        if error.code == "csrf_unavailable":
            status = 503
        if error.code in {"role_forbidden", "human_actor_required", "csrf_rejected"}:
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
    application.include_router(web_router)
    return application


def load_auth_config() -> AuthConfig | None:
    bundle_path = os.environ.get("ORCHESTRATOR_REGISTRY_BUNDLE")
    if not bundle_path:
        return None
    try:
        registry = RegistryAdapter.from_path(Path(bundle_path))
        credentials = _m2m_credentials(registry)
        trusted_proxy_ips = frozenset(_json_list("ORCHESTRATOR_TRUSTED_PROXY_IPS"))
        email_to_actor = _email_actor_mapping(registry)
        roles = {
            str(key): ActorRole(value)
            for key, value in _json_object("ORCHESTRATOR_M2M_ROLES", required=False).items()
        }
        if not set(roles) <= set(credentials) or any(
            role is ActorRole.HUMAN for role in roles.values()
        ):
            raise RuntimeError("invalid runtime authentication configuration")
        production_drill_credential_key_id = _required_environment(
            "ORCHESTRATOR_PRODUCTION_DRILL_CREDENTIAL_KEY_ID"
        )
        if roles.get(production_drill_credential_key_id) is not ActorRole.SYSTEM:
            raise RuntimeError("invalid runtime authentication configuration")
        marker = _required_environment("ORCHESTRATOR_PROXY_MARKER")
        csrf_secret = _required_environment("ORCHESTRATOR_CSRF_SECRET").encode()
        return AuthConfig(
            registry=registry,
            m2m_credentials=credentials,
            trusted_proxy_ips=trusted_proxy_ips,
            proxy_marker_header=os.environ.get(
                "ORCHESTRATOR_PROXY_MARKER_HEADER", "X-Alobar-Proxy"
            ),
            proxy_marker=marker,
            email_header=os.environ.get("ORCHESTRATOR_EMAIL_HEADER", "X-Alobar-Email"),
            email_to_actor=email_to_actor,
            m2m_roles=roles,
            production_drill_credential_key_id=production_drill_credential_key_id,
            credential_key_header=os.environ.get(
                "ORCHESTRATOR_CREDENTIAL_KEY_HEADER", "X-Credential-Key-Id"
            ),
            csrf_secret=csrf_secret,
        )
    except (TypeError, ValueError, KeyError) as error:
        raise RuntimeError("invalid runtime authentication configuration") from error


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _json_object(name: str, *, required: bool = True) -> dict[str, object]:
    raw = os.environ.get(name)
    if raw is None and not required:
        return {}
    try:
        value = json.loads(raw or "")
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid runtime authentication configuration") from error
    if not isinstance(value, dict):
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _json_list(name: str) -> list[str]:
    raw = _required_environment(name)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid runtime authentication configuration") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError("invalid runtime authentication configuration")
    return value


def _m2m_credentials(registry: RegistryAdapter) -> dict[str, M2MCredential]:
    credentials: dict[str, M2MCredential] = {}
    for key, raw in _json_object("ORCHESTRATOR_M2M_CREDENTIALS").items():
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


def _email_actor_mapping(registry: RegistryAdapter) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for email, actor_id in _json_object("ORCHESTRATOR_EMAIL_TO_ACTOR").items():
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
