from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from orchestrator.db import session_factory
from orchestrator.errors import DomainError
from orchestrator.identity.auth import (
    AuthenticationError,
    M2MCredential,
    authenticate_human,
    authenticate_m2m,
)
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import ActorRole
from orchestrator.services.lifecycle import ActorContext


@dataclass(frozen=True)
class AuthConfig:
    registry: RegistryAdapter
    m2m_credentials: Mapping[str, M2MCredential]
    trusted_proxy_ips: frozenset[str]
    proxy_marker_header: str
    proxy_marker: str
    email_header: str
    email_to_actor: Mapping[str, str]
    m2m_roles: Mapping[str, ActorRole] | None = None
    production_drill_credential_key_id: str | None = None
    runtime_observer_credential_key_id: str | None = None
    credential_key_header: str = "X-Credential-Key-Id"
    csrf_secret: bytes | None = None

    def __post_init__(self) -> None:
        if self.csrf_secret is not None and len(self.csrf_secret) < 32:
            raise ValueError("csrf_secret must contain at least 32 bytes")


class APIAuthenticationError(PermissionError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def get_session() -> Iterator[Session]:
    with session_factory() as session:
        yield session


def get_actor(request: Request) -> ActorContext:
    config = getattr(request.app.state, "auth_config", None)
    if not isinstance(config, AuthConfig):
        raise APIAuthenticationError("authentication_required")

    headers = _raw_headers(request)
    authorization = _header_values(headers, "authorization")
    key_ids = _header_values(headers, config.credential_key_header)
    forward_values = [
        *_header_values(headers, config.proxy_marker_header),
        *_header_values(headers, config.email_header),
    ]
    if (authorization or key_ids) and forward_values:
        raise APIAuthenticationError("authentication_failed")
    try:
        if authorization or key_ids:
            identity = _authenticate_machine(authorization, key_ids, config)
        elif forward_values:
            peer_ip = request.client.host if request.client is not None else ""
            identity = authenticate_human(
                headers=headers,
                peer_ip=peer_ip,
                trusted_proxy_ips=config.trusted_proxy_ips,
                proxy_marker_header=config.proxy_marker_header,
                proxy_marker=config.proxy_marker,
                email_header=config.email_header,
                email_to_actor=config.email_to_actor,
                registry=config.registry,
            )
        else:
            raise APIAuthenticationError("authentication_required")
    except AuthenticationError as error:
        raise APIAuthenticationError("authentication_failed") from error
    role = identity.role
    if identity.credential_key_id is not None and config.m2m_roles is not None:
        role = config.m2m_roles.get(identity.credential_key_id, role)
    return ActorContext(identity.actor_id, role, identity.credential_key_id)


def get_production_drill_actor(request: Request) -> ActorContext:
    """Authorize the dedicated runner credential for production-drill controls only."""
    actor = get_actor(request)
    config = getattr(request.app.state, "auth_config", None)
    if (
        not isinstance(config, AuthConfig)
        or config.production_drill_credential_key_id is None
        or actor.role is not ActorRole.SYSTEM
        or actor.credential_key_id != config.production_drill_credential_key_id
    ):
        raise DomainError(
            "role_forbidden",
            "only the configured production drill system credential may run drill controls",
            None,
        )
    return actor


def get_runtime_observer_actor(request: Request) -> ActorContext:
    """Authorize the credential that can attest the running deployment."""
    actor = get_actor(request)
    config = getattr(request.app.state, "auth_config", None)
    if (
        not isinstance(config, AuthConfig)
        or config.runtime_observer_credential_key_id is None
        or actor.role is not ActorRole.SYSTEM
        or actor.credential_key_id != config.runtime_observer_credential_key_id
    ):
        raise DomainError(
            "role_forbidden",
            "only the configured runtime observer credential may record runtime observations",
            None,
        )
    return actor


def _authenticate_machine(
    authorization: list[str],
    key_ids: list[str],
    config: AuthConfig,
):
    if len(authorization) != 1 or len(key_ids) != 1:
        raise AuthenticationError("machine authentication requires exactly one header")
    scheme, separator, token = authorization[0].partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token:
        raise AuthenticationError("invalid bearer credential")
    return authenticate_m2m(
        bearer_token=token,
        credential_key_id=key_ids[0],
        credentials=config.m2m_credentials,
        registry=config.registry,
    )


def _raw_headers(request: Request) -> list[tuple[str, str]]:
    return [
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in request.scope["headers"]
    ]


def _header_values(headers: list[tuple[str, str]], target: str) -> list[str]:
    normalized = target.lower()
    return [value for name, value in headers if name.lower() == normalized]
