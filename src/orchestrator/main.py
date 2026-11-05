import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from orchestrator.api.dependencies import APIAuthenticationError, AuthConfig
from orchestrator.api.error_shape import authentication_message, error_body, status_for
from orchestrator.api.health import router as health_router
from orchestrator.api.routes import (
    execution,
    intake,
    landing,
    lifecycle,
    membership,
    reconciliation,
    release,
    reporting,
    rotation,
    verifier,
)
from orchestrator.api.server_errors import (
    NO_ALERTS,
    ServerAlertConfig,
    log_level,
    server_alert_config,
    server_error_handler,
)
from orchestrator.config import Settings, get_settings
from orchestrator.errors import DomainError
from orchestrator.identity.auth import (
    TOKEN_HASH_PATTERN,
    M2MCredential,
    PreviousHash,
    previous_hash_valid,
)
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.web import router as web_router

# One router per domain, included in this order. Routers are included per domain so each
# domain's router keeps its own `.routes`; inventories read `openapi()["paths"]` and the domain
# routers, never `app.routes` (under FastAPI 0.159.1 that holds `_IncludedRouter` objects, not
# routes). No two routes overlap on a method, so the order
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
    rotation.router,
    membership.router,
)


def create_app(
    auth_config: AuthConfig | None = None, alert_config: ServerAlertConfig | None = None
) -> FastAPI:
    application = FastAPI(title="Orchestrator")
    application.state.auth_config = auth_config
    # Exception handlers get no `Depends`, so the 500 handler reads its config from here; a test
    # replaces `application.state.alert_config`, never a settings override.
    application.state.alert_config = alert_config or NO_ALERTS

    @application.exception_handler(APIAuthenticationError)
    async def authentication_error_handler(
        _request: Request, error: APIAuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=error_body(error.code, authentication_message(error.code)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status_for(error.code),
            content=error_body(
                error.code,
                error.message,
                recovery=error.recovery,
                current_state=error.current_state,
                current_version=error.current_version,
            ),
        )

    # Everything no handler above answers: the nested shape, a log line and one keyed alert.
    application.add_exception_handler(Exception, server_error_handler)

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
        _require_rotation_executors(
            registry,
            credentials,
            {
                ROTATION_EXECUTOR_PROFILE: settings.rotation_executor_agent_id,
                ROTATION_KEYCHAIN_EXECUTOR_PROFILE: settings.rotation_keychain_executor_agent_id,
            },
        )
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
    now = datetime.now(UTC)
    for key, raw in _json_object(document).items():
        if (
            not key
            or not isinstance(raw, dict)
            or set(raw) - {"previous"} != {"agent_id", "token_hash"}
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
            previous=_previous_hash(raw),
        )
    if not credentials:
        raise RuntimeError("invalid runtime authentication configuration")
    current_hashes = {credential.token_hash for credential in credentials.values()}
    if not all(
        previous_hash_valid(credential, current_hashes, now) for credential in credentials.values()
    ):
        raise RuntimeError("invalid runtime authentication configuration")
    return credentials


# The authority profiles security-standards gives the credential-rotation executor's two
# instances: the VM (ADR-0055) and the Keychain instance on the operator machine (amendment 12).
ROTATION_EXECUTOR_PROFILE = "rotation-executor-v1"
ROTATION_KEYCHAIN_EXECUTOR_PROFILE = "rotation-executor-keychain-v1"


def _require_rotation_executors(
    registry: RegistryAdapter,
    credentials: dict[str, M2MCredential],
    configured: dict[str, str | None],
) -> None:
    """Refuse to boot unless the executor settings and the credentials agree (ADR-0055 decision 1).

    ``configured`` maps each executor profile to its setting. A configured executor must resolve
    to a machine identity holding its own profile, so one identity can't be both instances. And a
    credential whose identity holds an executor profile must be that profile's configured
    executor: otherwise the executor would authenticate while claim confinement, keyed on the
    settings, did not know it, and it could claim any unit that is not a rotation unit.
    """
    for profile, agent_id in configured.items():
        if agent_id is not None and registry.resolve(agent_id).authority_profile != profile:
            raise RuntimeError("invalid runtime authentication configuration")
    for credential in credentials.values():
        profile = registry.resolve(credential.agent_id).authority_profile
        if profile in configured and credential.agent_id != configured[profile]:
            raise RuntimeError("invalid runtime authentication configuration")


def _previous_hash(raw: dict[str, object]) -> PreviousHash | None:
    """An entry's optional `previous`: `{"token_hash": <sha256>, "until": <UTC instant>}`.

    ADR-0055 decision 7. A rotation keeps the old bearer valid until every consumer has switched,
    and `until` is the backstop. This reads the shape; `previous_hash_valid` holds the rules,
    including that boot refuses an `until` more than eight days ahead, so a mistyped expiry cannot
    keep a retired bearer valid indefinitely. One already past is accepted and authenticates
    nothing. An image older than this parser refuses to start with `previous` present, which is
    why `docs/operations/deploy.md` says to remove it before rolling back.
    """
    if "previous" not in raw:
        return None
    previous = raw["previous"]
    if not isinstance(previous, dict) or set(previous) != {"token_hash", "until"}:
        raise RuntimeError("invalid runtime authentication configuration")
    # A non-string field raises TypeError here, which `load_auth_config` refuses like the rest.
    return PreviousHash(
        token_hash=previous["token_hash"], until=datetime.fromisoformat(previous["until"])
    )


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


# Once, at import: uvicorn configures only its own loggers, so without this the root logger drops
# INFO and `alert_not_configured` and `notified` never reach the container log.
logging.basicConfig(level=log_level(os.environ.get("LOG_LEVEL")))
app = create_app(load_auth_config(), server_alert_config(get_settings()))
