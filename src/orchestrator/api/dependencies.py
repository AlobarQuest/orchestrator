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

# WS-P3.6 / ADR-0017: OBSERVER's entire write surface, stated ONCE and POSITIVELY.
#
# Every other role is confined by roughly twenty service-level allowlists that happen to agree
# with each other. That is not a property the service layer actually provides: four POST routes
# carry no role check at all today (preflight, and the three event-publication routes), so a role
# confined only by service guards would reach them. An observe-and-report identity must not be
# confined by twenty places all remembering it.
#
# So OBSERVER is confined here, at the one dependency through which BOTH routers obtain their
# actor. It may POST to the routes named below and to nothing else, and a route added tomorrow is
# refused without its author needing to know this rule exists. That default is the point: this
# role must never gain a surface silently.
#
# Reads are deliberately NOT confined. Every machine role already reads any unit's evidence pack,
# runner brief and ledger; making OBSERVER the sole exception would be a second, unrelated policy
# change riding a security change. The claim this role makes is about what it can CHANGE.
OBSERVER_WRITE_ROUTES = frozenset({"/api/v1/observations"})
# not-a-vocabulary: the safe-method names from RFC 9110. Their source of truth is the HTTP
# standard, not another subsystem in this estate -- nothing here defines, serves or consumes a
# second copy of them, and Starlette hands us `request.method` already normalized to these
# spellings. There is nothing this set could drift out of agreement WITH.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
    if role is ActorRole.OBSERVER:
        _confine_observer(request)
    return ActorContext(identity.actor_id, role)


def _confine_observer(request: Request) -> None:
    """Refuse an observer any write outside `OBSERVER_WRITE_ROUTES`.

    Keyed on the matched route TEMPLATE, not on the concrete URL, so the rule reads the same as
    the route inventory it is checked against. An unmatched route yields None, which is not in the
    allowlist -- the unknown case refuses.
    """
    if request.method in _READ_METHODS:
        return
    matched = getattr(request.scope.get("route"), "path", None)
    if matched not in OBSERVER_WRITE_ROUTES:
        raise DomainError(
            "role_forbidden",
            "an observer may only record observations",
            None,
        )


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
