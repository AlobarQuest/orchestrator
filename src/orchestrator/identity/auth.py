import hashlib
import re
import secrets
from collections.abc import Mapping, Set
from dataclasses import dataclass

from orchestrator.identity.registry import RegistryAdapter, RegistryValidationError
from orchestrator.kernel.states import ActorRole

TOKEN_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class AuthenticationError(PermissionError):
    """Authentication failed without disclosing credential details."""


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    role: ActorRole
    authority_profile: str
    credential_key_id: str | None
    registry_version: int


@dataclass(frozen=True)
class M2MCredential:
    agent_id: str
    token_hash: str


def authenticate_m2m(
    *,
    bearer_token: str,
    credential_key_id: str,
    credentials: Mapping[str, M2MCredential],
    registry: RegistryAdapter,
) -> ActorContext:
    _validate_m2m_credentials(credentials)
    credential = credentials.get(credential_key_id)
    if credential is None:
        raise AuthenticationError("invalid machine credential")
    presented_hash = hashlib.sha256(bearer_token.encode()).hexdigest()
    if not bearer_token or not secrets.compare_digest(presented_hash, credential.token_hash):
        raise AuthenticationError("invalid machine credential")
    try:
        actor = registry.resolve(credential.agent_id)
    except RegistryValidationError as error:
        raise AuthenticationError("invalid machine identity") from error
    if actor.runtime == "human" or actor.authority_profile == "human-operator-v1":
        raise AuthenticationError("human identities cannot use machine credentials")
    return ActorContext(
        actor_id=actor.agent_id,
        role=ActorRole.WORKER,
        authority_profile=actor.authority_profile,
        credential_key_id=credential_key_id,
        registry_version=actor.version,
    )


def authenticate_human(
    *,
    headers: Mapping[str, str],
    peer_ip: str,
    trusted_proxy_ips: Set[str],
    proxy_marker_header: str,
    proxy_marker: str,
    email_header: str,
    email_to_actor: Mapping[str, str],
    registry: RegistryAdapter,
) -> ActorContext:
    normalized_headers = {name.lower(): value for name, value in headers.items()}
    has_forward_auth = (
        proxy_marker_header.lower() in normalized_headers
        or email_header.lower() in normalized_headers
    )
    if peer_ip not in trusted_proxy_ips:
        message = (
            "forward-auth headers received from untrusted peer"
            if has_forward_auth
            else "human authentication requires trusted proxy"
        )
        raise AuthenticationError(message)
    marker = normalized_headers.get(proxy_marker_header.lower(), "")
    if not proxy_marker or not secrets.compare_digest(marker, proxy_marker):
        raise AuthenticationError("invalid trusted proxy marker")
    email = normalized_headers.get(email_header.lower(), "").strip().lower()
    actor_id = email_to_actor.get(email)
    if actor_id is None:
        raise AuthenticationError("unknown human identity")
    try:
        actor = registry.resolve(actor_id)
    except RegistryValidationError as error:
        raise AuthenticationError("invalid human identity") from error
    if actor.runtime != "human" or actor.authority_profile != "human-operator-v1":
        raise AuthenticationError("registry identity is not a human operator")
    return ActorContext(
        actor_id=actor.agent_id,
        role=ActorRole.HUMAN,
        authority_profile=actor.authority_profile,
        credential_key_id=None,
        registry_version=actor.version,
    )


def _validate_m2m_credentials(credentials: Mapping[str, M2MCredential]) -> None:
    agent_ids: set[str] = set()
    for key_id, credential in credentials.items():
        if (
            not key_id
            or not isinstance(credential, M2MCredential)
            or not credential.agent_id
            or TOKEN_HASH_PATTERN.fullmatch(credential.token_hash) is None
        ):
            raise AuthenticationError("invalid machine credential configuration")
        if credential.agent_id in agent_ids:
            raise AuthenticationError("machine credentials must map one-to-one")
        agent_ids.add(credential.agent_id)
